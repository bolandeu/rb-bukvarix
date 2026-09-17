# Changelog

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/), versions follow
[SemVer](https://semver.org/). `scripts/release.py` moves the Unreleased
section under a new version heading — add entries here as you go, don't
write them at release time.

## [Unreleased]

### Changed

- Refactored onto the shared CLI architecture (cli-generator template
  v1.0.0): `src/` layout with thin `commands/` + `core/services.py`/
  `core/db.py`; structured `CliError`s and fixed exit codes via
  `output.guard`; dual human/`--json` output (JSON now wrapped in the
  standard `{"ok": ...}` envelope — **breaking change** for existing
  `--json` consumers, see README/AGENTS.md); `doctor`; `--dry-run`/
  `--confirm` gating on `db import-keywords`/`db normalize`/`db reindex`
  (previously unprotected — backup was manual-only); bundled
  `data/SKILL.md`; pytest + coverage floor + ruff `S` rules + CI
  (test matrix + dependency audit).
- Moved `import-keywords`/`normalize`/`reindex` under a `db` subcommand
  group (`bukvarix db import-keywords|normalize|reindex`), and added
  `bukvarix db set-path` to persist the database location without relying
  on a shell-profile-only `BUKVARIX_DB_PATH`.
- Database path resolution no longer hardcodes a personal absolute path in
  source: `BUKVARIX_DB_PATH` env var → `bukvarix db set-path` (persisted
  via platformdirs user config) → a platformdirs default location.

### Added

- Initial scaffold from the cli-generator template.
