"""The root Typer app: global options, the command groups and the optional commands a generated package has, wired
together, and the console-script entry point.
"""

from __future__ import annotations

import typer

from . import output
from .commands import auth, db, doctor, freq, search, site, stats
from .core.version import DEV_VERSION, cli_name, installed_version, optional_module, package_summary

_PACKAGE_NAME = __name__.split(".")[0]
# Optional parts (copier questions, dropped by _exclude): each one ships a module that registers its
# commands and global options. Probed at runtime, so this file needs no template logic.
PARTS = ("project", "skill", "describe")

# A bare `<cli>` is the discovery entry point for humans and agents alike:
# name + version + description, then every command with the first line of
# its docstring. No `no_args_is_help` (that exits 2) — main_options() below
# prints this and exits 0. Shell completion stays enabled.
app = typer.Typer(
    cls=output.RootGroup,  # a usage error under --json is the error envelope
    help=f"{cli_name()} {installed_version() or DEV_VERSION} — {package_summary() or 'Локальный аналог Wordstat поверх дампов ключевых слов Bukvarix'}",
)

app.add_typer(auth.app, name="auth")  # `auth status` in every CLI; login/logout only with credentials
app.add_typer(db.app, name="db")
app.command(name="doctor")(doctor.doctor)
app.command(name="search")(search.search)
app.command(name="freq")(freq.freq)
app.command(name="site")(site.site)
app.command(name="stats")(stats.stats)
for part in PARTS:
    if module := optional_module(f"{_PACKAGE_NAME}.commands.{part}"):
        module.register(app)


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(installed_version() or DEV_VERSION)
    raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_options(
    ctx: typer.Context,
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
) -> None:
    ctx.ensure_object(dict)  # a part's global option (--project) may have put its value here already
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
