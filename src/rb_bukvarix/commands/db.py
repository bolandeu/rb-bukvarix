from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer

from .. import output
from ..core import services
from ..core.db import build_indexes, build_lemma_postings, import_keywords, set_data_date
from ..core.db import normalize as db_normalize
from ..core.errors import InvalidArguments

app = output.group("Обслуживание базы данных (не часть обычного поиска — см. 'bukvarix search --help').")


@app.command("set-path")
@output.guard
def set_path(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="Путь к файлу .duckdb для команд search/freq/stats/db."),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Сохранить путь к файлу базы данных (переопределяется переменной окружения BUKVARIX_DB_PATH).

    Example: {cli} db set-path /Volumes/Data/bukvarix/rb_bukvarix.duckdb
    """
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
        ..., exists=True, readable=True, help="Путь к TSV с ключевыми словами (phrase\\tfreq, без заголовка)"
    ),
    data_date: str | None = typer.Option(
        None, "--data-date", help="Дата выгрузки данных, YYYY-MM-DD (по умолчанию — дата изменения TSV)."
    ),
    confirm: bool = output.ConfirmOption,
    dry_run: bool = output.DryRunOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Импортировать региональный TSV Bukvarix, заменив текущее содержимое базы.

    Деструктивно: выполняет CREATE OR REPLACE TABLE — существующая таблица
    keywords (и всё, что построили над ней normalize/reindex) пропадает
    после запуска. Сделайте бэкап файла базы заранее (см. AGENTS.md) —
    автоматического бэкапа нет. Дата данных (--data-date, иначе дата
    изменения TSV) сохраняется в базе и отдаётся в 'freq' как data_date.

    Example: {cli} db import-keywords msk.tsv --data-date 2026-08-01 --dry-run
    """
    try:
        parsed_date = dt.date.fromisoformat(data_date) if data_date else None
    except ValueError:
        raise InvalidArguments(f"--data-date must be YYYY-MM-DD, got {data_date!r}.") from None
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
    result = import_keywords(db_path, path, data_date=parsed_date)
    output.emit_success(
        ctx,
        result,
        render_human=lambda d: output.human.echo(
            f"Done: {d['raw_rows']:,} raw rows -> {d['deduped_rows']:,} after "
            f"removing {d['removed_permutations']:,} word-order duplicates."
        ),
    )


@app.command("set-data-date")
@output.guard
def set_data_date_cmd(
    ctx: typer.Context,
    date: str = typer.Argument(..., help="Дата выгрузки данных Bukvarix, YYYY-MM-DD."),
    confirm: bool = output.ConfirmOption,
    dry_run: bool = output.DryRunOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Записать дату данных в уже импортированную базу, без переимпорта.

    Для баз, импортированных до 0.4.0 (у них 'freq' отдаёт data_date: null),
    или чтобы исправить дату. Создаёт таблицу dataset_meta, если её нет;
    повторный вызов перезаписывает дату. Остальные таблицы не трогает.

    Example: {cli} db set-data-date 2026-08-01 --confirm
    """
    try:
        parsed_date = dt.date.fromisoformat(date)
    except ValueError:
        raise InvalidArguments(f"DATE must be YYYY-MM-DD, got {date!r}.") from None
    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)
    if output.is_dry_run(ctx):
        output.emit_success(
            ctx,
            {"db_path": str(db_path), "would_set_data_date": parsed_date.isoformat()},
            render_human=lambda d: output.human.echo(
                f"Dry run: would set data_date to {d['would_set_data_date']} in {d['db_path']}."
            ),
        )
        return
    output.require_confirmation(ctx, f"This sets data_date to {parsed_date} in {db_path}", confirm)
    previous = set_data_date(db_path, parsed_date)
    output.emit_success(
        ctx,
        {"db_path": str(db_path), "data_date": parsed_date.isoformat(), "previous_data_date": previous},
        render_human=lambda d: output.human.echo(
            f"Done: data_date {d['previous_data_date'] or 'not set'} -> {d['data_date']}."
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
    """Добавить ключи сопоставления content_signature (стоп-слова) и lemma_signature (pymorphy3).

    Меняет действующую базу на месте (ALTER TABLE + UPDATE), на полном
    датасете может занять несколько минут. Сделайте бэкап файла базы заранее.

    Example: {cli} db normalize --dry-run
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
    """Построить индексы на signature/content_signature/lemma_signature — отдельно от import/normalize.

    Идемпотентно (CREATE INDEX IF NOT EXISTS), но может занять больше
    10 минут и примерно удваивает размер файла базы — сделайте бэкап
    заранее, если место на диске или время ограничены.

    Example: {cli} db reindex --dry-run
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


@app.command("build-postings")
@output.guard
def build_postings_cmd(
    ctx: typer.Context,
    temp_dir: Path | None = typer.Option(
        None,
        "--temp-dir",
        help="Каталог для промежуточной базы и временных файлов (по умолчанию — рядом с базой; можно другой диск).",
    ),
    confirm: bool = output.ConfirmOption,
    dry_run: bool = output.DryRunOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Построить инвертированный индекс лемм (lemma_postings) для быстрого search.

    Нужен после normalize; пересобирать после каждого import-keywords/normalize
    (они сбрасывают постинги, и search до пересборки работает полным сканом).
    Долго и растит файл базы на несколько ГБ; промежуточные файлы (десятки ГБ)
    пишутся в --temp-dir и удаляются в конце. Сделайте бэкап файла базы заранее
    (см. AGENTS.md).

    Example: {cli} db build-postings --temp-dir /Volumes/Data/bukvarix/tmp --dry-run
    """
    db_path = services.resolve_db_path()
    services.require_db_ready(db_path)
    if output.is_dry_run(ctx):
        output.emit_success(
            ctx,
            {"db_path": str(db_path)},
            render_human=lambda d: output.human.echo(f"Dry run: would (re)build lemma_postings in {d['db_path']}."),
        )
        return
    output.require_confirmation(
        ctx, f"This (re)builds lemma_postings in {db_path} (slow, grows the file by several GB)", confirm
    )
    output.vecho(ctx, f"Building lemma_postings on {db_path} ...")
    result = build_lemma_postings(db_path, temp_directory=temp_dir, progress=lambda m: output.vecho(ctx, m))
    output.emit_success(
        ctx,
        result,
        render_human=lambda d: output.human.echo(
            f"Done: {d['postings_rows']:,} postings over {d['keywords_rows']:,} phrases."
        ),
    )
