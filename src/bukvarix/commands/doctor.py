from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import typer

from .. import output
from ..core import services
from ..core.errors import CliTooOld
from ..core.models import ProjectConfig
from ..core.version import DEV_VERSION, installed_version

_PACKAGE_NAME = __name__.split(".")[0]


def _auth_checks(provider: str) -> list[dict]:
    """doctor's credential_store/credential checks — not a Typer command, no --help text."""
    # credentials/ is dropped at generation time when include_auth=false;
    # detecting it at runtime keeps this file free of template logic.
    if importlib.util.find_spec(f"{_PACKAGE_NAME}.credentials") is None:
        return []
    credentials = importlib.import_module(f"{_PACKAGE_NAME}.credentials.manager")

    backend = credentials.store_backend()
    checks: list[dict] = [
        {
            "name": "credential_store",
            "ok": backend is not None,
            "detail": backend or "no usable OS keychain backend; credentials must come from environment variables",
        }
    ]
    available = credentials.get_credential(provider) is not None
    checks.append(
        {
            "name": "credential",
            "ok": available,
            "detail": f"provider '{provider}'"
            + ("" if available else f" — run '{_PACKAGE_NAME} auth login --provider {provider}'"),
        }
    )
    return checks


def _db_checks() -> list[dict]:
    """doctor's db_path/db_ready checks — not a Typer command, no --help text."""
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
    """Run diagnostic checks and report readiness.

    Always exits 0 — the report itself is the result; `healthy` says
    whether every check passed. Project checks are informational only:
    search/freq/stats/db work without a project (see 'bukvarix db --help').
    """
    checks: list[dict] = [{"name": "python", "ok": True}]
    checks.append({"name": "cli_version", "ok": True, "detail": installed_version() or DEV_VERSION})
    checks.extend(_db_checks())

    start = Path(ctx.obj.get("project") or Path.cwd())
    output.vecho(ctx, f"Searching for a project root starting at: {start}")
    root = services.find_project_root(start)
    checks.append({"name": "project", "ok": root is not None, "detail": str(root) if root else None})

    config: ProjectConfig | None = None
    if root is not None:
        try:
            config = services.load_config(root, check_compat=False)
            checks.append({"name": "config", "ok": True})
        except Exception as exc:
            checks.append({"name": "config", "ok": False, "detail": str(exc)})
    if config is not None:
        try:
            services.check_cli_compat(config)
            checks.append({"name": "cli_compat", "ok": True, "detail": f"project requires >= {config.cli.min_version}"})
        except CliTooOld as exc:
            checks.append({"name": "cli_compat", "ok": False, "detail": exc.message})

    checks.extend(_auth_checks(config.provider.name if config else "default"))

    # "healthy" tracks the load-bearing checks only — no project/config is
    # normal for this CLI's primary (non-project) commands, so it doesn't
    # count against overall health the way db_ready does.
    informational = {"project", "config", "cli_compat", "credential_store", "credential"}
    all_ok = all(check["ok"] for check in checks if check["name"] not in informational)
    output.emit_success(
        ctx, {"healthy": all_ok, "checks": checks}, render_human=lambda data: output.human.render_doctor(data["checks"])
    )
