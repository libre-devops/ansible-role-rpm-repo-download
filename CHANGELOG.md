# Changelog

Notable changes to this role. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `docs/using-without-galaxy.md`, linked from the README's Installation section: installing the role without Galaxy (git, release archives, requirements files that name their own source), getting its collections with no Galaxy access, keeping a copy on your own git server or artifact store, AWX, Automation Controller and execution environments, and the ansible-core and Python versions it needs.

### Changed

- The Release workflow imports the release's own tag into Galaxy, rather than the default branch, and can be run by hand with a tag to import that tag again.

## 1.0.0 - 2026-11-11

### Added

- First release.
- The `rpm_repo_query` module: reads `repomd.xml`, checks its signature with `gpgv` against the given keys only, checks the primary metadata against the checksum in `repomd.xml`, and selects packages with RPM version ordering (epoch, tilde and caret included). gzip, xz, bzip2 and plain metadata, and zstd where Python supports it.
- Downloads with the checksum from the signed metadata, one directory per repository, the `rpm_repo_download_results` fact and an optional JSON manifest.
- `latest`, exact versions, `version-release` and `epoch:version-release`, globally or per package.
- Check mode that reads metadata only and lists what a real run would download.
- Microsoft's two signing keys bundled, checked by fingerprint in CI.
- Unit tests, with the version ordering checked against rpm itself, and a Molecule scenario that downloads small packages from packages.microsoft.com.
