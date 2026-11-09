# Contributing

Issues and pull requests are welcome. Open an issue first for anything large, so we can agree the approach before you write it.

## Set up

You need [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just), and `gpg` and `gpgv`. No containers: the role runs on the control node, and so do its tests. Then:

```bash
just deps      # pinned Python tools
just lint      # yamllint, ansible-lint (production profile), zizmor
just unit      # pytest for the rpm_repo_query module
just test      # Molecule, downloading small packages from packages.microsoft.com
```

`just` on its own lists every recipe.

## Rules

- Every variable is in `defaults/main.yml`, `meta/argument_specs.yml` and the README variables table, with the same default and description in all three.
- Internal variables start with `__rpm_repo_download_` and live in `vars/main.yml` or are registered in tasks.
- Use fully qualified module names, name every task, and keep `ansible-lint` on the production profile clean. A `noqa` needs a comment saying why.
- Tasks must be idempotent: `just test` runs an idempotence check.
- The module in `library/` uses the Python standard library and Ansible's `module_utils` only. Every helper it adds has a unit test, and changes to version ordering add cases taken from rpm itself (`rpm.labelCompare`), not worked out by hand.
- Never commit downloaded packages, internal mirror URLs or host names. The Molecule scenario downloads small public packages from packages.microsoft.com.
- Docs and comments are in British English.

## Commits and releases

Write commit subjects in the imperative, wrapped at 72 characters. Add an entry under Unreleased in `CHANGELOG.md`.

Maintainers release with `just release 1.2.3`, which tags and pushes. The Release workflow then creates the GitHub release and imports the tag into Galaxy.
