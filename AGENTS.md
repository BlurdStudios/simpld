# AGENTS.md — working agreement for this repo

## Versioning (SemVer — when to bump what)

`factory/version.py:VERSION` is the single source of truth. The About dialog,
the Windows installer (`build/windows/simpld.spec` + `.iss`), the updater,
and the release tag all read from it — never hardcode a version elsewhere.

### MAJOR — X.0.0
- **When:** incompatible API changes (breaking changes).
- **What it means:** code written for an older major may stop working or need updates.
- **Rule:** reset minor and patch to 0 (e.g. `1.5.4` → `2.0.0`).

### MINOR — 0.Y.0
- **When:** new functionality added in a backward-compatible manner.
- **What it means:** new features land, existing code keeps working untouched.
- **Rule:** reset patch to 0 (e.g. `1.4.9` → `1.5.0`).

### PATCH — 0.0.Z
- **When:** backward-compatible bug fixes or security patches.
- **What it means:** small fixes only — no new features, nothing breaks.
- **Rule:** just counts up, other numbers untouched (e.g. `1.4.5` → `1.4.6`).

### Release procedure
1. Bump `VERSION` in the commit you tag.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` — the tag MUST match `VERSION`.
3. The CI/release workflow refuses tags that disagree with `VERSION`.

Current line: `1.x` — factory-floor app, offline-first. Bug fixes → PATCH,
new station features → MINOR, breaking protocol/config changes → MAJOR.
