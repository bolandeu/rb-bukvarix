from __future__ import annotations

import csv
import time
from pathlib import Path

import typer

from .. import output
from ..core import api, services
from ..core.db import search as db_search
from ..core.db import search_smart
from ..core.errors import InvalidArguments

_MODES = {"contains", "words", "prefix", "exact", "query", "scan"}
_SOURCES = {"local", "api"}
# rows taken from the API per search: it returns phrases shortest first, not by frequency,
# so a few hundred rows hold the phrase itself and its close variants
_API_MIN_FETCH = 200


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
    source: str = typer.Option(
        "local",
        "--source",
        help="local — локальная база (Москва, freq_msk_exact) | api — веб-API Bukvarix (весь мир, freq_world_exact/freq_world_broad, нужна сеть)",
    ),
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

    --source api: те же поля ответа, но данные веб-API Bukvarix по региону
    «Весь мир» (region "world", source "bukvarix-api"): freq_world_exact —
    точная частотность, freq_world_broad — широкая. Операторы — синтаксис
    API (+слово, -слово, !слово). API отдаёт фразы от коротких к длинным,
    поэтому берутся первые max(--limit, 200) строк (fetched) и сортируются
    по точной частотности; total_count — полное число фраз в API,
    truncated — взяты не все, total_freq_sum — null. Без --mode.
    Ключ — BUKVARIX_API_KEY, без него бесплатный 'free'.

    Example: {cli} search "растаможка авто" --limit 10 --json
    """
    if mode is not None and mode not in _MODES:
        raise InvalidArguments(f"Invalid --mode: {mode!r}. Use {'|'.join(sorted(_MODES))}.")
    if source not in _SOURCES:
        raise InvalidArguments(f"Invalid --source: {source!r}. Use local|api.")
    if source == "api":
        if mode is not None:
            raise InvalidArguments("--mode works only with --source local.")
        _search_api(ctx, query, limit, csv_path)
        return

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
        _save_csv(ctx, query, csv_path, results, ["phrase", "freq_msk_exact"], elapsed)
        return

    payload = {"query": query, "mode": mode or "default", "results": results, "elapsed_seconds": elapsed}
    if mode in (None, "scan"):
        payload["self"] = self_result
        payload["total_count"] = total_count
        payload["total_freq_sum"] = total_freq_sum
        payload["engine"] = engine
    output.emit_success(ctx, payload, render_human=output.human.render_search)


def _save_csv(
    ctx: typer.Context, query: str, csv_path: Path, results: list[dict], default_fields: list[str], elapsed: float
) -> None:
    fieldnames = list(results[0].keys()) if results else default_fields
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    output.emit_success(
        ctx,
        {"query": query, "saved_to": str(csv_path), "rows": len(results)},
        render_human=lambda d: output.human.echo(f"Saved {d['rows']} rows to {d['saved_to']} ({elapsed:.3f}s)"),
    )


def _search_api(ctx: typer.Context, query: str, limit: int, csv_path: Path | None) -> None:
    if limit < 1:
        raise InvalidArguments("--limit must be at least 1.")
    t0 = time.perf_counter()
    rows = api.keywords(query, min(max(limit, _API_MIN_FETCH), api.MAX_ROWS))
    total_count = api.keywords_count(query)
    self_row = api.pick_phrase(rows, query)
    if self_row is None and api.phrase_words(query):
        self_row = api.lookup(query)
    results = sorted(rows, key=lambda r: r["freq_world_exact"] or 0, reverse=True)[:limit]
    elapsed = time.perf_counter() - t0

    if csv_path:
        _save_csv(ctx, query, csv_path, results, ["phrase", "freq_world_exact", "freq_world_broad"], elapsed)
        return

    payload = {
        "query": query,
        "mode": "default",
        "source": api.SOURCE,
        "region": api.REGION_WORLD,
        "results": results,
        "self": {**self_row, "matched_via": "exact"} if self_row else None,
        "total_count": total_count,
        "total_freq_sum": None,
        "fetched": len(rows),
        "truncated": total_count > len(rows),
        "engine": "api",
        "elapsed_seconds": elapsed,
    }
    output.emit_success(ctx, payload, render_human=output.human.render_search)
