"""The `init` command: create the project folder with its config file in the target directory."""

from __future__ import annotations

from pathlib import Path

import typer

from .. import output
from ..core import services


@output.guard
def init(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Имя проекта (по умолчанию — имя директории)."),
    force: bool = typer.Option(False, "--force", help="Перезаписать конфиг уже инициализированного проекта."),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Инициализировать проект: создаёт .bukvarix/config.toml в целевой директории.

    Не требуется для search/freq/stats/db — они работают с единой базой
    на весь компьютер (см. '{cli} db --help'). Это только для
    конфигурации на директорию (пока только cli.min_version), оставлено
    для единообразия со стандартным набором команд этого CLI.

    Example: {cli} init --name demo
    """
    target = Path(ctx.obj.get("project") or Path.cwd()).resolve()

    if not force and services.find_project_root(target) == target:
        output.vecho(ctx, f"Found existing config at {services.config_path(target)}")
        config = services.load_config(target)
        output.emit_success(
            ctx,
            {"project": config.name, "path": str(target), "already_initialized": True},
            render_human=lambda data: output.human.echo(
                f"Already initialized in {data['path']} (use --force to reset)."
            ),
        )
        return

    project_name = name or target.name
    config = services.init_project(target, project_name)
    output.emit_success(
        ctx,
        {"project": config.name, "path": str(target), "already_initialized": False},
        render_human=lambda data: output.human.key_values(
            f"Initialized project in {data['path']}", {"name": data["project"]}
        ),
    )
