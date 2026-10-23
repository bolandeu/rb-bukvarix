---
name: bukvarix
description: Conventions for driving the bukvarix CLI from an agent — install, JSON output, exit codes, the database it queries, destructive-command gating. Invoke when a task involves keyword/search-frequency lookups over the Bukvarix Moscow dump, world frequencies or a domain's/competitors' keywords via the Bukvarix web API.
---

# bukvarix

Local Wordstat-like CLI over a Bukvarix regional keyword dump (Moscow,
113M+ phrases) — frequency lookups and related-phrase search, no network,
no API rate limits — plus the Bukvarix web API for world frequencies and
domain keywords. Everything command-specific is in the CLI itself: run
`bukvarix` with no arguments for version + command list, `bukvarix
<command> --help` for details. This document only covers what `--help`
can't tell you. If you can run the CLI but not read files, `bukvarix --ai`
prints this document.

## Install (if `bukvarix --version` fails)

```bash
uv tool install rb-bukvarix --index https://homeserver.tailee01ab.ts.net:3000/api/packages/bolandeu/pypi/simple
```

From the git tag instead:

```bash
uv tool install "rb-bukvarix @ git+https://homeserver.tailee01ab.ts.net:3000/bolandeu/bukvarix.git@vX.Y.Z"
```

Replace `vX.Y.Z` with the latest release tag (install from a tag, never a
branch: [the Standard](https://homeserver.tailee01ab.ts.net:3000/bolandeu/cli-generator/src/branch/main/skills/cli-builder/reference/standard.md), Install).

Same command on macOS, Linux and Windows; `uv tool upgrade rb-bukvarix`
updates it. Do not run from source unless asked to develop the CLI.

## Conventions

- **`--json` on every command.** Parse stdout; never scrape human output.
  stdout is data only, stderr is diagnostics/progress.
- **Not project-aware for querying.** `search`/`freq`/`stats`/`db ...` work
  from any directory against one machine-wide database — no `init` needed.
  (`init`/`status`/`config` exist for parity with this CLI's sibling tools
  but hold no data bukvarix's own commands read.)
- **The database is a separate resource from the CLI.** `search`/`freq`
  fail with `DATABASE_NOT_READY` (exit 1) if it hasn't been built yet —
  `stats --json` always works and reports `keywords_rows: 0` in that case.
  Default location: `user_data_dir("bukvarix")/bukvarix.duckdb` (macOS
  `~/Library/Application Support/bukvarix/`, Linux `~/.local/share/bukvarix/`).
  `db set-path PATH` points the CLI at an existing `.duckdb` file elsewhere;
  `BUKVARIX_DB_PATH` overrides it for one process/session.
- **Speeds.** `freq` is an indexed lookup (ms). Default `search` uses a
  lemma index when it's built (`"engine": "postings"`, 0.1-0.4s); if the
  JSON says `"engine": "scan"`, it's a full scan (16-35s) — expect it,
  don't assume a hang, and tell the user `bukvarix db build-postings`
  would fix it. Results match whole lemmas, word order and form don't
  matter, stop words ("в", "для", "как") are ignored unless forced with `+`.
- **`--non-interactive`** on anything that could prompt. It fails with a
  structured error instead of hanging. It is a global option — put it
  before the command (`bukvarix --non-interactive db ...`); after the
  command it is rejected as `No such option`.
- **Two sources.** Default `--source local` = the Moscow database
  (`freq_msk_exact`). `--source api` on `search`/`freq` and the `site`
  command use the Bukvarix web API: frequencies for the whole world
  (`freq_world_exact`, `freq_world_broad`, `region: "world"`) — never
  present them as Moscow numbers. They need the network and are paced
  (2 s per `freq` phrase), so prefer local for Moscow lookups. `site
  d1.ru d2.ru ...` (2-10 domains) is the competitors' semantic core;
  `--region` there only picks the SERP the positions come from.
- **No stored credentials** — the API key is optional (`BUKVARIX_API_KEY`,
  default free key); `auth status --json`
  reports an empty `credentials` list and exits 0.

- **`doctor --json`** first when something looks off — it reports
  what the CLI needs to run: its version, the database, the API key source, the project and the external programs.
- **`describe --json`** — the whole CLI as one machine-readable OpenCLI
  document (commands, options, exit codes, env vars, data): the bare
  document, no `{"ok": ...}` envelope.

## Destructive commands — always get explicit sign-off first

`db import-keywords`, `db normalize`, `db reindex` and `db build-postings` mutate the live,
tens-of-gigabytes database file in place, with **no automatic backup**.
`db import-keywords` in particular replaces the entire keywords table —
anything `normalize`/`reindex` built on top of it is gone too. Their
`--help` shows `--dry-run`/`--confirm`:

1. Run with `--dry-run` first, show the user what would happen.
2. Get explicit human sign-off — never infer it from the original task
   wording alone.
3. Only then re-run with `--confirm` (add `bukvarix --non-interactive` for
   unattended use; without `--confirm` that combination fails loudly
   instead of hanging on a prompt).

Never chain straight to `--confirm` without a human having seen the
dry-run. These are maintenance operations, not part of normal querying —
day-to-day use is `search`/`freq`/`stats` only.

## Errors

Error envelope: `{"ok": false, "error": {"code", "message", "action"}}`.
Read exit code → `error.code` → `error.action` (the concrete next command).

    0  ok
    1  general (UNEXPECTED = a bug: re-run with --verbose, report; ABORTED = user said no;
       DATABASE_NOT_READY = no db built yet → db import-keywords)
    2  invalid arguments
    3  credentials: API key rejected (CREDENTIALS_INVALID, BUKVARIX_API_KEY only)
    4  project not initialized (only for init/status/config — search/freq/stats/db don't need this)
    5  provider error (shared-database errors of the template; bukvarix web-API errors use exit 1)
    6  CLI too old for this project → uv tool upgrade rb-bukvarix
