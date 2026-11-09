# Security policy

## Reporting a vulnerability

Report vulnerabilities in this role privately, through [GitHub's private vulnerability reporting](https://github.com/libre-devops/ansible-role-rpm-repo-download/security/advisories/new). Do not open a public issue.

## What the role proves, and what it does not

- **Proven:** every downloaded file has the checksum listed in the repository's primary metadata, the primary metadata has the checksum listed in `repomd.xml`, and `repomd.xml` carries a valid signature from one of the keys you gave, by fingerprint. A key file that does not have its declared fingerprint stops the role. Signatures are checked with `gpgv` against a keyring made only of those keys, in a temporary directory, so keys you trust elsewhere play no part.
- **Not checked:** the signatures inside the RPM files themselves. `rpm -K`, or the package manager on the host that installs the package, does that. Satellite and dnf check them again when the package is published and installed.
- **Turning signature checks off** (`rpm_repo_download_verify_signature: false`) leaves only the checksums, which an attacker who can change the metadata can also change. Only do it for repositories that are not signed.
- **Keys:** the role ships Microsoft's public keys for packages.microsoft.com. `just check-keys` checks the bundled files against the fingerprints in `defaults/main.yml` in CI. The fingerprints are published on [Microsoft Learn: Linux package repository](https://learn.microsoft.com/en-us/linux/packages).
