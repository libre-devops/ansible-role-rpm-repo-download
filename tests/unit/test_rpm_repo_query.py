"""Unit tests for library/rpm_repo_query.py.

The version comparison table was produced by rpm itself (rpm.labelCompare in a UBI 9
container), so these tests check the module agrees with rpm, not with our reading of it.
"""

import bz2
import gzip
import hashlib
import lzma
import os
import shutil
import subprocess
import tempfile

import pytest

RPM_ORDER = [
    ("1.0", "1.0", 0),
    ("1.0", "2.0", -1),
    ("2.0", "1.0", 1),
    ("2.0.1", "2.0.1", 0),
    ("2.0", "2.0.1", -1),
    ("2.0.1a", "2.0.1", 1),
    ("5.5p1", "5.5p2", -1),
    ("5.5p10", "5.5p1", 1),
    ("10xyz", "10.1xyz", -1),
    ("xyz10", "xyz10.1", -1),
    ("xyz.4", "8", -1),
    ("8", "xyz.4", 1),
    ("6.0.rc1", "6.0", 1),
    ("10b2", "10a1", 1),
    ("10a2", "10b2", -1),
    ("1.0a", "1.0aa", -1),
    ("10.0001", "10.1", 0),
    ("10.0001", "10.0039", -1),
    ("4.999.9", "5.0", -1),
    ("20101121", "20101122", -1),
    ("2.0", "2_0", 0),
    ("1.0~rc1", "1.0", -1),
    ("1.0", "1.0~rc1", 1),
    ("1.0~rc1", "1.0~rc2", -1),
    ("1.0~rc1~git123", "1.0~rc1", -1),
    ("1.0^", "1.0", 1),
    ("1.0", "1.0^", -1),
    ("1.0^git1", "1.0^git2", -1),
    ("1.0^git1", "1.01", -1),
    ("1.0^20160101", "1.0.1", -1),
    ("1.0^20160101^git1", "1.0^20160101", 1),
    ("1.0~rc1^git1", "1.0~rc1", 1),
    ("1.0^git1~pre", "1.0^git1", -1),
    ("101.98.89", "101.25092.0005", -1),
    ("101.26101.0000", "101.26091.0004", 1),
    ("1.2.3", "1.2.3.0", -1),
    ("a", "1", -1),
]


@pytest.mark.parametrize("first, second, expected", RPM_ORDER)
def test_rpmvercmp_matches_rpm(rrq, first, second, expected):
    assert rrq.rpmvercmp(first, second) == expected


def test_epoch_beats_version_and_release_breaks_ties(rrq):
    # From rpm.labelCompare: epoch 1 of 1.0 is newer than epoch 0 of 9.9, and release 10 is
    # newer than release 2.
    assert rrq.compare_evr(("1", "1.0", "1"), ("0", "9.9", "1")) == 1
    assert rrq.compare_evr(("0", "1.0", "2"), ("0", "1.0", "10")) == -1
    assert rrq.compare_evr(("", "1.0", "1"), ("0", "1.0", "1")) == 0


@pytest.mark.parametrize("text, expected", [
    ("101.25092.0005", (None, "101.25092.0005", None)),
    ("101.25092.0005-1", (None, "101.25092.0005", "1")),
    ("2:1.0-3.el9", ("2", "1.0", "3.el9")),
])
def test_parse_version_request(rrq, text, expected):
    assert rrq.parse_version_request(text) == expected


def _pkg(name, version, release="1", epoch="0", arch="x86_64"):
    return {"name": name, "version": version, "release": release, "epoch": epoch, "arch": arch,
            "href": "Packages/%s-%s-%s.%s.rpm" % (name, version, release, arch), "base": None,
            "checksum_type": "sha256", "checksum": "0" * 64, "size": 1}


PACKAGES = [
    _pkg("tool", "1.9"),
    _pkg("tool", "1.10"),
    _pkg("tool", "1.10", release="2"),
    _pkg("tool", "1.10", arch="aarch64"),
    _pkg("tool", "0.1", epoch="1"),
    _pkg("other", "3.0", arch="noarch"),
]


