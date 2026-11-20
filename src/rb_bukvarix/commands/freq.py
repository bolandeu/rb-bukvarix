from __future__ import annotations

import sys
import time
from pathlib import Path

import typer

from .. import output
from ..core import api, services
from ..core.db import REGION, SOURCE, data_date, freq_batch
from ..core.errors import InvalidArguments


def _read_phrases(source: str) -> list[str]:
    if source == "-":
        return sys.stdin.read().splitlines()
    try:
        return Path(source).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InvalidArguments(f"--input: cannot read {source!r}: {exc.strerror or exc}.") from None


@output.guard
def freq(
    ctx: typer.Context,
    queries: list[str] | None = typer.Argument(
        None, help="Фразы, точная частотность которых нужна (одна или несколько)."
    ),
    input_path: str | None = typer.Option(
        None, "--input", help="Файл с фразами, по одной на строку; '-' — читать из stdin. Добавляется к аргументам."
    ),
    source: str = typer.Option(
        "local",
        "--source",
        help="local — локальная база (Москва, freq_msk_exact) | api — веб-API Bukvarix (весь мир, freq_world_exact/freq_world_broad, нужна сеть)",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Точная частотность фраз (порядок слов не важен) — одной или пачкой.

    Ответ всегда один: results по фразе на каждый вход, в порядке ввода, плюс
    источник (source), регион (region, 213 = Москва) и дата данных (data_date;
    null — база импортирована до того, как дату начали сохранять). Фразы нет
    в базе — freq_msk_exact 0 и matched_via null, не ошибка.

    --source api: тот же конверт, но по веб-API Bukvarix, регион «Весь мир»
    (region "world", source "bukvarix-api", data_date null — данные живые):
    freq_world_exact и freq_world_broad вместо freq_msk_exact. Один запрос
    на фразу, пауза 2 с между запросами (100 фраз ≈ 3.5 мин), ход — в stderr.
    Ключ — BUKVARIX_API_KEY, без него бесплатный 'free'.

    Example: {cli} freq "доставка грузов из китая" "доставка из китая" --json
    """
    if source not in ("local", "api"):
        raise InvalidArguments(f"Invalid --source: {source!r}. Use local|api.")
    phrases = [*(queries or []), *(_read_phrases(input_path) if input_path else [])]
    phrases = [p.strip() for p in phrases if p.strip()]
    if not phrases:
        raise InvalidArguments("No phrases given: pass them as arguments or via --input <file|->.")
    if source == "api":
        _freq_api(ctx, phrases)
        return

    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)

    t0 = time.perf_counter()
    found = freq_batch(db_path, phrases)
    results = [
        {
            "query": q,
            "phrase": hit["phrase"] if hit else None,
            "freq_msk_exact": hit["freq_msk_exact"] if hit else 0,
            "matched_via": hit["matched_via"] if hit else None,
        }
        for q in phrases
        for hit in [found[q]]
    ]
    payload = {
        "count": len(results),
        "region": REGION,
        "source": SOURCE,
        "data_date": data_date(db_path),
        "results": results,
        "elapsed_seconds": time.perf_counter() - t0,
    }
    output.emit_success(ctx, payload, render_human=output.human.render_freq)


def _freq_api(ctx: typer.Context, phrases: list[str]) -> None:
    t0 = time.perf_counter()
    results = []
    for i, q in enumerate(phrases, 1):
        if len(phrases) > 1 and not ctx.obj.get("quiet"):
            output.human.err_console.print(f"[dim][{i}/{len(phrases)}] {q}[/dim]")
        hit = api.lookup(q)
        results.append(
            {
                "query": q,
                "phrase": hit["phrase"] if hit else None,
                "freq_world_exact": hit["freq_world_exact"] if hit else 0,
                "freq_world_broad": hit["freq_world_broad"] if hit else 0,
                "matched_via": "exact" if hit else None,
            }
        )
    payload = {
        "count": len(results),
        "region": api.REGION_WORLD,
        "source": api.SOURCE,
        "data_date": None,
        "results": results,
        "elapsed_seconds": time.perf_counter() - t0,
    }
    output.emit_success(ctx, payload, render_human=output.human.render_freq)
