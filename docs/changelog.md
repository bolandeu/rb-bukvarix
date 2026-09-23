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
- `--help` is now in Russian throughout (root description, global options,
  command groups, arguments, command docstrings). Typer/Click built-ins
  (`--install-completion`, `--show-completion`, `--help`) stay English.
- `doctor` now checks only what bukvarix actually uses — `python`,
  `cli_version`, `db_path`, `db_ready`. The project/config/cli_compat/
  credential_store/credential checks are gone: bukvarix has no project or
  credential concept in practice, and the permanent ✗ on them was
  misleading. `--json` consumers reading those keys will no longer find
  them.
- Moved project docs into `docs/`: `BUILD.md` → `docs/build.md`,
  `CHANGELOG.md` → `docs/changelog.md`, `EXAMPLES.md` → `docs/examples.md`.
  `scripts/release.py` now stamps `docs/changelog.md`; references in
  README, AGENTS.md and code comments updated.

### Added

- Initial scaffold from the cli-generator template.
- `docs/backlog.md` — planned tasks with self-contained instructions for
  the implementer (first entry: an inverted lemma index to speed up
  `search`).
- AGENTS.md: database backups go only to `/Volumes/Data/bukvarix/`, engine
  changes are made in a `feature/*` branch, and every search/build
  experiment (successful or rolled back) is recorded in `docs/build.md`.
