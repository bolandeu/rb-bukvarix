# Changelog

All notable changes to this project are documented here. Versions follow
[SemVer](https://semver.org/) (while at `0.x`, a minor bump may break).

The changelog is written by `antagor publish`; don't edit it by hand.

## v0.6.2 — 2026-10-27

### Main

- Merge pull request #1 (Renovate)
- chore(template): Update dependency https://homeserver.tailee01ab.ts.net:3000/bolandeu/cli-generator.git to v1.27.3


## v0.6.1 — 2026-10-27

### Outside epics

- Deps: setuptools for GHSA-xmj3-9gf7-h52w (BUKVARIX-001)

## v0.6.0 — 2026-10-23

### Main

- daemon: enabled
- mode: fast
- antagor init: project bukvarix (BUKVARIX)
- Template v1.27.1: update from v1.2.9
- chore: copier source on Gitea
- docs: убрать упоминания корневого агента tools (AGENTS.md, подсказка release.py)


## [0.5.0] - 2026-10-18

### Added

- Веб-API Bukvarix: `--source api` у `search` и `freq` — частотность по всему миру (`freq_world_exact`, `freq_world_broad`) в том же конверте; новая команда `site` — ключевые слова домена и ядро конкурентов (2-10 доменов) с позициями по 25 регионам выдачи. Ключ — `BUKVARIX_API_KEY` (по умолчанию бесплатный `free`), в `doctor` — проверка `api_key`.


## [0.4.1] - 2026-10-03

### Added

- `db set-data-date YYYY-MM-DD` — записать дату данных в уже импортированную базу без переимпорта (для баз до 0.4.0, у которых `freq` отдаёт `data_date: null`).


## [0.4.0] - 2026-10-03

### Changed

- **Ломающее:** `bukvarix freq --json` теперь всегда отдаёт конверт `{count, region, source, data_date, results: [{query, phrase, freq_msk_exact, matched_via}]}` вместо `{query, result}` — и для одной фразы тоже; ненайденная фраза — `freq_msk_exact: 0`, `matched_via: null`. Новое: несколько фраз аргументами и `--input <файл|->`; `db import-keywords --data-date YYYY-MM-DD` сохраняет дату данных в базе (по умолчанию — дата изменения TSV), для базы, импортированной раньше, `data_date: null`. Python API (`core.db.freq`, `freq_batch`) не изменился.


## [0.3.2] - 2026-09-30

### Changed

- - База по умолчанию документирована в `user_data_dir("bukvarix")` (macOS `~/Library/Application Support/bukvarix/`, Linux `~/.local/share/bukvarix/`), а не в репозитории.


## [0.3.1] - 2026-09-28

### Changed

- Template v1.2.9: `doctor` reports declared external dependencies (none for now), the wheel carries `pyproject.toml` for the external-dependency registry, and an install still registered under the old distribution name `bukvarix` reports its real version.


## [0.3.0] - 2026-09-27

### Changed

- Release standard (template cli-generator v1.2.3): version from the git tag, changelog from changelog.d/ fragments, one scripts/release.py gate; every command's --help has an Example line and is pinned by snapshots. CHANGELOG moved from docs/changelog.md to the repo root.
- The distribution is renamed bukvarix → rb-bukvarix (the bukvarix command and the bukvarix import package stay). Installed copies: `uv tool uninstall bukvarix` first, then `uv tool install "rb-bukvarix @ git+https://github.com/bolandeu/bukvarix@vX.Y.Z"`.
- `auth status [--check] [--provider] --json` now returns the cross-tool credentials report (tool, version, checked_at, credentials[]); bukvarix stores no secrets, so the list is empty and the exit code is 0. The unused `auth login`/`auth logout` and the 'default' provider are removed, and so is the keyring dependency.

### Fixed

- The global `bukvarix` (`~/.local/bin`, used from any shell) was a non-editable snapshot of 0.1.0, not the editable install AGENTS.md described: after the 0.2.0 release it still ran the old 17.5s scan with no `engine` field. Reinstalled with `uv tool install --editable . --force`; AGENTS.md now says how to check it.


## [0.2.0] - 2026-09-23

### Changed

- `search` results match **whole lemmas**, not substrings, and ignore stop
  words unless forced with `+` (Wordstat semantics). Counts differ from the
  old scan: "растаможка авто" 1 550 instead of 1 824, "авто" 172 481 instead of
  1 272 430 (the scan also counted "автомобиль", "автосервис"...), "как
  похудеть" 23 510 instead of 10 095 ("как" is a stop word).
- `db import-keywords` and `db normalize` (when it recomputes
  `lemma_signature`) drop the lemma index; `search` falls back to the scan
  with a stderr warning until `db build-postings` is re-run.

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

- `search` got ~100-300x faster: an inverted lemma index
  (`lemma_postings`) answers in 0.08-0.37s instead of 16-35s on the full
  113.7M-phrase database (12 real queries, docs/build.md problem 6). Build
  it with the new `bukvarix db build-postings [--temp-dir DIR]` (4:26 on
  the full db, +1.45 GB to the file: 43.96 → 45.41 GB). Without it `search`
  keeps working through the old full scan.
- `search --json` has a new `engine` field (`postings` | `scan`); the human
  output shows it next to the timing.
- `search --mode scan` — the old full-scan engine, for comparison.
- Initial scaffold from the cli-generator template.
- `docs/backlog.md` — planned tasks with self-contained instructions for
  the implementer (first entry: an inverted lemma index to speed up
  `search`).
- AGENTS.md: database backups go only to `/Volumes/Data/bukvarix/`, engine
  changes are made in a `feature/*` branch, and every search/build
  experiment (successful or rolled back) is recorded in `docs/build.md`.

### Removed

- The unused FTS index left over from the rolled-back BM25 experiment
  (`fts_main_keywords` schema, 6 tables incl. `terms` with 463.6M rows) is
  dropped from the working database; a copy stays in the backup on
  `/Volumes/Data`. The drop alone did not shrink the file (DuckDB keeps
  freed blocks for reuse), so the database was rebuilt into a fresh file:
  45.41 → 41.32 GB, answers identical (docs/build.md, problem 6). The
  rebuilt db is backed up as
  `/Volumes/Data/bukvarix/bukvarix.duckdb.backup_v0.2.0`.

### Fixed

Every error hit along the way is listed here, including ones made while
doing the work, so they aren't repeated.

- `search` (default mode) matched query words as **substrings** of lemmas:
  `%авто%` also hit `автомобиль`/`автосервис`, inflating `results` and
  `total_count`. The lemma-postings path matches whole lemmas only; the old
  behaviour remains available as `--mode scan`.
- `docs/examples.md` showed `bukvarix db import-keywords ... --non-interactive`,
  which really fails with `No such option: --non-interactive` (exit 2):
  it is a global option and must come before the command
  (`bukvarix --non-interactive db ...`). Fixed there and noted in AGENTS.md
  and SKILL.md. The first real `db build-postings` run hit the same mistake
  and never started.
- `db build-postings`, first version (one `CREATE TABLE ... ORDER BY` over
  all ~484M postings), died after 3:03 with `OutOfMemoryException`
  (9.3 GiB / 9.3 GiB used), after spilling ~41 GB of temp files onto the
  system disk. The database was left intact (the failed transaction rolled
  back). Rewritten to build in ~20M-posting lemma ranges via a staging
  database in `--temp-dir` (see docs/build.md, problem 6).
- `db build-postings` chunked version: `ATTACH ? AS staging` failed with
  `Parser Error: syntax error at or near "?"` — DuckDB's `ATTACH`/`SET`
  take no bound parameters. Now the paths are quoted as SQL literals. Caught by
  the tests before any real run.
- A new test compared postings vs scan on "растаможка авто", which is
  itself a substring trap ("растаможки автомобиля"), so it failed for the
  wrong reason. Replaced with queries that have no substring overlaps;
  the trap is now its own test (`..._postings_is_whole_word`).
- First version of the lemma index required stop words as whole words:
  "авто в растаможка" found 812 phrases instead of the 1 824 the old scan
  gave (there '%в%' matched almost anything). Caught by the real-db
  acceptance run; stop words without `+` are now left out of the
  intersection.
- `lemma_postings_meta.built_at` was `TIMESTAMP WITH TIME ZONE`; fetching
  it from Python requires `pytz`, which isn't installed, so the acceptance
  script crashed. The CLI never reads that column. It is now a plain
  `TIMESTAMP` (takes effect on the next `build-postings`).
- Stale docs, found by checking them against the real CLI output: AGENTS.md,
  docs/examples.md and README described `doctor` checks that no longer exist
  (project/config/credentials); README called the data "keyword/SERP dumps"
  (SERP is out of scope); the "best path" section of docs/build.md used
  pre-`db` command names, `export BUKVARIX_DB_PATH`, an on-disk backup location
  and a cancelled `build-fts` step; docstrings in `core/services.py` and
  `credentials/manager.py` said `doctor` used them.
