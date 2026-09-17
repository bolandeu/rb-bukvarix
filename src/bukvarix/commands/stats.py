from __future__ import annotations

import typer

from .. import output
from ..core import services
from ..core.db import stats as db_stats


@output.guard
def stats(
    ctx: typer.Context,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Статистика базы данных: путь, размер, число строк. Работает, даже если базы ещё нет."""
    db_path = services.resolve_db_path()
    result = db_stats(db_path)
    output.emit_success(ctx, result, render_human=output.human.render_stats)
