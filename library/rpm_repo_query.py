#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: (c) 2026 Craig Thacker (Libre DevOps)
# MIT License (see LICENSE)
"""Pick package versions from yum repository metadata, with the metadata's integrity proven."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: rpm_repo_query
short_description: Pick package versions from yum or dnf repository metadata, verified
description:
  - Reads C(repodata/repomd.xml) from each repository and checks its detached signature
    (C(repomd.xml.asc)) with C(gpgv) against the given public keys only.
  - Downloads the primary metadata named in C(repomd.xml) and checks its checksum, so every
    package checksum it reports is covered by the repository signature.
  - Selects the newest version of each package (RPM version ordering, epoch included), or the
    exact version asked for, and reports where to download it and the checksum to expect.
  - Downloads nothing but metadata and changes nothing, so it is safe in check mode.
version_added: "1.0.0"
author:
  - Craig Thacker
options:
  base_url:
    description: Base URL the repository paths are relative to.
    type: str
    default: https://packages.microsoft.com
  repositories:
    description: The repositories to read.
    type: list
    elements: dict
    required: true
    suboptions:
      path:
        description: Path of the repository under O(base_url), such as C(rhel/9/prod).
        type: str
        required: true
      label:
        description: A short name for the repository, returned with each package. Defaults to the path with slashes replaced by hyphens.
        type: str
      base_url:
        description: A different base URL for this repository only.
        type: str
  packages:
    description: Package names to select. Each must exist in every repository.
    type: list
    elements: str
    required: true
  version:
    description:
      - C(latest), or an exact C(version), C(version-release) or C(epoch:version-release).
      - A dictionary of package name to version sets it per package. Packages missing from
        the dictionary get C(latest).
    type: raw
    default: latest
  arches:
    description: Architectures to accept. Add C(noarch) for packages built without one.
    type: list
    elements: str
    default: [x86_64]
  keys:
    description: Public keys trusted to sign C(repomd.xml).
    type: list
    elements: dict
    suboptions:
      content:
        description: The ASCII-armoured public key.
        type: str
        required: true
      fingerprint:
        description: The key's full fingerprint. The key content must have it, and a signature only counts when it is from this key.
        type: str
        required: true
  verify_signature:
    description: Check the signature on C(repomd.xml). Only turn this off for repositories that are not signed.
    type: bool
    default: true
  validate_certs:
    description: Check TLS certificates.
    type: bool
    default: true
  timeout:
    description: Seconds to wait for each metadata download.
    type: int
    default: 60
requirements:
  - C(gpg) and C(gpgv) on the host the module runs on, when O(verify_signature) is true.
notes:
  - Primary metadata compressed with gzip, xz, bzip2 or none is supported. zstd needs Python
    3.14 (C(compression.zstd)) or the C(zstandard) package.
"""

EXAMPLES = r"""
- name: Find the newest mdatp for RHEL 8 and 9
  rpm_repo_query:
    repositories:
      - path: rhel/8/prod
      - path: rhel/9/prod
    packages: [mdatp]
    keys:
      - content: "{{ lookup('ansible.builtin.file', 'microsoft.asc') }}"
        fingerprint: BC528686B50D79E339D3721CEB3E94ADBE1229CF
  register: found

- name: Find one exact version
  rpm_repo_query:
    repositories:
      - path: rhel/9/prod
    packages: [mdatp]
    version: 101.25092.0005
    keys: "{{ my_keys }}"
"""

RETURN = r"""
repositories:
  description: What was read from each repository.
  returned: always
  type: list
  elements: dict
  contains:
    label: {description: The repository label., type: str}
    url: {description: The repository URL., type: str}
    revision: {description: The revision in C(repomd.xml)., type: str}
    signed_by: {description: Fingerprint of the key that signed C(repomd.xml), or null when not checked., type: str}
    primary_checksum: {description: Checksum of the primary metadata, as C(type:value)., type: str}
packages:
  description: One entry per repository, package and architecture.
  returned: always
  type: list
  elements: dict
  contains:
    label: {description: The repository label., type: str}
    repository: {description: The repository path., type: str}
    name: {description: Package name., type: str}
    epoch: {description: Epoch, C(0) when the package has none., type: str}
    version: {description: Version., type: str}
    release: {description: Release., type: str}
    arch: {description: Architecture., type: str}
    evr: {description: C(version-release), with C(epoch:) in front when the epoch is not 0., type: str}
    nevra: {description: C(name-[epoch:]version-release.arch)., type: str}
    href: {description: Location of the package within the repository., type: str}
    url: {description: Full download URL., type: str}
    filename: {description: File name of the package., type: str}
    checksum_type: {description: Checksum algorithm, as written in the metadata., type: str}
    checksum: {description: Checksum of the package file., type: str}
    size: {description: Size of the package file in bytes., type: int}
"""

