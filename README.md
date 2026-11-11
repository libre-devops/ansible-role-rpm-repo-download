<div align="center">
  <a href="https://libredevops.org">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://libredevops.org/assets/libre-devops-white.png">
      <img alt="Libre DevOps" src="https://libredevops.org/assets/libre-devops-black.png" width="300">
    </picture>
  </a>
</div>

# Ansible Role: RPM repository download

Downloads RPMs from a yum or dnf repository, packages.microsoft.com by default. It chooses the version from the repository's own metadata, and proves every file with the repository's signature and checksums.

[![CI](https://github.com/libre-devops/ansible-role-rpm-repo-download/actions/workflows/ci.yml/badge.svg)](https://github.com/libre-devops/ansible-role-rpm-repo-download/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/libre-devops/ansible-role-rpm-repo-download?sort=semver&label=release)](https://github.com/libre-devops/ansible-role-rpm-repo-download/releases/latest)
[![Galaxy](https://img.shields.io/badge/galaxy-libre__devops.rpm__repo__download-EE0000?logo=ansible&logoColor=white)](https://galaxy.ansible.com/ui/standalone/roles/libre_devops/rpm_repo_download/)
[![Licence](https://img.shields.io/github/license/libre-devops/ansible-role-rpm-repo-download)](./LICENSE)

---

> **Status:** tested in CI against the real packages.microsoft.com repositories, with unit tests for the version, metadata and signature handling.

## What it does

For each repository and package you name, the role:

1. Downloads `repodata/repomd.xml` and its detached signature `repomd.xml.asc`, and checks the signature with `gpgv` against the public keys you give, and only those.
2. Downloads the primary metadata that `repomd.xml` points to, and checks it against the checksum in `repomd.xml`.
3. Picks the package version: the newest by RPM's own ordering (epoch, version, release, tilde and caret included), or the exact version you ask for.
4. Downloads the file with `ansible.builtin.get_url`, using the checksum from the primary metadata, into `<dest>/<label>/`.
5. Sets the `rpm_repo_download_results` fact and, if asked, writes a JSON manifest.

Each step vouches for the next: the key signs `repomd.xml`, `repomd.xml` carries the checksum of the primary metadata, and the primary metadata carries the checksum of every package. So a downloaded file is the one the repository owner published, even through a proxy or a mirror. A file already downloaded with the right checksum is left alone, so a second run changes nothing.

The usual use is a monthly job on the control node that fetches a new release of an agent (Microsoft Defender for Endpoint's `mdatp`, the Azure Connected Machine agent's `azcmagent`, and so on) for each RHEL release, before uploading it to an internal repository such as Red Hat Satellite.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Role variables](#role-variables)
- [Choosing versions](#choosing-versions)
- [Keys and other repositories](#keys-and-other-repositories)
- [Results and the manifest](#results-and-the-manifest)
- [Check mode](#check-mode)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Developing](#developing)
- [Releasing](#releasing)
- [Sources](#sources)

## Requirements

| What | Detail |
|---|---|
| Control node | `ansible-core` 2.16 or later. No collections. |
| Where it runs | Any Linux host, normally the control node itself (`hosts: localhost`). The host needs Python, and `gpg` and `gpgv` when signatures are checked (both come with GnuPG 2 on every common distribution). |
| Network | HTTPS to the repositories. A proxy set in `https_proxy` is used. |
| Disk | Room for the packages. `mdatp` is about 160 MB per RHEL release. |

The role's module, `rpm_repo_query` in `library/`, uses the Python standard library and Ansible's own `module_utils`, so there is nothing to `pip install`.

## Installation

From Galaxy:

```bash
ansible-galaxy role install libre_devops.rpm_repo_download
```

Or pin a release from GitHub, as in [`examples/requirements.yml`](./examples/requirements.yml):

```yaml
roles:
  - name: libre_devops.rpm_repo_download
    src: https://github.com/libre-devops/ansible-role-rpm-repo-download.git
    scm: git
    version: 1.0.0
```

Without Galaxy, or to keep a copy inside your organisation, see [Using the role without Galaxy](./docs/using-without-galaxy.md). It covers git, release archives, requirements files that point at your own servers, AWX and Automation Controller, and execution environments.

## Quick start

The newest `mdatp` for RHEL 7, 8 and 9, as in [`examples/playbook.yml`](./examples/playbook.yml):

```yaml
- name: Download the newest mdatp for each RHEL release
  hosts: localhost
  connection: local
  gather_facts: false
  roles:
    - role: libre_devops.rpm_repo_download
      vars:
        rpm_repo_download_repositories:
          - path: rhel/7/prod
            label: rhel7
          - path: rhel/8/prod
            label: rhel8
          - path: rhel/9/prod
            label: rhel9
        rpm_repo_download_packages: [mdatp]
        rpm_repo_download_dest: /srv/staging/mdatp
        rpm_repo_download_manifest: /srv/staging/mdatp/manifest.json
```

This gives `/srv/staging/mdatp/rhel7/mdatp-<version>-1.x86_64.rpm` and the same for `rhel8` and `rhel9`, plus the manifest. Run it with `--check` first to see what it would download.

## Role variables

Every variable is validated against [`meta/argument_specs.yml`](./meta/argument_specs.yml) when the role starts. Defaults are in [`defaults/main.yml`](./defaults/main.yml).

| Variable | Default | Description |
|---|---|---|
| `rpm_repo_download_base_url` | `https://packages.microsoft.com` | Base URL the repository paths are relative to. |
| `rpm_repo_download_repositories` | `[{path: rhel/9/prod}]` | The repositories to download from. Each has a `path`, and optionally a `label` (the download subdirectory) and a `base_url` of its own. The label defaults to the path with slashes as hyphens (`rhel-9-prod`). Labels must differ. |
| `rpm_repo_download_packages` | `[mdatp]` | Package names to download from every repository. Each must exist in every repository. |
| `rpm_repo_download_version` | `latest` | `latest`, an exact version, `version-release` or `epoch:version-release`, or a dictionary of package name to one of those. See [Choosing versions](#choosing-versions). |
| `rpm_repo_download_arches` | `[x86_64]` | Architectures to accept. Add `noarch` for packages built without one. One file per package and architecture found. |
| `rpm_repo_download_dest` | `{{ playbook_dir }}/downloads` | Directory for the downloads, on the host the role runs on. Each repository gets a subdirectory named after its label. |
| `rpm_repo_download_gpg_keys` | Microsoft's two keys | Keys trusted to sign the metadata: a list of `file` (read on the control node, from this role's `files/` first) and `fingerprint`. See [Keys and other repositories](#keys-and-other-repositories). |
| `rpm_repo_download_verify_signature` | `true` | Check the signature on `repomd.xml`. Only turn it off for repositories that are not signed. |
| `rpm_repo_download_validate_certs` | `true` | Check TLS certificates. |
| `rpm_repo_download_timeout` | `600` | Seconds to wait for each metadata file and each package. |
| `rpm_repo_download_manifest` | `""` | Path for a JSON manifest of the repositories read and the packages downloaded. Empty writes none. |

### Facts set by the role

| Fact | Description |
|---|---|
| `rpm_repo_download_results` | One entry per repository, package and architecture. See [Results and the manifest](#results-and-the-manifest). |

## Choosing versions

`rpm_repo_download_version` is one of:

| Value | Picks |
|---|---|
| `latest` | The newest version, by RPM's ordering. |
| `101.25092.0005` | That version, with its newest release. |
| `101.25092.0005-1` | That version and release. |
| `1:2.4.0-3.el9` | That epoch, version and release. |
| `{mdatp: 101.25092.0005, azcmagent: latest}` | Per package. Packages left out get `latest`. |

RPM's ordering is not the same as sorting text: `101.26101.0000` is newer than `101.98.89`, because each dotted part is compared as a number. The module follows `rpmvercmp` exactly, and its unit tests check it against `rpm.labelCompare` from rpm itself.

When the version asked for is not in a repository, the role stops and lists the five newest versions that are there.

The same version applies to every repository. To take different versions from different repositories, call the role once per repository.

## Keys and other repositories

`rpm_repo_download_gpg_keys` lists the keys allowed to sign the metadata:

```yaml
rpm_repo_download_gpg_keys:
  - file: microsoft.asc
    fingerprint: BC528686B50D79E339D3721CEB3E94ADBE1229CF
  - file: microsoft-2025.asc
    fingerprint: AA86F75E427A19DD33346403EE4D7792F748182B
```

These are the defaults, shipped in [`files/`](./files). Microsoft signs older repositories (RHEL 7, 8 and 9, SLES 15) with `microsoft.asc` and newer ones (RHEL 10, SLES 16, Debian 13, Ubuntu 26.04) with `microsoft-2025.asc`, so keep both.

The role only accepts a signature from one of these fingerprints, and stops if a key file does not contain the fingerprint given for it. Signatures are checked with `gpgv` against a keyring of just these keys, in a temporary directory that is deleted afterwards, so no key you trust elsewhere on the machine plays a part, and no `gpg-agent` is left running.

For another vendor, give its base URL, repository paths and key. Check the fingerprint against what the vendor publishes, from a source other than the key file itself:

```yaml
rpm_repo_download_base_url: https://repo.example.com/linux
rpm_repo_download_repositories:
  - path: el/9/x86_64/stable
    label: el9
rpm_repo_download_packages: [example-agent]
rpm_repo_download_gpg_keys:
  - file: "{{ playbook_dir }}/keys/example-vendor.asc"
    fingerprint: 0123456789ABCDEF0123456789ABCDEF01234567
```

## Results and the manifest

Each entry in `rpm_repo_download_results`, and in the manifest's `packages`, has:

| Key | Example |
|---|---|
| `label` | `rhel9` |
| `repository` | `rhel/9/prod` |
| `name`, `epoch`, `version`, `release`, `arch` | `mdatp`, `0`, `101.26101.0000`, `1`, `x86_64` |
| `evr` | `101.26101.0000-1` (with `epoch:` in front when the epoch is not 0) |
| `nevra` | `mdatp-101.26101.0000-1.x86_64` |
| `url` | `https://packages.microsoft.com/rhel/9/prod/Packages/m/mdatp-101.26101.0000-1.x86_64.rpm` |
| `href`, `filename` | The path in the repository, and the file name. |
| `checksum_type`, `checksum`, `size` | `sha256`, the checksum from the signed metadata, the size in bytes. |
| `path` | Where the file was downloaded. |

The manifest also has `repositories`: for each one, its `url`, the metadata `revision`, the fingerprint it was `signed_by`, and the `primary_checksum`. A later step, such as an upload to Satellite or a change record, can read the manifest instead of working the versions out again.

## Check mode

With `--check`, the role reads and verifies the metadata, picks the versions, and compares any files already in place with the signed checksums. Then it says what a real run would download, and reports changed when there is something to download. It creates no directories, downloads no packages and writes no manifest.

```bash
ansible-playbook download.yml --check
```

## Examples

### One exact version for every RHEL release

```yaml
rpm_repo_download_version: 101.25092.0005
```

### Two packages, one pinned

```yaml
rpm_repo_download_packages: [mdatp, azcmagent]
rpm_repo_download_version:
  mdatp: 101.25092.0005
```

`azcmagent` gets `latest`.

### Arm64 as well

```yaml
rpm_repo_download_arches: [x86_64, aarch64]
```

### On a staging host rather than the control node

```yaml
- name: Download onto the staging server
  hosts: staging
  become: true
  roles:
    - role: libre_devops.rpm_repo_download
      vars:
        rpm_repo_download_dest: /srv/staging/packages
```

The key files are still read on the control node and passed to the module, so nothing needs copying first. The staging host needs `gpg`, `gpgv` and HTTPS access to the repository.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `the signature on repomd.xml is not valid for the given keys: gpgv: Can't check signature: No public key` | The repository is signed with a key you did not give. For packages.microsoft.com, keep both Microsoft keys: RHEL 10 and other newer releases use the 2025 key. |
| `the key given for fingerprint ... does not have that fingerprint` | The key file and the fingerprint do not match. Check both against the vendor's published fingerprint. |
| `has no detached signature (repomd.xml.asc)` | The repository is not signed. Use a signed one, or set `rpm_repo_download_verify_signature: false` knowing the checksums are then unproven. |
| `primary metadata checksum mismatch` | The repository was being updated while it was read, or a proxy served a stale file. Run again. If it persists, the metadata has been tampered with or corrupted. |
| `no package named ... for x86_64` | The name is wrong, or the package is `noarch`: add `noarch` to `rpm_repo_download_arches`. |
| `... has no version ... Newest available: ...` | The version is not in that repository. The message lists the newest five. |
| `Failed to find required executable "gpgv"` | Install GnuPG 2 on the host the role runs on (`gnupg2` on RHEL, `gpg` and `gpgv` on Debian and Ubuntu). |
| `... is compressed with zstd` | The repository uses zstd metadata. Run the role with Python 3.14 or later, or install the `zstandard` package for the Python the module runs with. |
| A download times out | Raise `rpm_repo_download_timeout`, or check the proxy. |

## Developing

You need [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just), and `gpg` and `gpgv`. There are no containers: the role runs on the control node, and so do its tests. CI runs the same recipes.

| Recipe | What it does |
|---|---|
| `just deps` | Installs the pinned Python tools (`uv.lock`). |
| `just lint` | `yamllint`, `ansible-lint` on the production profile, and `zizmor` on the workflows. |
| `just unit` | `pytest` for the module: version ordering checked against rpm, version selection, `repomd.xml` and primary parsing in every compression, checksum mismatches, and signatures from throwaway keys (a given key, the second of two, a key not given, a tampered file, a wrong fingerprint). |
| `just test` | The Molecule scenario on this machine. |
| `just live [args]` | Runs [`examples/playbook.yml`](./examples/playbook.yml): the newest `mdatp` for RHEL 7, 8 and 9 into `examples/downloads`. Add `--check` to download nothing. |
| `just check-keys` | Checks the bundled keys match the fingerprints in `defaults/main.yml`. |
| `just secrets` | Scans the git history with gitleaks. |

The Molecule scenario downloads small packages from packages.microsoft.com: `aadsshlogin-selinux` and the `noarch` `packages-microsoft-prod` from RHEL 9 (signed with the original key) and RHEL 10 (signed with the 2025 key), and an exact older `dotnet-runtime-deps-6.0` with the label worked out from the path. It runs check mode first, then the role twice to prove the second run changes nothing, then checks every file against the signed checksum and each repository against the key that signed it.

## Releasing

1. Move the Unreleased entries in [`CHANGELOG.md`](./CHANGELOG.md) under the new version.
2. Run `just release 1.2.3`. It tags the commit and pushes the tag.
3. The Release workflow creates the GitHub release and imports the tag into Galaxy, which takes the version from the tag. It needs the `GALAXY_API_KEY` secret, which the libre-devops organisation provides. If the import fails, fix the cause and run the Release workflow by hand with the tag to import it again.

## Sources

| Topic | Source |
|---|---|
| packages.microsoft.com and its signing keys | [Microsoft Learn: Linux package repository for Microsoft software](https://learn.microsoft.com/en-us/linux/packages) |
| Repository metadata (`repomd.xml`, primary) | [createrepo_c](https://github.com/rpm-software-management/createrepo_c) |
| RPM version ordering | [`rpmvercmp.cc` in rpm](https://github.com/rpm-software-management/rpm/blob/master/rpmio/rpmvercmp.cc) |
| Verifying detached signatures with `gpgv` | [GnuPG manual: gpgv](https://www.gnupg.org/documentation/manuals/gnupg/gpgv.html) |

## Licence

[MIT](./LICENSE).
