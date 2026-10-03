from __future__ import annotations

import sys
import time
from pathlib import Path

import typer

from .. import output
from ..core import services
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
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Точная частотность фраз (порядок слов не важен) — одной или пачкой.

    Ответ всегда один: results по фразе на каждый вход, в порядке ввода, плюс
    источник (source), регион (region, 213 = Москва) и дата данных (data_date;
    null — база импортирована до того, как дату начали сохранять). Фразы нет
    в базе — freq_msk_exact 0 и matched_via null, не ошибка.

    Example: {cli} freq "доставка грузов из китая" "доставка из китая" --json
    """
    phrases = [*(queries or []), *(_read_phrases(input_path) if input_path else [])]
    phrases = [p.strip() for p in phrases if p.strip()]
    if not phrases:
        raise InvalidArguments("No phrases given: pass them as arguments or via --input <file|->.")

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