import bz2
import gzip
import hashlib
import lzma
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url

REPO_NS = "{http://linux.duke.edu/metadata/repo}"
COMMON_NS = "{http://linux.duke.edu/metadata/common}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
ASCII_DIGITS = "0123456789"
ASCII_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHECKSUM_ALGORITHMS = {"sha": "sha1", "sha1": "sha1", "sha224": "sha224", "sha256": "sha256",
                       "sha384": "sha384", "sha512": "sha512", "md5": "md5"}


class RepoError(Exception):
    """A repository could not be read, verified or matched."""


# RPM version ordering ---------------------------------------------------------------------


def rpmvercmp(first, second):
    """Compare two version or release strings the way rpm does. Returns -1, 0 or 1."""
    if first == second:
        return 0
    one, two = first, second
    i = j = 0
    while i < len(one) or j < len(two):
        while i < len(one) and one[i] not in ASCII_DIGITS + ASCII_ALPHA + "~^":
            i += 1
        while j < len(two) and two[j] not in ASCII_DIGITS + ASCII_ALPHA + "~^":
            j += 1

        # A tilde sorts before everything, even the end of the string.
        if (i < len(one) and one[i] == "~") or (j < len(two) and two[j] == "~"):
            if not (i < len(one) and one[i] == "~"):
                return 1
            if not (j < len(two) and two[j] == "~"):
                return -1
            i += 1
            j += 1
            continue

        # A caret sorts after the end of the string, before everything else.
        if (i < len(one) and one[i] == "^") or (j < len(two) and two[j] == "^"):
            if i >= len(one):
                return -1
            if j >= len(two):
                return 1
            if one[i] != "^":
                return 1
            if two[j] != "^":
                return -1
            i += 1
            j += 1
            continue

        if not (i < len(one) and j < len(two)):
            break

        start_one, start_two = i, j
        if one[i] in ASCII_DIGITS:
            while i < len(one) and one[i] in ASCII_DIGITS:
                i += 1
            while j < len(two) and two[j] in ASCII_DIGITS:
                j += 1
            numeric = True
        else:
            while i < len(one) and one[i] in ASCII_ALPHA:
                i += 1
            while j < len(two) and two[j] in ASCII_ALPHA:
                j += 1
            numeric = False

        segment_one, segment_two = one[start_one:i], two[start_two:j]
        if not segment_two:
            # Numeric segments are newer than alphabetic ones.
            return 1 if numeric else -1
        if numeric:
            segment_one = segment_one.lstrip("0")
            segment_two = segment_two.lstrip("0")
            if len(segment_one) != len(segment_two):
                return 1 if len(segment_one) > len(segment_two) else -1
        if segment_one != segment_two:
            return 1 if segment_one > segment_two else -1

    if i >= len(one) and j >= len(two):
        return 0
    return -1 if i >= len(one) else 1


def compare_evr(first, second):
    """Compare (epoch, version, release) tuples. Returns -1, 0 or 1."""
    epoch_one, epoch_two = int(first[0] or 0), int(second[0] or 0)
    if epoch_one != epoch_two:
        return 1 if epoch_one > epoch_two else -1
    result = rpmvercmp(first[1], second[1])
    if result:
        return result
    return rpmvercmp(first[2] or "", second[2] or "")


def parse_version_request(text):
    """Split a requested version into (epoch or None, version, release or None)."""
    text = str(text).strip()
    epoch = None
    if ":" in text:
        epoch, text = text.split(":", 1)
    release = None
    if "-" in text:
        text, release = text.rsplit("-", 1)
    if not text:
        raise RepoError("'%s' is not a version" % text)
    return epoch, text, release


# Metadata --------------------------------------------------------------------------------


