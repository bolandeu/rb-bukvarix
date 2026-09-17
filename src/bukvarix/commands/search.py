from __future__ import annotations

import csv
import time
from pathlib import Path

import typer

from .. import output
from ..core import services
from ..core.db import search as db_search
from ..core.db import search_smart
from ..core.errors import InvalidArguments

_MODES = {"contains", "words", "prefix", "exact", "query"}


@output.guard
def search(
    ctx: typer.Context,
    query: str,
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="omit for the default (self + related phrases, order/form-insensitive, like Wordstat) | contains | words | prefix | exact | query",
    ),
    limit: int = typer.Option(20, "--limit"),
    csv_path: Path | None = typer.Option(None, "--csv", help="Save results as CSV instead of printing them"),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Search phrases. Default: the phrase's own frequency + a depth list of
    related phrases (word order and word form don't matter) — one call,
    like typing into Wordstat. Explicit --mode for narrower/literal control:

    --mode contains: literal substring, word order matters
    --mode words: all words present, any order (no lemma normalization)
    --mode prefix: phrase starts with QUERY
    --mode exact: only the single canonical phrase (post word-order dedup)
    --mode query: literal +word/-word operators (no lemma normalization)
      e.g. "+в москве -недорого"
    """
    if mode is not None and mode not in _MODES:
        raise InvalidArguments(f"Invalid --mode: {mode!r}. Use {'|'.join(sorted(_MODES))}.")

    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)

    t0 = time.perf_counter()
    if mode is None:
        smart = search_smart(db_path, query, limit)
        self_result, results = smart["self"], smart["results"]
        total_count, total_freq_sum = smart["total_count"], smart["total_freq_sum"]
    else:
        self_result, results = None, db_search(db_path, query, mode, limit)
        total_count = total_freq_sum = None
    elapsed = time.perf_counter() - t0

    if csv_path:
        fieldnames = list(results[0].keys()) if results else ["phrase", "freq_msk_exact"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        output.emit_success(
            ctx,
            {"query": query, "saved_to": str(csv_path), "rows": len(results)},
            render_human=lambda d: output.human.echo(f"Saved {d['rows']} rows to {d['saved_to']} ({elapsed:.3f}s)"),
        )
        return

    payload = {"query": query, "mode": mode or "default", "results": results, "elapsed_seconds": elapsed}
    if mode is None:
        payload["self"] = self_result
        payload["total_count"] = total_count
        payload["total_freq_sum"] = total_freq_sum
    output.emit_success(ctx, payload, render_human=output.human.render_search)
