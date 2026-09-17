from __future__ import annotations

from pathlib import Path

import typer

from .. import output
from ..core import services
from ..core.db import build_indexes, import_keywords
from ..core.db import normalize as db_normalize

app = output.group("Database maintenance (not part of normal querying — see 'bukvarix search --help').")


@app.command("set-path")
@output.guard
def set_path(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="Path to the .duckdb file to use for search/freq/stats/db commands."),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Persist the database file path (overridden by the BUKVARIX_DB_PATH env var)."""
    resolved = path.expanduser().resolve()
    services.set_user_db_path(resolved)
    output.emit_success(
        ctx,
        {"db_path": str(resolved)},
        render_human=lambda d: output.human.echo(f"Set database path to {d['db_path']}"),
    )


@app.command("import-keywords")
@output.guard
def import_keywords_cmd(
    ctx: typer.Context,
    path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Path to keywords TSV (phrase\\tfreq, no header)"
    ),
    confirm: bool = output.ConfirmOption,
    dry_run: bool = output.DryRunOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Import a Bukvarix regional keywords TSV, replacing the current database contents.

    Destructive: does CREATE OR REPLACE TABLE — the existing keywords table
    (and any normalize/reindex work on it) is gone once this runs. Back up
    the database file first (see AGENTS.md); there is no automatic backup.
    """
    db_path = services.resolve_db_path()
    if output.is_dry_run(ctx):
        output.emit_success(
            ctx,
            {"would_import": str(path), "db_path": str(db_path)},
            render_human=lambda d: output.human.echo(
                f"Dry run: would replace the keywords table in {d['db_path']} from {d['would_import']}."
            ),
        )
        return
    output.require_confirmation(
        ctx, f"This replaces the entire keywords table in {db_path} — make sure you have a backup", confirm
    )
    output.vecho(ctx, f"Importing {path} into {db_path} ...")
    result = import_keywords(db_path, path)
    output.emit_success(
        ctx,
        result,
        render_human=lambda d: output.human.echo(
            f"Done: {d['raw_rows']:,} raw rows -> {d['deduped_rows']:,} after "
            f"removing {d['removed_permutations']:,} word-order duplicates."
        ),
    )


@app.command("normalize")
@output.guard
def normalize_cmd(
    ctx: typer.Context,
    confirm: bool = output.ConfirmOption,
    dry_run: bool = output.DryRunOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Add content_signature (stop-words) and lemma_signature (pymorphy3) match keys.

    Mutates the live database in place (ALTER TABLE + UPDATE) and can take
    several minutes on the full dataset. Back up the database file first.
    """
    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)
    if output.is_dry_run(ctx):
        output.emit_success(
            ctx,
            {"db_path": str(db_path)},
            render_human=lambda d: output.human.echo(
                f"Dry run: would add content_signature/lemma_signature columns to {d['db_path']}."
            ),
        )
        return
    output.require_confirmation(ctx, f"This mutates {db_path} in place — make sure you have a backup", confirm)
    output.vecho(ctx, f"Building content_signature + lemma_signature on {db_path} ...")
    result = db_normalize(db_path)
    output.emit_success(
        ctx,
        result,
        render_human=lambda d: output.human.echo(
            f"Done: lemmatized {d['distinct_words_lemmatized']:,} distinct words."
        ),
    )


@app.command("reindex")
@output.guard
def reindex_cmd(
    ctx: typer.Context,
    confirm: bool = output.ConfirmOption,
    dry_run: bool = output.DryRunOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Build indexes on signature/content_signature/lemma_signature, separately from import/normalize.

    Idempotent (CREATE INDEX IF NOT EXISTS) but can take well over 10
    minutes and roughly doubles the database file size — back it up first
    if disk space or time budget is tight.
    """
    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)
    if output.is_dry_run(ctx):
        output.emit_success(
            ctx,
            {"db_path": str(db_path)},
            render_human=lambda d: output.human.echo(f"Dry run: would build/refresh indexes on {d['db_path']}."),
        )
        return
    output.require_confirmation(ctx, f"This builds indexes on {db_path} (slow, grows the file substantially)", confirm)
    output.vecho(ctx, f"Building indexes on {db_path} ...")
    build_indexes(db_path)
    output.emit_success(ctx, {"db_path": str(db_path)}, render_human=lambda d: output.human.echo("Done."))