def decompress(data, href):
    """Return the uncompressed bytes of a metadata file, by its magic bytes or name."""
    if data[:2] == b"\x1f\x8b" or href.endswith(".gz"):
        return gzip.decompress(data)
    if data[:6] == b"\xfd7zXZ\x00" or href.endswith(".xz"):
        return lzma.decompress(data)
    if data[:3] == b"BZh" or href.endswith(".bz2"):
        return bz2.decompress(data)
    if data[:4] == b"\x28\xb5\x2f\xfd" or href.endswith(".zst"):
        try:
            from compression import zstd  # Python 3.14 and later

            return zstd.decompress(data)
        except ImportError:
            pass
        try:
            import zstandard

            return zstandard.ZstdDecompressor().decompressobj().decompress(data)
        except ImportError:
            raise RepoError(
                "%s is compressed with zstd, which needs Python 3.14 or the zstandard package" % href
            )
    return data


def file_checksum(data, checksum_type):
    """Hex digest of data with the algorithm named in repository metadata."""
    algorithm = CHECKSUM_ALGORITHMS.get(checksum_type.lower())
    if algorithm is None:
        raise RepoError("unsupported checksum type '%s'" % checksum_type)
    return hashlib.new(algorithm, data).hexdigest()


def parse_repomd(content):
    """Read the revision and the primary metadata entry from repomd.xml."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise RepoError("repomd.xml is not valid XML: %s" % error)
    revision = root.findtext(REPO_NS + "revision") or ""
    for data in root.findall(REPO_NS + "data"):
        if data.get("type") != "primary":
            continue
        location = data.find(REPO_NS + "location")
        checksum = data.find(REPO_NS + "checksum")
        if location is None or checksum is None:
            raise RepoError("repomd.xml has a primary entry without a location or checksum")
        size = data.findtext(REPO_NS + "size")
        return {
            "revision": revision,
            "href": location.get("href"),
            "base": location.get(XML_BASE),
            "checksum_type": checksum.get("type"),
            "checksum": (checksum.text or "").strip(),
            "size": int(size) if size else None,
        }
    raise RepoError("repomd.xml has no primary metadata")


def parse_primary(content):
    """Read the packages from primary.xml."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise RepoError("primary metadata is not valid XML: %s" % error)
    packages = []
    for package in root.findall(COMMON_NS + "package"):
        if package.get("type") not in (None, "rpm"):
            continue
        version = package.find(COMMON_NS + "version")
        location = package.find(COMMON_NS + "location")
        checksum = package.find(COMMON_NS + "checksum")
        size = package.find(COMMON_NS + "size")
        if version is None or location is None or checksum is None:
            continue
        packages.append({
            "name": package.findtext(COMMON_NS + "name"),
            "arch": package.findtext(COMMON_NS + "arch"),
            "epoch": version.get("epoch") or "0",
            "version": version.get("ver"),
            "release": version.get("rel") or "",
            "href": location.get("href"),
            "base": location.get(XML_BASE),
            "checksum_type": checksum.get("type"),
            "checksum": (checksum.text or "").strip(),
            "size": int(size.get("package")) if size is not None and size.get("package") else None,
        })
    return packages


def evr_string(package):
    """version-release, with the epoch in front when it is not 0."""
    evr = "%s-%s" % (package["version"], package["release"])
    return evr if package["epoch"] in ("", "0", None) else "%s:%s" % (package["epoch"], evr)


def matches_request(package, request):
    """Whether a package matches a requested (epoch, version, release)."""
    epoch, version, release = request
    if epoch is not None and int(epoch) != int(package["epoch"] or 0):
        return False
    if package["version"] != version:
        return False
    return release is None or package["release"] == release


def version_for(name, version):
    """The version request for one package: a string, or an entry of a dictionary."""
    if isinstance(version, dict):
        return str(version.get(name, "latest"))
    return str(version)


def select_packages(packages, names, arches, version, where=""):
    """Pick one package per name and architecture: the newest, or the newest matching version."""
    selected = []
    for name in names:
        candidates = [p for p in packages if p["name"] == name and p["arch"] in arches]
        if not candidates:
            raise RepoError("no package named %s for %s in %s" % (name, ", ".join(arches), where))
        wanted = version_for(name, version)
        if wanted != "latest":
            request = parse_version_request(wanted)
            matching = [p for p in candidates if matches_request(p, request)]
            if not matching:
                newest = sorted(candidates, key=_evr_key, reverse=True)[:5]
                raise RepoError(
                    "%s has no version %s in %s. Newest available: %s"
                    % (name, wanted, where, ", ".join(evr_string(p) for p in newest))
                )
            candidates = matching
        for arch in sorted({p["arch"] for p in candidates}):
            in_arch = [p for p in candidates if p["arch"] == arch]
            selected.append(sorted(in_arch, key=_evr_key)[-1])
    return selected


