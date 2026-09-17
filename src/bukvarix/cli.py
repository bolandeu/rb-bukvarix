from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bukvarix import db

app = typer.Typer(add_completion=False, help="Local Wordstat-like CLI over Bukvarix dumps")
console = Console()
err_console = Console(stderr=True)


@app.command("import-keywords")
def import_keywords_cmd(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to keywords TSV (phrase\\tfreq, no header)"),
):
    """Import a Bukvarix regional keywords TSV into the local database."""
    err_console.print(f"Importing {path} ...")
    result = db.import_keywords(path)
    err_console.print(
        f"Done: {result['raw_rows']:,} raw rows -> {result['deduped_rows']:,} after "
        f"removing {result['removed_permutations']:,} word-order duplicates."
    )


@app.command()
def reindex():
    """Build the `signature` index separately (see db.build_indexes docstring)."""
    err_console.print("Building index on `signature` ...")
    db.build_indexes()
    err_console.print("Done.")


@app.command()
def normalize():
    """Add content_signature (stop-words) and lemma_signature (pymorphy3) match keys.

    Non-destructive — see PRD §4.1. Lets freq/search --mode exact fall back
    to a looser match when the exact word-order signature misses.
    """
    err_console.print("Building content_signature + lemma_signature ...")
    result = db.normalize()
    err_console.print(f"Done: lemmatized {result['distinct_words_lemmatized']:,} distinct words.")


@app.command()
def search(
    query: str,
    mode: Optional[str] = typer.Option(
        None, "--mode",
        help="omit for the default (self + related phrases, order/form-insensitive, like Wordstat) | contains | words | prefix | exact | query",
    ),
    limit: int = typer.Option(20, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
    csv_path: Optional[Path] = typer.Option(None, "--csv", help="Save results as CSV instead of printing them"),
):
    """Search phrases. Default: the phrase's own frequency + a depth list of
    related phrases (word order and word form don't matter) — one call,
    like typing into Wordstat. Explicit --mode for narrower/literal control:

    --mode contains: literal substring, word order matters
    --mode words: all words present, any order (no lemma normalization)
    --mode prefix: phrase starts with QUERY
    --mode exact: only the single canonical phrase (post word-order dedup)
    --mode query: operators — +word (require), -word (exclude), (a|b) (group)
      e.g. "+в москве -недорого (авто|машина)"
    """
    if mode is not None and mode not in {"contains", "words", "prefix", "exact", "query"}:
        err_console.print(f"Invalid --mode: {mode!r}. Use contains|words|prefix|exact|query.")
        raise typer.Exit(code=2)

    t0 = time.perf_counter()
    total_count = total_freq_sum = None
    if mode is None:
        smart = db.search_smart(query, limit)
        self_result, results = smart["self"], smart["results"]
        total_count, total_freq_sum = smart["total_count"], smart["total_freq_sum"]
    else:
        self_result, results = None, db.search(query, mode, limit)
    elapsed = time.perf_counter() - t0

    if csv_path:
        fieldnames = list(results[0].keys()) if results else ["phrase", "freq_msk_exact"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        err_console.print(f"Saved {len(results)} rows to {csv_path} ({elapsed:.3f}s)")
        return
    if as_json:
        payload = {"query": query, "mode": mode or "default", "results": results}
        if mode is None:
            payload["self"] = self_result
            payload["total_count"] = total_count
            payload["total_freq_sum"] = total_freq_sum
        print(json.dumps(payload, ensure_ascii=False))
        return
    if mode is None and self_result:
        console.print(f"[bold]{self_result['phrase']}: {self_result['freq_msk_exact']}[/bold]")
    for r in results:
        console.print(f"{r['phrase']}\t{r['freq_msk_exact']}")
    if mode is None and total_count is not None:
        if total_count > len(results):
            console.print(f"[dim]... показано {len(results)} из {total_count}, задайте --limit {total_count} чтобы увидеть все[/dim]")
        console.print(f"[dim]сумма по всем {total_count} похожим: {total_freq_sum}[/dim]")
    console.print(f"[dim]({elapsed:.3f}s)[/dim]")


@app.command()
def freq(query: str, as_json: bool = typer.Option(False, "--json")):
    """Exact frequency lookup for a single phrase (word order doesn't matter)."""
    t0 = time.perf_counter()
    result = db.freq(query)
    elapsed = time.perf_counter() - t0
    if as_json:
        print(json.dumps({"query": query, "result": result}, ensure_ascii=False))
        return
    if result is None:
        console.print(f"[yellow]not found:[/yellow] {query} [dim]({elapsed:.3f}s)[/dim]")
    else:
        via = result.get("matched_via")
        suffix = f" [dim]({via})[/dim]" if via and via != "exact" else ""
        console.print(f"{result['phrase']}: {result['freq_msk_exact']}{suffix} [dim]({elapsed:.3f}s)[/dim]")


@app.command()
def stats(as_json: bool = typer.Option(False, "--json")):
    """Database stats: path, size, row counts."""
    result = db.stats()
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return
    console.print(f"DB path: {result['db_path']}")
    console.print(f"DB size: {result['db_size_bytes'] / 1e9:.2f} GB")
    console.print(f"Tables: {', '.join(result['tables']) or '(none)'}")
    console.print(f"keywords rows: {result['keywords_rows']:,}")


if __name__ == "__main__":
    app()
