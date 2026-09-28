from __future__ import annotations

import typer

from .. import output
from ..core import deps, services
from ..core.version import DEV_VERSION, installed_version

_PACKAGE_NAME = __name__.split(".")[0]


def _db_checks() -> list[dict]:
    """Проверки db_path/db_ready для doctor — не Typer-команда, не попадает в --help."""
    db_path = services.resolve_db_path()
    ready = db_path.exists()
    return [
        {
            "name": "db_path",
            "ok": True,
            "detail": str(db_path),
        },
        {
            "name": "db_ready",
            "ok": ready,
            "detail": None
            if ready
            else f"no database at {db_path} — run '{_PACKAGE_NAME} db import-keywords <path.tsv>'",
        },
    ]


@output.guard
def doctor(
    ctx: typer.Context,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Проверить готовность окружения, базы данных и внешних зависимостей.

    Внешние программы — по одной проверке `external:<имя>` на каждую из
    записей tool.rb.external в pyproject.toml (сейчас их нет).

    Всегда завершается с exit 0 — сам отчёт и есть результат; `healthy`
    показывает, прошли ли все проверки.

    Example: {cli} doctor --json
    """
    checks: list[dict] = [{"name": "python", "ok": True}]
    checks.append({"name": "cli_version", "ok": True, "detail": installed_version() or DEV_VERSION})
    checks.extend(_db_checks())
    checks.extend(deps.doctor_checks())  # [[tool.rb.external]] in pyproject.toml

    all_ok = all(check["ok"] for check in checks)
    output.emit_success(
        ctx,
        {"healthy": all_ok, "checks": checks},
        render_human=lambda data: output.human.render_doctor(data["checks"]),
    )