class _EVR(object):
    """Sort key wrapper for RPM epoch, version and release ordering."""

    __slots__ = ("evr",)

    def __init__(self, evr):
        self.evr = evr

    def __lt__(self, other):
        return compare_evr(self.evr, other.evr) < 0

    def __eq__(self, other):
        return compare_evr(self.evr, other.evr) == 0


def _evr_key(package):
    return _EVR((package["epoch"], package["version"], package["release"]))


def join_url(first, *rest):
    """Join URL parts with exactly one slash between them, skipping empty parts."""
    url = str(first).rstrip("/")
    for part in rest:
        if part:
            url += "/" + str(part).strip("/")
    return url


# Signatures ------------------------------------------------------------------------------


def _normalise_fingerprint(fingerprint):
    return "".join(str(fingerprint).split()).upper()


def verify_signature(data, signature, keys, gpg, gpgv, run=subprocess.run):
    """Check a detached signature against the given keys only. Returns the signer's fingerprint.

    Each key's fingerprint is checked with gpg --show-keys, the keys are dearmoured into a
    keyring of their own, and gpgv verifies against that keyring alone, in a temporary
    GNUPGHOME, so nothing the user trusts elsewhere is involved and no agent is started.
    """
    if not keys:
        raise RepoError("signature checking is on, but no keys were given")
    home = tempfile.mkdtemp(prefix="rpm_repo_query.")
    env = dict(os.environ, GNUPGHOME=home, LC_ALL="C")
    try:
        os.chmod(home, 0o700)
        keyring = os.path.join(home, "trusted.gpg")
        allowed = set()
        with open(keyring, "wb") as keyring_file:
            for index, key in enumerate(keys):
                wanted = _normalise_fingerprint(key["fingerprint"])
                key_path = os.path.join(home, "key%d.asc" % index)
                with open(key_path, "w") as key_file:
                    key_file.write(key["content"])
                shown = run([gpg, "--batch", "--quiet", "--show-keys", "--with-colons", key_path],
                            capture_output=True, text=True, env=env, check=False)
                fingerprints = _primary_fingerprints(shown.stdout)
                if shown.returncode != 0 or wanted not in fingerprints:
                    raise RepoError(
                        "the key given for fingerprint %s does not have that fingerprint (found: %s)"
                        % (wanted, ", ".join(sorted(fingerprints)) or "none")
                    )
                dearmoured = run([gpg, "--batch", "--quiet", "--dearmor", "--output", "-", key_path],
                                 capture_output=True, env=env, check=False)
                if dearmoured.returncode != 0:
                    raise RepoError("could not read the key with fingerprint %s" % wanted)
                keyring_file.write(dearmoured.stdout)
                allowed.add(wanted)

        data_path = os.path.join(home, "repomd.xml")
        signature_path = os.path.join(home, "repomd.xml.asc")
        with open(data_path, "wb") as handle:
            handle.write(data)
        with open(signature_path, "wb") as handle:
            handle.write(signature)
        verified = run([gpgv, "--status-fd", "1", "--keyring", keyring, signature_path, data_path],
                       capture_output=True, text=True, env=env, check=False)
        signer = _valid_signer(verified.stdout)
        if verified.returncode != 0 or signer is None:
            detail = (verified.stderr or verified.stdout).strip().splitlines()
            raise RepoError("the signature on repomd.xml is not valid for the given keys: %s"
                            % (detail[-1] if detail else "no output from gpgv"))
        if signer not in allowed:
            raise RepoError("repomd.xml is signed by %s, which is not one of the given keys" % signer)
        return signer
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _primary_fingerprints(colons):
    """Primary key fingerprints from gpg --with-colons output."""
    fingerprints, expect_primary = set(), False
    for line in colons.splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            expect_primary = True
        elif fields[0] == "fpr" and expect_primary:
            fingerprints.add(fields[9].upper())
            expect_primary = False
    return fingerprints


def _valid_signer(status):
    """The primary key fingerprint from a VALIDSIG status line, or None."""
    for line in status.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == "[GNUPG:]" and fields[1] == "VALIDSIG":
            return (fields[-1] if len(fields) >= 12 else fields[2]).upper()
    return None


