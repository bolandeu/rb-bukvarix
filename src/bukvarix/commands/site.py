from __future__ import annotations

import csv
import time
from pathlib import Path

import typer

from .. import output
from ..core import api
from ..core.errors import InvalidArguments

_REGION_HELP = ", ".join(f"{code} — {name}" for code, name in api.REGIONS.items())


def _domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not domain or "." not in domain:
        raise InvalidArguments(f"Not a domain: {value!r}. Pass domain.com or sub.domain.com, without a path.")
    return domain


@output.guard
def site(
    ctx: typer.Context,
    domains: list[str] = typer.Argument(
        ..., help="Домен (domain.com, sub.domain.com) или 2-10 доменов конкурентов через пробел."
    ),
    region: str = typer.Option(
        api.DEFAULT_REGION, "--region", help=f"Поисковик и регион выдачи, по которой взяты позиции: {_REGION_HELP}."
    ),
    limit: int = typer.Option(
        1000, "--limit", help=f"Сколько фраз выгрузить (до {api.MAX_ROWS:,} на бесплатном ключе).".replace(",", " ")
    ),
    csv_path: Path | None = typer.Option(None, "--csv", help="Сохранить результаты в CSV вместо печати"),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Ключевые слова домена или ядро конкурентов из веб-API Bukvarix (нужна сеть).

    Один домен: фразы, по которым он есть в выдаче региона, с позицией
    (position) и числом результатов в поисковике (serp_results). 2-10
    доменов: ядро конкурентов — общий список фраз этих доменов, в каждой
    domains_found (сколько из доменов есть в выдаче), avg_position и
    positions {домен: позиция, null — домена нет}. Частотность везде по
    региону «Весь мир» (freq_world_exact — точная, freq_world_broad —
    широкая); --region меняет только выдачу, по которой взяты позиции.
    Порядок фраз — как отдаёт API; total_count — сколько фраз всего.
    Ключ — BUKVARIX_API_KEY, без него бесплатный 'free'; пауза 1 с между
    запросами.

    Example: {cli} site avito.ru auto.ru drom.ru --region spb --limit 5000 --csv core.csv
    """
    if region not in api.REGIONS:
        raise InvalidArguments(f"Invalid --region: {region!r}. Use one of: {', '.join(api.REGIONS)}.")
    if not 1 <= limit <= api.MAX_ROWS:
        raise InvalidArguments(f"--limit must be between 1 and {api.MAX_ROWS}.")
    names = list(dict.fromkeys(_domain(d) for d in domains))
    if len(names) > api.MAX_DOMAINS:
        raise InvalidArguments(f"At most {api.MAX_DOMAINS} domains in one report (free API key), got {len(names)}.")

    t0 = time.perf_counter()
    if len(names) == 1:
        results = api.site(names[0], region, limit)
        total_count = api.site_count(names[0], region)
    else:
        results = api.sites(names, region, limit)
        total_count = api.sites_count(names, region)
    elapsed = time.perf_counter() - t0

    if csv_path:
        _save_csv(csv_path, results)
        output.emit_success(
            ctx,
            {"domains": names, "saved_to": str(csv_path), "rows": len(results), "total_count": total_count},
            render_human=lambda d: output.human.echo(
                f"Saved {d['rows']} of {d['total_count']} rows to {d['saved_to']} ({elapsed:.3f}s)"
            ),
        )
        return

    payload = {
        "domains": names,
        "region": region,
        "freq_region": api.REGION_WORLD,
        "source": api.SOURCE,
        "results": results,
        "total_count": total_count,
        "truncated": total_count > len(results),
        "elapsed_seconds": elapsed,
    }
    output.emit_success(ctx, payload, render_human=output.human.render_site)


def _save_csv(csv_path: Path, results: list[dict]) -> None:
    """One column per competitor instead of the nested `positions` object."""
    rows = [
        {**{k: v for k, v in r.items() if k != "positions"}, **{f"position {d}": p for d, p in r["positions"].items()}}
        if "positions" in r
        else r
        for r in results
    ]
    fields = list(rows[0]) if rows else ["phrase", "freq_world_exact", "freq_world_broad"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
