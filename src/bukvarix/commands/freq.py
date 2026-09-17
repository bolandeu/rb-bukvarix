from __future__ import annotations

import time

import typer

from .. import output
from ..core import services
from ..core.db import freq as db_freq


@output.guard
def freq(
    ctx: typer.Context,
    query: str,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Exact frequency lookup for a single phrase (word order doesn't matter)."""
    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)

    t0 = time.perf_counter()
    result = db_freq(db_path, query)
    elapsed = time.perf_counter() - t0

    output.emit_success(
        ctx,
        {"query": query, "result": result, "elapsed_seconds": elapsed},
        render_human=output.human.render_freq,
    )