# Repositories ----------------------------------------------------------------------------


def query_repository(fetch, repository, base_url, names, arches, version, keys, verify, gpg=None, gpgv=None):
    """Read, verify and select from one repository. fetch(url) returns the body as bytes."""
    path = repository["path"].strip("/")
    label = repository.get("label") or path.replace("/", "-")
    repo_url = join_url(repository.get("base_url") or base_url, path)

    repomd = fetch(join_url(repo_url, "repodata/repomd.xml"))
    signer = None
    if verify:
        try:
            signature = fetch(join_url(repo_url, "repodata/repomd.xml.asc"))
        except RepoError as error:
            raise RepoError("%s has no detached signature (repomd.xml.asc): %s" % (repo_url, error))
        signer = verify_signature(repomd, signature, keys, gpg, gpgv)

    primary_entry = parse_repomd(repomd)
    primary_url = join_url(primary_entry["base"] or repo_url, primary_entry["href"])
    primary_raw = fetch(primary_url)
    actual = file_checksum(primary_raw, primary_entry["checksum_type"])
    if actual != primary_entry["checksum"]:
        raise RepoError("primary metadata checksum mismatch for %s: repomd.xml says %s, got %s"
                        % (primary_url, primary_entry["checksum"], actual))

    packages = parse_primary(decompress(primary_raw, primary_entry["href"]))
    selected = select_packages(packages, names, arches, version, where=repo_url)
    results = []
    for package in selected:
        evr = evr_string(package)
        results.append({
            "label": label,
            "repository": path,
            "name": package["name"],
            "epoch": package["epoch"],
            "version": package["version"],
            "release": package["release"],
            "arch": package["arch"],
            "evr": evr,
            "nevra": "%s-%s.%s" % (package["name"], evr, package["arch"]),
            "href": package["href"],
            "url": join_url(package["base"] or repo_url, package["href"]),
            "filename": package["href"].rsplit("/", 1)[-1],
            "checksum_type": package["checksum_type"],
            "checksum": package["checksum"],
            "size": package["size"],
        })
    summary = {
        "label": label,
        "url": repo_url,
        "revision": primary_entry["revision"],
        "signed_by": signer,
        "primary_checksum": "%s:%s" % (primary_entry["checksum_type"], primary_entry["checksum"]),
    }
    return summary, results


def main():
    module = AnsibleModule(
        argument_spec=dict(
            base_url=dict(type="str", default="https://packages.microsoft.com"),
            repositories=dict(type="list", elements="dict", required=True, options=dict(
                path=dict(type="str", required=True),
                label=dict(type="str"),
                base_url=dict(type="str"),
            )),
            packages=dict(type="list", elements="str", required=True),
            version=dict(type="raw", default="latest"),
            arches=dict(type="list", elements="str", default=["x86_64"]),
            keys=dict(type="list", elements="dict", default=[], no_log=False, options=dict(
                content=dict(type="str", required=True),
                fingerprint=dict(type="str", required=True),
            )),
            verify_signature=dict(type="bool", default=True),
            validate_certs=dict(type="bool", default=True),
            timeout=dict(type="int", default=60),
        ),
        supports_check_mode=True,
    )
    params = module.params
    gpg = gpgv = None
    if params["verify_signature"]:
        gpg = module.get_bin_path("gpg", required=True)
        gpgv = module.get_bin_path("gpgv", required=True)
    else:
        module.warn("verify_signature is false: the repository metadata, and so the package "
                    "checksums, are not proven to come from the repository owner.")

    def fetch(url):
        try:
            response = open_url(url, timeout=params["timeout"], validate_certs=params["validate_certs"],
                                http_agent="libre_devops.rpm_repo_download")
            return response.read()
        except Exception as error:  # urllib raises several unrelated types
            raise RepoError("could not download %s: %s" % (url, error))

    summaries, packages = [], []
    try:
        for repository in params["repositories"]:
            summary, results = query_repository(
                fetch, repository, params["base_url"], params["packages"], params["arches"],
                params["version"], params["keys"], params["verify_signature"], gpg, gpgv,
            )
            summaries.append(summary)
            packages.extend(results)
    except RepoError as error:
        module.fail_json(msg=str(error), repositories=summaries)

    module.exit_json(changed=False, repositories=summaries, packages=packages)


if __name__ == "__main__":
    main()
