# Bukvarix

Local Wordstat-like CLI over a Bukvarix keyword dump (Moscow, frequencies
only — no SERP data).

## Development

```bash
uv sync
uv run bukvarix --help
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
scripts/install_hooks.sh  # installs a pre-commit hook warning on stale --help docstrings
```

`pytest` fails below the coverage floor in `pyproject.toml`; `ruff` includes
the security (`S`) rules; CI additionally scans `uv.lock` for known
vulnerabilities (OSV-Scanner) — the same as `osv-scanner --lockfile=uv.lock`
locally.

## Release

```bash
uv run scripts/release.py patch   # or minor / major
```

Runs the whole gate (`uv run scripts/release.py --check` without releasing),
bumps the last `vX.Y.Z` tag (the version comes from the tag — there is no
`version =` in `pyproject.toml`), assembles `CHANGELOG.md` from the
`changelog.d/` fragments with towncrier, commits `Release vX.Y.Z` and tags it.
It prints `git push --follow-tags`; pushing is a separate, confirmed step.
Every user-visible change adds a fragment:
`uv run towncrier create +<slug>.<type>.md -c "..."`.

## Install

**On this machine, from a local checkout:**

```bash
uv tool install .
```

**On any other machine (any OS), once this repo is pushed to GitHub — no
PyPI publish needed:**

```bash
uv tool install "git+https://github.com/bolandeu/bukvarix.git"
```

Pin a release by appending `@v<version>` (a tag cut by `scripts/release.py`)
to the URL, or a branch/commit the same way. This is also how an AI agent
bootstraps this CLI on a fresh machine/sandbox where it isn't installed
yet — see `src/bukvarix/data/SKILL.md`.

**Private repo?** The command above is unchanged — `uv` runs git under the
hood, so it authenticates the same way `git clone` does. On a machine
where the install is refused with an auth error, do one of these once:

```bash
gh auth login          # interactive login also sets up git's credential helper
gh auth setup-git      # only needed if gh was logged in with --with-token / in CI
```

or use the SSH form instead of HTTPS:

```bash
uv tool install "git+ssh://git@github.com/bolandeu/bukvarix.git"
```

Installed under the old distribution name `bukvarix` (before 0.3)? Remove it
first, otherwise two tools claim the same `bukvarix` command:
`uv tool uninstall bukvarix`.

**Update** an installed copy to the latest commit:

```bash
uv tool upgrade rb-bukvarix
```

After install, the command `bukvarix` is available globally on macOS,
Linux and Windows alike — no shelling out, no hardcoded POSIX paths.

To pick up later architecture changes from the `cli-generator` template
itself, run `copier update` in this repo.

## The database

`bukvarix` queries a single DuckDB file (~41GB on the real Moscow dump).
It lives in the user data dir, never in the repo:
`platformdirs.user_data_dir("bukvarix")/bukvarix.duckdb` — on macOS
`~/Library/Application Support/bukvarix/bukvarix.duckdb`, on Linux
`~/.local/share/bukvarix/bukvarix.duckdb`. Path resolution, in order:

1. `BUKVARIX_DB_PATH` env var — explicit override for one process/session.
2. `bukvarix db set-path PATH` — persisted via the OS user config dir
   (survives across shells/processes without needing to be sourced from a
   shell profile, which a non-interactive process wouldn't see anyway).
3. The user-data default above — the normal case; leave 1 and 2 unset
   unless the database really lives elsewhere.

`search`/`freq` fail with a structured `DATABASE_NOT_READY` error if
nothing has been imported yet; `stats --json` always succeeds and reports
`keywords_rows: 0` in that case. Building the database from a raw Bukvarix
TSV export is documented in `docs/build.md`.

## Architecture

This project follows a fixed CLI architecture:

- `src/bukvarix/commands/` — argument parsing only, no business logic.
- `src/bukvarix/core/` — services (including database-path resolution),
  the DuckDB query/import/normalize layer (`core/db.py`), models, errors.
- `src/bukvarix/credentials/` — OS keychain (`keyring`) + env vars, kept
  for fleet-wide consistency; unused today (bukvarix has no external API).
- `src/bukvarix/output/` — human (`rich`) and `--json` rendering.

Run `bukvarix doctor` to check environment and database readiness, and
`bukvarix --help` for the full command surface. Every command supports
`--json` for machine/agent consumption (envelope: `{"ok": true, ...}` on
success, `{"ok": false, "error": {"code", "message", "action"}}` on
failure); human-mode errors go to stderr, so stdout is always safe to
pipe. `db import-keywords`/`db normalize`/`db reindex` mutate the database
file in place and require `--confirm` (or an interactive yes) — see
`AGENTS.md` for the backup discipline around them.

`search`/`freq`/`stats`/`db ...` work from any directory against one
machine-wide database — no `bukvarix init` needed. `init`/`status`/`config`
exist for parity with this CLI's sibling tools (a project can require a
minimum CLI version via `.bukvarix/config.toml`'s `[cli] min_version`) but
hold no data bukvarix's own commands read.

See `src/bukvarix/data/SKILL.md` for the agent-facing usage guide bundled
with this package — install it into an AI tool with `bukvarix skill
install`, or get it as plain text with `bukvarix --ai` (works even without
file access).
