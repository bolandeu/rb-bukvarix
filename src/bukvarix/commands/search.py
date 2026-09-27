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

_MODES = {"contains", "words", "prefix", "exact", "query", "scan"}


@output.guard
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Фраза; +слово — обязательное, -слово — исключить."),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="без флага — умный дефолт (self + похожие фразы, порядок/форма слов не важны, как в Wordstat) | contains | words | prefix | exact | query | scan",
    ),
    limit: int = typer.Option(20, "--limit", help="Сколько похожих фраз вернуть."),
    csv_path: Path | None = typer.Option(None, "--csv", help="Сохранить результаты в CSV вместо печати"),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Поиск фраз. По умолчанию: частотность самой фразы + список похожих
    фраз в глубину (порядок слов и словоформы не важны) — один вызов,
    как ввод в Wordstat. Явный --mode для более узкого/буквального контроля:

    --mode contains: буквальная подстрока, порядок слов важен
    --mode words: все слова присутствуют, порядок не важен (без лемм)
    --mode prefix: фраза начинается с QUERY
    --mode exact: только единственная каноническая фраза (после дедупа перестановок)
    --mode query: буквальные операторы +слово/-слово (без лемм)
      например "+в москве -недорого"
    --mode scan: то же, что дефолт, но полным сканом без индекса лемм
      (медленно; ищет по подстроке — для сверки с индексом)

    Example: {cli} search "растаможка авто" --limit 10 --json
    """
    if mode is not None and mode not in _MODES:
        raise InvalidArguments(f"Invalid --mode: {mode!r}. Use {'|'.join(sorted(_MODES))}.")

    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)

    t0 = time.perf_counter()
    if mode in (None, "scan"):
        smart = search_smart(db_path, query, limit, force_scan=mode == "scan")
        self_result, results = smart["self"], smart["results"]
        total_count, total_freq_sum = smart["total_count"], smart["total_freq_sum"]
        engine = smart["engine"]
    else:
        self_result, results = None, db_search(db_path, query, mode, limit)
        total_count = total_freq_sum = engine = None
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
    if mode in (None, "scan"):
        payload["self"] = self_result
        payload["total_count"] = total_count
        payload["total_freq_sum"] = total_freq_sum
        payload["engine"] = engine
    output.emit_success(ctx, payload, render_human=output.human.render_search)
