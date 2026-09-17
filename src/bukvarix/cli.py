from __future__ import annotations

from importlib import resources

import typer

from . import output
from .commands import auth, db, doctor, freq, init, search, skill, stats, status
from .commands import config as config_cmd
from .core.version import DEV_VERSION, installed_version, package_summary

_PACKAGE_NAME = __name__.split(".")[0]

# A bare `<cli>` is the discovery entry point for humans and agents alike:
# name + version + description, then every command with the first line of
# its docstring. No `no_args_is_help` (that exits 2) — main_options() below
# prints this and exits 0. Shell completion stays enabled.
app = typer.Typer(
    help=f"{_PACKAGE_NAME} {installed_version() or DEV_VERSION} — {package_summary() or 'Локальный аналог Wordstat поверх дампов ключевых слов Bukvarix'}"
)

app.add_typer(config_cmd.app, name="config")
app.add_typer(auth.app, name="auth")
app.add_typer(skill.app, name="skill")
app.add_typer(db.app, name="db")

app.command(name="init")(init.init)
app.command(name="status")(status.status)
app.command(name="doctor")(doctor.doctor)
app.command(name="search")(search.search)
app.command(name="freq")(freq.freq)
app.command(name="stats")(stats.stats)


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(installed_version() or DEV_VERSION)
    raise typer.Exit()


def _ai_callback(value: bool) -> None:
    """Print the bundled agent skill doc to stdout.

    For agents that can execute this CLI but cannot read files from disk
    (e.g. a sandboxed shell with no access to a SKILL.md on the filesystem),
    this is the fallback way to get full usage guidance.
    """
    if not value:
        return
    content = resources.files(f"{_PACKAGE_NAME}.data").joinpath("SKILL.md").read_text()
    typer.echo(content)
    raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_options(
    ctx: typer.Context,
    project: str = typer.Option(None, "--project", help="Путь к корню проекта."),
    json_output: bool = typer.Option(False, "--json", help="Вывести результат в машиночитаемом формате JSON."),
    quiet: bool = typer.Option(False, "--quiet", help="Подавить второстепенный вывод."),
    verbose: bool = typer.Option(False, "--verbose", help="Вывести дополнительную диагностику."),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Никогда не спрашивать интерактивно — завершаться с ошибкой."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Показать, что произойдёт, без внесения изменений."),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Показать версию и выйти."
    ),
    ai: bool = typer.Option(
        False,
        "--ai",
        callback=_ai_callback,
        is_eager=True,
        help="Вывести документацию для AI-агентов (встроенный skill-файл) и выйти.",
    ),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["project"] = project
    ctx.obj["json"] = json_output
    ctx.obj["quiet"] = quiet
    ctx.obj["verbose"] = verbose
    ctx.obj["non_interactive"] = non_interactive
    ctx.obj["dry_run"] = dry_run
    output.configure_logging(verbose)
    output.help_if_no_command(ctx)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