def test_latest_uses_epoch_then_version_then_release(rrq):
    selected = rrq.select_packages(PACKAGES, ["tool"], ["x86_64"], "latest")
    assert [(p["epoch"], p["version"], p["release"]) for p in selected] == [("1", "0.1", "1")]


def test_exact_version_takes_the_newest_release(rrq):
    selected = rrq.select_packages(PACKAGES, ["tool"], ["x86_64"], "1.10")
    assert [(p["version"], p["release"]) for p in selected] == [("1.10", "2")]


def test_exact_version_release(rrq):
    selected = rrq.select_packages(PACKAGES, ["tool"], ["x86_64"], "1.10-1")
    assert [(p["version"], p["release"]) for p in selected] == [("1.10", "1")]


def test_epoch_in_the_request(rrq):
    selected = rrq.select_packages(PACKAGES, ["tool"], ["x86_64"], "1:0.1-1")
    assert selected[0]["epoch"] == "1"
    with pytest.raises(rrq.RepoError):
        rrq.select_packages(PACKAGES, ["tool"], ["x86_64"], "0:0.1-1")


def test_version_per_package(rrq):
    selected = rrq.select_packages(PACKAGES, ["tool", "other"], ["x86_64", "noarch"], {"tool": "1.9"})
    assert [(p["name"], p["version"]) for p in selected] == [("tool", "1.9"), ("other", "3.0")]


def test_one_package_per_architecture(rrq):
    selected = rrq.select_packages(PACKAGES, ["tool"], ["x86_64", "aarch64"], "1.10-1")
    assert sorted(p["arch"] for p in selected) == ["aarch64", "x86_64"]


def test_noarch_needs_asking_for(rrq):
    with pytest.raises(rrq.RepoError, match="no package named other"):
        rrq.select_packages(PACKAGES, ["other"], ["x86_64"], "latest")


def test_missing_version_lists_the_newest(rrq):
    with pytest.raises(rrq.RepoError, match=r"tool has no version 9\.9 .*Newest available: 1:0\.1-1, 1\.10-2"):
        rrq.select_packages(PACKAGES, ["tool"], ["x86_64"], "9.9")


@pytest.mark.parametrize("first, rest, expected", [
    ("https://example.com/", ("rhel/9/prod/", "repodata/repomd.xml"), "https://example.com/rhel/9/prod/repodata/repomd.xml"),
    ("https://example.com", ("", "/a"), "https://example.com/a"),
])
def test_join_url(rrq, first, rest, expected):
    assert rrq.join_url(first, *rest) == expected


# A small repository built in the test --------------------------------------------------

PRIMARY = b"""<?xml version="1.0" encoding="UTF-8"?>
<metadata xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm" packages="3">
<package type="rpm">
  <name>tool</name><arch>x86_64</arch>
  <version epoch="0" ver="2.0" rel="1"/>
  <checksum type="sha256" pkgid="YES">aaaa</checksum>
  <size package="1234" installed="0" archive="0"/>
  <location href="Packages/t/tool-2.0-1.x86_64.rpm"/>
</package>
<package type="rpm">
  <name>tool</name><arch>x86_64</arch>
  <version epoch="0" ver="10.0" rel="1"/>
  <checksum type="sha256" pkgid="YES">bbbb</checksum>
  <size package="2345" installed="0" archive="0"/>
  <location href="Packages/t/tool-10.0-1.x86_64.rpm"/>
</package>
<package type="rpm">
  <name>mirrored</name><arch>noarch</arch>
  <version epoch="0" ver="1.0" rel="1"/>
  <checksum type="sha256" pkgid="YES">cccc</checksum>
  <size package="10" installed="0" archive="0"/>
  <location xml:base="https://mirror.example.com/pool/" href="m/mirrored-1.0-1.noarch.rpm"/>
</package>
</metadata>
"""

