from __future__ import annotations

from rich.console import Console

# Data goes to stdout; errors and diagnostics go to stderr, so
# `<cli> status > out.txt` never captures an "Error: ..." line as data.
console = Console()
err_console = Console(stderr=True)


def echo(message: str) -> None:
    console.print(message)


def key_values(title: str, pairs: dict) -> None:
    console.print(f"[bold]{title}[/bold]")
    for key, value in pairs.items():
        console.print(f"  {key}: {value}")


def render_doctor(checks: list[dict]) -> None:
    for check in checks:
        mark = "[green]✓[/green]" if check["ok"] else "[red]✗[/red]"
        detail = f" ({check['detail']})" if check.get("detail") else ""
        console.print(f"{mark} {check['name']}{detail}")


def render_search(data: dict) -> None:
    self_result = data.get("self")
    results = data["results"]
    smart = data["mode"] in ("default", "scan")
    if smart and self_result:
        console.print(f"[bold]{self_result['phrase']}: {self_result['freq_msk_exact']}[/bold]")
    for r in results:
        console.print(f"{r['phrase']}\t{r['freq_msk_exact']}")
    total_count = data.get("total_count")
    if smart and total_count is not None:
        if total_count > len(results):
            console.print(
                f"[dim]... показано {len(results)} из {total_count}, задайте --limit {total_count} чтобы увидеть все[/dim]"
            )
        console.print(f"[dim]сумма по всем {total_count} похожим: {data['total_freq_sum']}[/dim]")
    engine = f", engine: {data['engine']}" if data.get("engine") else ""
    console.print(f"[dim]({data['elapsed_seconds']:.3f}s{engine})[/dim]")


def render_freq(data: dict) -> None:
    result = data["result"]
    elapsed = f" [dim]({data['elapsed_seconds']:.3f}s)[/dim]"
    if result is None:
        console.print(f"[yellow]not found:[/yellow] {data['query']}{elapsed}")
        return
    via = result.get("matched_via")
    suffix = f" [dim]({via})[/dim]" if via and via != "exact" else ""
    console.print(f"{result['phrase']}: {result['freq_msk_exact']}{suffix}{elapsed}")


def render_stats(data: dict) -> None:
    console.print(f"DB path: {data['db_path']}")
    console.print(f"DB size: {data['db_size_bytes'] / 1e9:.2f} GB")
    console.print(f"Tables: {', '.join(data['tables']) or '(none)'}")
    console.print(f"keywords rows: {data['keywords_rows']:,}")
