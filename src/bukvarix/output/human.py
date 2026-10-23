"""Human-readable output (messages, key-value lists, the doctor report), kept apart from the JSON output."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

# Data goes to stdout; errors and diagnostics go to stderr, so
# `<cli> status > out.txt` never captures an "Error: ..." line as data.
console = Console()
err_console = Console(stderr=True)


def echo(message: str) -> None:
    """Print plain text. Escaped: rich would otherwise treat `[...]` in data as markup and drop it."""
    console.print(escape(message))


def echo_markup(message: str) -> None:
    """Print rich markup (`[green]✓[/green]`); escape() any data you put inside it."""
    console.print(message)


def key_values(title: str, pairs: dict) -> None:
    console.print(f"[bold]{escape(title)}[/bold]")
    for key, value in pairs.items():
        console.print(f"  {escape(str(key))}: {escape(str(value))}")


def render_doctor(checks: list[dict]) -> None:
    for check in checks:
        mark = "[green]✓[/green]" if check["ok"] else "[red]✗[/red]"
        detail = f" ({check['detail']})" if check.get("detail") else ""
        console.print(f"{mark} {escape(check['name'] + detail)}")


def _freq(row: dict) -> str:
    """Exact frequency of a local (Moscow) or API (world: exact / broad) row."""
    if "freq_world_exact" in row:
        return f"{row['freq_world_exact']}\t{row['freq_world_broad']}"
    return str(row["freq_msk_exact"])


def render_search(data: dict) -> None:
    self_result = data.get("self")
    results = data["results"]
    smart = data["mode"] in ("default", "scan")
    if data.get("source") == "bukvarix-api":
        console.print("[dim]весь мир (API): фраза, точная, широкая[/dim]")
    if smart and self_result:
        console.print(f"[bold]{self_result['phrase']}: {_freq(self_result)}[/bold]")
    for r in results:
        console.print(f"{r['phrase']}\t{_freq(r)}")
    total_count = data.get("total_count")
    if smart and total_count is not None:
        if total_count > len(results):
            console.print(
                f"[dim]... показано {len(results)} из {total_count}, задайте --limit {total_count} чтобы увидеть все[/dim]"
            )
        if data["total_freq_sum"] is not None:
            console.print(f"[dim]сумма по всем {total_count} похожим: {data['total_freq_sum']}[/dim]")
    engine = f", engine: {data['engine']}" if data.get("engine") else ""
    console.print(f"[dim]({data['elapsed_seconds']:.3f}s{engine})[/dim]")


def render_freq(data: dict) -> None:
    for r in data["results"]:
        if r["phrase"] is None:
            console.print(f"[yellow]not found:[/yellow] {r['query']}")
            continue
        via = r["matched_via"]
        suffix = f" [dim]({via})[/dim]" if via != "exact" else ""
        console.print(f"{r['phrase']}: {_freq(r)}{suffix}")
    date = data["data_date"] or ("живые данные" if data["source"] == "bukvarix-api" else "дата данных неизвестна")
    console.print(f"[dim]({data['source']}, регион {data['region']}, {date}; {data['elapsed_seconds']:.3f}s)[/dim]")


def render_site(data: dict) -> None:
    domains = data["domains"]
    head = "фраза, точная, широкая, позиция" if len(domains) == 1 else "фраза, точная, широкая, доменов, позиции"
    console.print(f"[dim]{', '.join(domains)}; регион {data['region']}; весь мир (API): {head}[/dim]")
    for r in data["results"]:
        if len(domains) == 1:
            tail = r["position"]
        else:
            tail = f"{r['domains_found']}\t" + " ".join(f"{p if p else '-'}" for p in r["positions"].values())
        console.print(f"{r['phrase']}\t{r['freq_world_exact']}\t{r['freq_world_broad']}\t{tail}")
    if data["total_count"] > len(data["results"]):
        console.print(f"[dim]... показано {len(data['results'])} из {data['total_count']}, задайте --limit[/dim]")
    console.print(f"[dim]({data['elapsed_seconds']:.3f}s)[/dim]")


def render_stats(data: dict) -> None:
    console.print(f"DB path: {data['db_path']}")
    console.print(f"DB size: {data['db_size_bytes'] / 1e9:.2f} GB")
    console.print(f"Tables: {', '.join(data['tables']) or '(none)'}")
    console.print(f"keywords rows: {data['keywords_rows']:,}")