COMPRESSORS = {
    "primary.xml.gz": gzip.compress,
    "primary.xml.xz": lzma.compress,
    "primary.xml.bz2": bz2.compress,
    "primary.xml": lambda data: data,
}


def _repomd(primary_name, primary_bytes, checksum=None):
    checksum = checksum or hashlib.sha256(primary_bytes).hexdigest()
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<repomd xmlns="http://linux.duke.edu/metadata/repo">
  <revision>1700000000</revision>
  <data type="filelists"><location href="repodata/filelists.xml.gz"/><checksum type="sha256">ffff</checksum></data>
  <data type="primary">
    <checksum type="sha256">%s</checksum>
    <location href="repodata/%s"/>
    <size>%d</size>
  </data>
</repomd>
""" % (checksum, primary_name, len(primary_bytes))).encode()


def _fetcher(files):
    def fetch(url):
        if url not in files:
            raise KeyError(url)
        return files[url]
    return fetch


@pytest.mark.parametrize("primary_name", sorted(COMPRESSORS))
def test_query_repository_reads_every_compression(rrq, primary_name):
    primary = COMPRESSORS[primary_name](PRIMARY)
    base = "https://repo.example.com/el/9"
    files = {base + "/repodata/repomd.xml": _repomd(primary_name, primary),
             base + "/repodata/" + primary_name: primary}
    summary, results = rrq.query_repository(
        _fetcher(files), {"path": "el/9"}, "https://repo.example.com", ["tool", "mirrored"],
        ["x86_64", "noarch"], "latest", [], verify=False)
    assert summary["revision"] == "1700000000"
    assert summary["label"] == "el-9"
    assert summary["signed_by"] is None
    tool, mirrored = results
    assert tool["evr"] == "10.0-1"
    assert tool["url"] == base + "/Packages/t/tool-10.0-1.x86_64.rpm"
    assert tool["filename"] == "tool-10.0-1.x86_64.rpm"
    assert (tool["checksum_type"], tool["checksum"], tool["size"]) == ("sha256", "bbbb", 2345)
    assert mirrored["url"] == "https://mirror.example.com/pool/m/mirrored-1.0-1.noarch.rpm"


def test_primary_checksum_mismatch_stops(rrq):
    primary = gzip.compress(PRIMARY)
    base = "https://repo.example.com/el/9"
    files = {base + "/repodata/repomd.xml": _repomd("primary.xml.gz", primary, checksum="0" * 64),
             base + "/repodata/primary.xml.gz": primary}
    with pytest.raises(rrq.RepoError, match="checksum mismatch"):
        rrq.query_repository(_fetcher(files), {"path": "el/9"}, "https://repo.example.com",
                             ["tool"], ["x86_64"], "latest", [], verify=False)


def test_repomd_without_primary(rrq):
    with pytest.raises(rrq.RepoError, match="no primary"):
        rrq.parse_repomd(b'<repomd xmlns="http://linux.duke.edu/metadata/repo"><revision>1</revision></repomd>')


def test_zstd_without_support_says_so(rrq, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_zstd(name, *args, **kwargs):
        if name in ("compression", "zstandard"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_zstd)
    with pytest.raises(rrq.RepoError, match="zstd"):
        rrq.decompress(b"\x28\xb5\x2f\xfd" + b"\x00" * 8, "repodata/primary.xml.zst")


# Signatures, with throwaway keys made in the test ----------------------------------------

needs_gpg = pytest.mark.skipif(not (shutil.which("gpg") and shutil.which("gpgv")), reason="needs gpg and gpgv")


@pytest.fixture(scope="module")
def signing_keys():
    """Two throwaway signing keys: (home, [(fingerprint, armoured public key)])."""
    home = tempfile.mkdtemp(prefix="rrq-test-")
    os.chmod(home, 0o700)
    env = dict(os.environ, GNUPGHOME=home)
    keys = []
    for uid in ("Test Repo One <one@example.com>", "Test Repo Two <two@example.com>"):
        subprocess.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                        "--quick-gen-key", uid, "ed25519", "sign", "never"],
                       env=env, check=True, capture_output=True)
        listing = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys", uid],
                                 env=env, check=True, capture_output=True, text=True).stdout
        fingerprint = [line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")][0]
        armoured = subprocess.run(["gpg", "--batch", "--armor", "--export", fingerprint],
                                  env=env, check=True, capture_output=True, text=True).stdout
        keys.append((fingerprint, armoured))
    yield home, keys
    subprocess.run(["gpgconf", "--homedir", home, "--kill", "all"], env=env, check=False, capture_output=True)
    shutil.rmtree(home, ignore_errors=True)


def _sign(home, fingerprint, data):
    env = dict(os.environ, GNUPGHOME=home)
    result = subprocess.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                             "--local-user", fingerprint, "--armor", "--detach-sign", "--output", "-"],
                            input=data, env=env, check=True, capture_output=True)
    return result.stdout


@needs_gpg
def test_signature_from_a_given_key(rrq, signing_keys):
    home, keys = signing_keys
    fingerprint, armoured = keys[0]
    data = b"<repomd/>"
    signer = rrq.verify_signature(data, _sign(home, fingerprint, data),
                                  [{"content": armoured, "fingerprint": fingerprint}], "gpg", "gpgv")
    assert signer == fingerprint.upper()


@needs_gpg
def test_signature_from_the_second_of_two_keys(rrq, signing_keys):
    home, keys = signing_keys
    data = b"<repomd/>"
    given = [{"content": armoured, "fingerprint": fingerprint} for fingerprint, armoured in keys]
    assert rrq.verify_signature(data, _sign(home, keys[1][0], data), given, "gpg", "gpgv") == keys[1][0].upper()


@needs_gpg
def test_tampered_data_fails(rrq, signing_keys):
    home, keys = signing_keys
    fingerprint, armoured = keys[0]
    signature = _sign(home, fingerprint, b"<repomd/>")
    with pytest.raises(rrq.RepoError, match="not valid"):
        rrq.verify_signature(b"<repomd>changed</repomd>", signature,
                             [{"content": armoured, "fingerprint": fingerprint}], "gpg", "gpgv")


@needs_gpg
def test_signature_from_a_key_not_given_fails(rrq, signing_keys):
    home, keys = signing_keys
    data = b"<repomd/>"
    with pytest.raises(rrq.RepoError, match="not valid"):
        rrq.verify_signature(data, _sign(home, keys[1][0], data),
                             [{"content": keys[0][1], "fingerprint": keys[0][0]}], "gpg", "gpgv")


@needs_gpg
def test_key_content_must_have_the_declared_fingerprint(rrq, signing_keys):
    _, keys = signing_keys
    with pytest.raises(rrq.RepoError, match="does not have that fingerprint"):
        rrq.verify_signature(b"x", b"y", [{"content": keys[0][1], "fingerprint": keys[1][0]}], "gpg", "gpgv")


@needs_gpg
def test_no_keys_given(rrq):
    with pytest.raises(rrq.RepoError, match="no keys"):
        rrq.verify_signature(b"x", b"y", [], "gpg", "gpgv")


@needs_gpg
def test_signed_repository_end_to_end(rrq, signing_keys):
    home, keys = signing_keys
    fingerprint, armoured = keys[0]
    primary = gzip.compress(PRIMARY)
    repomd = _repomd("primary.xml.gz", primary)
    base = "https://repo.example.com/el/9"
    files = {base + "/repodata/repomd.xml": repomd,
             base + "/repodata/repomd.xml.asc": _sign(home, fingerprint, repomd),
             base + "/repodata/primary.xml.gz": primary}
    summary, results = rrq.query_repository(
        _fetcher(files), {"path": "el/9", "label": "el9"}, "https://repo.example.com", ["tool"], ["x86_64"],
        "2.0", [{"content": armoured, "fingerprint": fingerprint}], True, "gpg", "gpgv")
    assert summary["signed_by"] == fingerprint.upper()
    assert results[0]["label"] == "el9"
    assert results[0]["evr"] == "2.0-1"
