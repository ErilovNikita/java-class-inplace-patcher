# Maintainer notes

## Before publishing

- Run `python -m pytest -q`.
- Review `git status --short` after staging files.
- Do not commit real JARs, `.class` files, local patch configurations, backups, or private hashes.
- Keep `examples/patches.yaml` generic and free of organisation-specific package names.

## Behaviour and security boundaries

- The `.class` mode changes equal-length byte ranges only after structural method lookup and uniqueness checks.
- The JAR mode reads an input archive and writes a separate output archive; it does not edit compressed ZIP entries in place.
- Signed JARs are rejected. Changing a signed class invalidates its signature; a valid replacement requires signing with an authorized key.
- Test patches only on artifacts you are authorized to modify and retain the original file and generated backup.

## Release checklist

- Update the version in `pyproject.toml`.
- Run the test suite on a supported Python version and, when available, with `javac`.
- Verify the CLI with `classpatch --help` after installation through `pipx`.
