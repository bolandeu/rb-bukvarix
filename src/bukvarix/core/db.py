from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb


def connect(db_path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path), read_only=read_only)


def _fetchone(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> tuple:
    """`.fetchone()`, asserting the row exists — safe for aggregate queries
    (COUNT/SUM), which DuckDB always returns exactly one row for, even over
    zero matching rows. Narrows the `tuple | None` return type for callers
    that unpack/subscript it unconditionally.
    """
    result = con.execute(sql, params).fetchone() if params is not None else con.execute(sql).fetchone()
    assert result is not None  # noqa: S101 -- type-narrowing an aggregate query's guaranteed row, not a security check
    return result


def import_keywords(db_path: Path, tsv_path: Path) -> dict:
    """Import a Bukvarix regional keywords TSV (phrase\\tfreq, no header).

    Bukvarix TSVs are not quoted/escaped, so quote='' is required or a
    literal '"' inside a phrase would corrupt row parsing.

    Word-order duplicates ("таможенное оформление авто" / "авто таможенное
    оформление" / ...) are collapsed to a single row: phrases are grouped by
    a signature (lowercased words, sorted), and only the highest-frequency
    phrase per group is kept — the rest are dropped from the table (not just
    hidden), so an exact lookup on a non-canonical wording will no longer
    resolve after import. That's intentional: the caller decided those
    variants are noise, same as Wordstat already collapsing them in its own
    output.
    """
    con = connect(db_path)
    try:
        con.execute("PRAGMA preserve_insertion_order = false")
        con.execute(
            """
            CREATE OR REPLACE TABLE keywords AS
            SELECT * FROM read_csv(
                ?,
                delim='\t', header=false, quote='',
                columns={'phrase': 'VARCHAR', 'freq_msk_exact': 'BIGINT'}
            )
            """,
            [str(tsv_path)],
        )
        raw_rows = _fetchone(con, "SELECT count(*) FROM keywords")[0]

        con.execute("ALTER TABLE keywords ADD COLUMN signature VARCHAR")
        con.execute(
            """
            UPDATE keywords
            SET signature = list_aggregate(list_sort(str_split(lower(phrase), ' ')), 'string_agg', ' ')
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE keywords AS
            SELECT phrase, freq_msk_exact, signature
            FROM (
                SELECT *,
                    row_number() OVER (
                        PARTITION BY signature ORDER BY freq_msk_exact DESC, phrase
                    ) AS rn
                FROM keywords
            )
            WHERE rn = 1
            """
        )
        deduped_rows = _fetchone(con, "SELECT count(*) FROM keywords")[0]

        # keywords was recreated, so rowids changed: postings pointing at
        # the old table would silently return wrong phrases.
        drop_lemma_postings(con)
        con.execute("CHECKPOINT")
        return {
            "raw_rows": raw_rows,
            "deduped_rows": deduped_rows,
            "removed_permutations": raw_rows - deduped_rows,
        }
    finally:
        con.close()


def build_indexes(db_path: Path) -> None:
    """Build indexes for all three lookup tiers, separately from import/normalize.

    Split out because CREATE (UNIQUE) INDEX on a ~127M-row VARCHAR column
    turned out to be the slow part of import on this hardware (DuckDB's ART
    index build is largely single-threaded) — dedup itself finishes in
    minutes, indexing alone took 30+ minutes and counting before being
    killed. Keeping it separate means import produces usable data quickly;
    this can be run (and re-timed) independently. No index on `phrase`
    itself: nothing queries it directly, only the three signature columns
    (see _exact_lookup()). content_signature/lemma_signature indexes are
    skipped with a warning if those columns don't exist yet (normalize()
    hasn't run) rather than failing.
    """
    con = connect(db_path)
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_keywords_signature ON keywords(signature)")

        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'keywords'"
            ).fetchall()
        }
        if "content_signature" in cols:
            con.execute("CREATE INDEX IF NOT EXISTS idx_keywords_content_signature ON keywords(content_signature)")
        if "lemma_signature" in cols:
            con.execute("CREATE INDEX IF NOT EXISTS idx_keywords_lemma_signature ON keywords(lemma_signature)")

        con.execute("CHECKPOINT")
    finally:
        con.close()


def _has_column(con: duckdb.DuckDBPyConnection, table: str, column: str) -> bool:
    return bool(
        con.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?", [table, column]
        ).fetchone()
    )


def _has_table(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]).fetchone())


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def drop_lemma_postings(con: duckdb.DuckDBPyConnection) -> None:
    """Invalidate the postings: they reference keywords by rowid, which is
    only stable until keywords is recreated or its lemma_signature changes.
    """
    con.execute("DROP TABLE IF EXISTS lemma_postings")
    con.execute("DROP TABLE IF EXISTS lemma_postings_meta")


def build_lemma_postings(
    db_path: Path,
    *,
    temp_directory: Path | None = None,
    chunk_rows: int = 20_000_000,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Build the inverted index `lemma_postings(lemma, kw_id, freq)` for search_smart().

    One row per (lemma, phrase containing it), sorted by lemma so a
    `WHERE lemma = ?` lookup reads only the matching row groups (zone maps —
    no ART index: on ~480M VARCHAR rows that would take hours and tens of GB,
    see docs/build.md problems 1 and 3). `kw_id` is keywords.rowid, not an
    explicit column: adding one would mean an UPDATE over 113.7M rows of a
    table with three ART indexes. `freq` is denormalized from keywords so
    count/sum/top-N come from the postings alone and keywords is only
    touched for the final N phrases (by rowid, ~40ms instead of a ~1s join).

    Tokens are str_split(lemma_signature, ' ') — the same lemmas
    _lemmatize_words_list() produces for query words (both via word_lemmas,
    coalesce(lemma, word)), so a query lemma matches a posting byte-for-byte.
    All lemmas are indexed, stop words included, so `+в` keeps working.

    lemma_postings_meta.keywords_rows guards against drift: search_smart()
    ignores the postings if keywords' row count no longer matches.

    Built in lemma-range chunks, not one ORDER BY: a single sort of the
    ~484M postings OOM'd at 9.3GB (docs/build.md problem 6). Instead the
    unsorted postings go to a staging DuckDB file in `temp_directory`
    (default: next to the db; point it at another disk to spare this one),
    the lemma alphabet is cut into ranges of ~`chunk_rows` postings each,
    and each range is inserted already sorted. Ranges are appended in
    lemma order, so the final table is globally sorted with only
    ~`chunk_rows` rows in any one sort. The staging file is deleted after.
    """
    from bukvarix.core.errors import CliError

    say = progress or (lambda _msg: None)
    work_dir = temp_directory or db_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    staging = work_dir / f"{db_path.stem}.postings_staging.duckdb"
    staging.unlink(missing_ok=True)

    con = connect(db_path)
    try:
        if not _has_column(con, "keywords", "lemma_signature"):
            raise CliError(
                "DATABASE_NOT_READY",
                "keywords has no lemma_signature column yet — postings are built from it.",
                action="Run 'bukvarix db normalize' first.",
            )
        con.execute("SET memory_limit = '10GB'")
        # SET/ATTACH take no bound parameters — quote the paths as SQL literals.
        con.execute(f"SET temp_directory = {_sql_literal(str(work_dir))}")
        con.execute(f"ATTACH {_sql_literal(str(staging))} AS staging")

        say(f"staging unsorted postings in {staging} ...")
        con.execute("PRAGMA preserve_insertion_order = false")
        con.execute(
            """
            CREATE TABLE staging.raw AS
            SELECT tok.lemma AS lemma, k.rowid AS kw_id, k.freq_msk_exact AS freq
            FROM keywords k, UNNEST(str_split(k.lemma_signature, ' ')) AS tok(lemma)
            WHERE tok.lemma != ''
            """
        )
        # Lower bound of each chunk: cut the sorted lemma list wherever the
        # running posting count crosses a multiple of chunk_rows. A lemma
        # bigger than chunk_rows ('в': ~15M) simply gets a chunk to itself.
        bounds = [
            r[0]
            for r in con.execute(
                """
                SELECT min(lemma) FROM (
                    SELECT lemma, floor((sum(n) OVER (ORDER BY lemma) - n) / ?) AS chunk
                    FROM (SELECT lemma, count(*) AS n FROM staging.raw GROUP BY lemma)
                ) GROUP BY chunk ORDER BY 1
                """,
                [chunk_rows],
            ).fetchall()
        ]

        drop_lemma_postings(con)
        con.execute("CREATE TABLE lemma_postings (lemma VARCHAR, kw_id BIGINT, freq BIGINT)")
        con.execute("PRAGMA preserve_insertion_order = true")  # keep each chunk's ORDER BY on disk
        for i, lo in enumerate(bounds):
            hi = bounds[i + 1] if i + 1 < len(bounds) else None
            say(f"chunk {i + 1}/{len(bounds)}: lemmas from {lo!r}")
            con.execute(
                f"""
                INSERT INTO lemma_postings
                SELECT lemma, kw_id, freq FROM staging.raw
                WHERE lemma >= ? {"AND lemma < ?" if hi is not None else ""}
                ORDER BY lemma, kw_id
                """,
                [lo] if hi is None else [lo, hi],
            )
        con.execute("DETACH staging")
        con.execute(
            """
            CREATE TABLE lemma_postings_meta AS
            SELECT (SELECT count(*) FROM keywords) AS keywords_rows,
                   (SELECT count(*) FROM lemma_postings) AS postings_rows,
                   now()::TIMESTAMP AS built_at  -- plain TIMESTAMP: TIMESTAMPTZ needs pytz to fetch in Python
            """
        )
        keywords_rows, postings_rows = _fetchone(con, "SELECT keywords_rows, postings_rows FROM lemma_postings_meta")
        con.execute("CHECKPOINT")
        return {"keywords_rows": keywords_rows, "postings_rows": postings_rows, "chunks": len(bounds)}
    finally:
        con.close()
        staging.unlink(missing_ok=True)
        staging.with_name(staging.name + ".wal").unlink(missing_ok=True)


def _postings_usable(con: duckdb.DuckDBPyConnection) -> bool:
    """True if lemma_postings exists and was built over the current keywords table."""
    if not _has_table(con, "lemma_postings_meta"):
        return False
    meta = con.execute("SELECT keywords_rows FROM lemma_postings_meta").fetchone()
    current = _fetchone(con, "SELECT count(*) FROM keywords")[0]
    if meta is None or meta[0] != current:
        import sys

        print(
            f"warning: lemma_postings is stale (built over {meta[0] if meta else '?'} rows, keywords has "
            f"{current}) — falling back to a full scan; rebuild with 'bukvarix db build-postings'",
            file=sys.stderr,
        )
        return False
    return True


def _search_postings(con: duckdb.DuckDBPyConnection, required: list[str], excluded: list[str], limit: int) -> dict:
    """AND over the query lemmas as an intersection of posting lists, minus excluded.

    A word with no postings means no phrase contains it, so the result is
    empty — no substring fallback (that was the scan path's defect: '%авто%'
    also matched 'автосервис').
    """
    required = list(dict.fromkeys(required))
    excluded = [w for w in dict.fromkeys(excluded) if w not in required]
    empty = {"results": [], "total_count": 0, "total_freq_sum": 0}

    counts = dict(
        con.execute(
            "SELECT lemma, count(*) FROM lemma_postings WHERE lemma IN (SELECT unnest(?)) GROUP BY lemma", [required]
        ).fetchall()
    )
    if len(counts) < len(required):
        return empty
    required.sort(key=lambda w: counts[w])  # rarest first: smallest intermediate sets

    posting = "SELECT kw_id, freq FROM lemma_postings WHERE lemma = ?"
    # INTERSECT/EXCEPT are set operations (distinct), and INTERSECT binds
    # tighter than EXCEPT: (w1 ∩ w2 ∩ ...) − x1 − x2. Only placeholders are
    # interpolated; the words themselves go in as parameters.
    sql = "SELECT DISTINCT kw_id, freq FROM lemma_postings WHERE lemma = ?"
    sql += f" INTERSECT ({posting})" * (len(required) - 1)
    sql += f" EXCEPT ({posting})" * len(excluded)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _hits AS {sql}", [*required, *excluded])

    total_count, total_freq_sum = _fetchone(con, "SELECT count(*), coalesce(sum(freq), 0) FROM _hits")
    top = con.execute("SELECT kw_id, freq FROM _hits ORDER BY freq DESC, kw_id LIMIT ?", [limit]).fetchall()
    phrases = dict(
        con.execute(
            "SELECT rowid, phrase FROM keywords WHERE rowid IN (SELECT unnest(?))", [[kw_id for kw_id, _ in top]]
        ).fetchall()
    )
    con.execute("DROP TABLE _hits")
    return {
        "results": [{"phrase": phrases[kw_id], "freq_msk_exact": freq} for kw_id, freq in top],
        "total_count": total_count,
        "total_freq_sum": total_freq_sum,
    }


def _signature(phrase: str) -> str:
    """Mirror of the SQL signature used at import time: lowercased words, sorted.

    Lets a query in any word order resolve to the canonical (post-dedup)
    phrase, e.g. "оформление таможенное" -> "таможенное оформление".
    """
    return " ".join(sorted(phrase.lower().split(" ")))


def _content_signature(phrase: str) -> str:
    from bukvarix.core.normalize import STOPWORDS

    stopwords = set(STOPWORDS)
    return " ".join(sorted(w for w in phrase.lower().split(" ") if w not in stopwords))


def _lemmatize_words_list(con: duckdb.DuckDBPyConnection, words: list[str]) -> list[str]:
    """Lemma for each word (lowercased already expected). Falls back to the
    word itself if word_lemmas doesn't exist yet or the word wasn't seen at
    import time (rare words/typos/brands pymorphy3 or the corpus missed).
    """
    if not words:
        return []
    if not con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'word_lemmas'").fetchone():
        return list(words)
    rows = dict(con.execute("SELECT word, lemma FROM word_lemmas WHERE word IN (SELECT unnest(?))", [words]).fetchall())
    return [rows.get(w, w) for w in words]


def _query_lemmas(con: duckdb.DuckDBPyConnection, phrase: str) -> list[str] | None:
    """Per-word lemma for each word in `phrase`, in original order. None if
    word_lemmas doesn't exist yet (normalize() hasn't run) or phrase is empty.
    """
    words = [w for w in phrase.lower().split(" ") if w]
    if not words:
        return None
    if not con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'word_lemmas'").fetchone():
        return None
    return _lemmatize_words_list(con, words)


def _lemma_signature(con: duckdb.DuckDBPyConnection, phrase: str) -> str | None:
    lemmas = _query_lemmas(con, phrase)
    return " ".join(sorted(lemmas)) if lemmas else None


def _exact_lookup(con: duckdb.DuckDBPyConnection, query: str) -> tuple[dict | None, str | None]:
    """Tiered lookup: signature -> content_signature -> lemma_signature (see PRD §4.1).

    Returns (row, matched_via) so callers/agents know whether the result was
    an exact word-order match or found via a looser (stop-word/lemma) tier.
    """
    row = con.execute("SELECT phrase, freq_msk_exact FROM keywords WHERE signature = ?", [_signature(query)]).fetchone()
    if row:
        return {"phrase": row[0], "freq_msk_exact": row[1]}, "exact"

    has_content_sig = con.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'keywords' AND column_name = 'content_signature'"
    ).fetchone()
    if has_content_sig:
        row = con.execute(
            "SELECT phrase, freq_msk_exact FROM keywords WHERE content_signature = ?",
            [_content_signature(query)],
        ).fetchone()
        if row:
            return {"phrase": row[0], "freq_msk_exact": row[1]}, "stopwords"

    lemma_sig = _lemma_signature(con, query)
    if lemma_sig:
        row = con.execute(
            "SELECT phrase, freq_msk_exact FROM keywords WHERE lemma_signature = ?", [lemma_sig]
        ).fetchone()
        if row:
            return {"phrase": row[0], "freq_msk_exact": row[1]}, "lemma"

    return None, None


def search_smart(db_path: Path, query: str, limit: int, *, force_scan: bool = False) -> dict:
    """Default search behavior — mirrors how real Wordstat answers a query:
    both the phrase's own frequency (`self`, via the tiered exact lookup)
    AND a depth list of related phrases (`results`), in one response.

    The depth list is a strict AND over the query's lemmas (word order AND
    word form insensitive), computed one of two ways — `engine` in the
    result says which:

    - "postings": intersection of lemma_postings lists (see
      build_lemma_postings()). Whole-word match, sub-second.
    - "scan": AND-of-ILIKE over lemma_signature (or `phrase` if normalize()
      hasn't run), 16-30s+ on 113.7M rows. Used when the postings are
      missing or stale, or on request (`--mode scan`). Substring match:
      '%авто%' also hits 'автосервис', so it can return more than postings.

    ⚠️ FTS/BM25 was tried here (see docs/build.md problem 5) and reverted: BM25
    is OR-semantics (ranks phrases with ANY query word), so a query
    containing a common word (e.g. the preposition "в") matched millions
    of largely-unrelated rows — both slower than the plain AND scan AND
    wrong. Postings keep AND semantics: an intersection only narrows.

    Also recognizes +word/-word operators (see query_syntax.py) right here
    in the default, lemmatized — unlike --mode query, which is literal.
    """
    from bukvarix.core.query_syntax import parse_query

    parsed = parse_query(query)
    con = connect(db_path, read_only=True)
    try:
        if parsed.excluded:
            self_result = None
        else:
            self_row, matched_via = _exact_lookup(con, query)
            self_result = {**self_row, "matched_via": matched_via} if self_row else None

        has_lemma_col = _fetchone(
            con,
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'keywords' AND column_name = 'lemma_signature'",
        )[0]
        col = "lemma_signature" if has_lemma_col else "phrase"

        required = _lemmatize_words_list(con, parsed.required) if has_lemma_col else parsed.required
        excluded = _lemmatize_words_list(con, parsed.excluded) if has_lemma_col else parsed.excluded

        use_postings = has_lemma_col and not force_scan and _postings_usable(con)
        engine = "postings" if use_postings else "scan"

        if not required:
            return {"self": self_result, "results": [], "total_count": 0, "total_freq_sum": 0, "engine": engine}

        if use_postings:
            # Like Wordstat, a stop word only counts when forced with `+`:
            # "авто в растаможка" must find everything "растаможка авто"
            # does (812 vs 1824 phrases otherwise). The scan path gets this
            # for free — '%в%' matches almost any phrase. A query made only
            # of stop words keeps them, or it would match nothing.
            from bukvarix.core.normalize import STOPWORDS

            stop = set(STOPWORDS) - set(parsed.forced)
            content = [lem for w, lem in zip(parsed.required, required, strict=True) if w not in stop]
            return {
                "self": self_result,
                **_search_postings(con, content or required, excluded, limit),
                "engine": engine,
            }

        conditions = [f"{col} NOT ILIKE '%' || ? || '%'" for _ in excluded]
        params: list = list(excluded)
        for w in required:
            conditions.append(f"{col} ILIKE '%' || ? || '%'")
            params.append(w)
        where = " AND ".join(conditions)

        total_count, total_freq_sum = _fetchone(
            con, f"SELECT count(*), coalesce(sum(freq_msk_exact), 0) FROM keywords WHERE {where}", params
        )
        sql = f"SELECT phrase, freq_msk_exact FROM keywords WHERE {where} ORDER BY freq_msk_exact DESC LIMIT ?"
        rows = con.execute(sql, [*params, limit]).fetchall()
        results = [{"phrase": r[0], "freq_msk_exact": r[1]} for r in rows]
        return {
            "self": self_result,
            "results": results,
            "total_count": total_count,
            "total_freq_sum": total_freq_sum,
            "engine": engine,
        }
    finally:
        con.close()


def search_query(db_path: Path, query: str, limit: int) -> list[dict]:
    """search --mode query: parses +word/-word operators (see query_syntax.py)."""
    from bukvarix.core.query_syntax import parse_query

    parsed = parse_query(query)
    conditions = []
    params: list = []
    for w in parsed.required:
        conditions.append("phrase ILIKE '%' || ? || '%'")
        params.append(w)
    for w in parsed.excluded:
        conditions.append("phrase NOT ILIKE '%' || ? || '%'")
        params.append(w)
    if not conditions:
        return []
    where = " AND ".join(conditions)
    sql = f"SELECT phrase, freq_msk_exact FROM keywords WHERE {where} ORDER BY freq_msk_exact DESC LIMIT ?"
    params.append(limit)
    con = connect(db_path, read_only=True)
    try:
        rows = con.execute(sql, params).fetchall()
        return [{"phrase": r[0], "freq_msk_exact": r[1]} for r in rows]
    finally:
        con.close()


def search(db_path: Path, query: str, mode: str, limit: int) -> list[dict]:
    if mode == "query":
        return search_query(db_path, query, limit)
    con = connect(db_path, read_only=True)
    try:
        if mode == "exact":
            row, matched_via = _exact_lookup(con, query)
            return [{**row, "matched_via": matched_via}] if row else []
        elif mode == "prefix":
            sql = "SELECT phrase, freq_msk_exact FROM keywords WHERE starts_with(phrase, ?) ORDER BY freq_msk_exact DESC LIMIT ?"
            params = [query, limit]
        elif mode == "words":
            # every query word present anywhere in the phrase, any order/distance
            # ("купить диван" also matches "диван недорого купить")
            words = [w for w in query.lower().split(" ") if w]
            if not words:
                return []
            conditions = " AND ".join("phrase ILIKE '%' || ? || '%'" for _ in words)
            sql = f"SELECT phrase, freq_msk_exact FROM keywords WHERE {conditions} ORDER BY freq_msk_exact DESC LIMIT ?"
            params = [*words, limit]
        else:  # contains
            sql = "SELECT phrase, freq_msk_exact FROM keywords WHERE phrase ILIKE '%' || ? || '%' ORDER BY freq_msk_exact DESC LIMIT ?"
            params = [query, limit]
        rows = con.execute(sql, params).fetchall()
        return [{"phrase": r[0], "freq_msk_exact": r[1]} for r in rows]
    finally:
        con.close()


def freq(db_path: Path, query: str) -> dict | None:
    con = connect(db_path, read_only=True)
    try:
        row, matched_via = _exact_lookup(con, query)
        return {**row, "matched_via": matched_via} if row else None
    finally:
        con.close()


def _lemma_signatures_batch(con: duckdb.DuckDBPyConnection, phrases: list[str]) -> dict[str, str | None]:
    """Batched version of _lemma_signature(): one word_lemmas lookup for the
    union of all words across `phrases`, instead of one lookup per phrase.
    """
    phrase_words = {p: [w for w in p.lower().split(" ") if w] for p in phrases}
    if not con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'word_lemmas'").fetchone():
        return dict.fromkeys(phrases)

    all_words = list({w for words in phrase_words.values() for w in words})
    lemma_by_word = (
        dict(
            con.execute("SELECT word, lemma FROM word_lemmas WHERE word IN (SELECT unnest(?))", [all_words]).fetchall()
        )
        if all_words
        else {}
    )

    return {
        p: " ".join(sorted(lemma_by_word.get(w, w) for w in words)) if words else None
        for p, words in phrase_words.items()
    }


def freq_batch(db_path: Path, queries: list[str]) -> dict[str, dict | None]:
    """Batched version of freq(): one connection and one SQL query per tier
    (signature -> content_signature -> lemma_signature) for the whole list,
    instead of a connection + up to 3 queries per phrase. Meant for callers
    that need exact frequency for many phrases at once (e.g. backfilling a
    collected keyword pool) - the per-phrase freq() pays a full connect() to
    the multi-GB DB file for every single lookup, which dominates over the
    (indexed, sub-millisecond) query itself.

    Returns {original_query: {"phrase", "freq_msk_exact", "matched_via"} | None},
    one entry per unique query (duplicates in `queries` collapse to one key).
    """
    if not queries:
        return {}

    con = connect(db_path, read_only=True)
    try:
        result: dict[str, dict | None] = dict.fromkeys(dict.fromkeys(queries))
        remaining = list(result)

        # Tier 1: signature (exact word-order-insensitive match)
        by_sig: dict[str, list[str]] = {}
        for q in remaining:
            by_sig.setdefault(_signature(q), []).append(q)
        rows = con.execute(
            "SELECT phrase, freq_msk_exact, signature FROM keywords WHERE signature IN (SELECT unnest(?))",
            [list(by_sig)],
        ).fetchall()
        for phrase, freq_val, sig in rows:
            for q in by_sig[sig]:
                result[q] = {"phrase": phrase, "freq_msk_exact": freq_val, "matched_via": "exact"}
        remaining = [q for q in remaining if result[q] is None]

        # Tier 2: content_signature (stop-words ignored)
        has_content_sig = (
            remaining
            and con.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'keywords' AND column_name = 'content_signature'"
            ).fetchone()
        )
        if has_content_sig:
            by_csig: dict[str, list[str]] = {}
            for q in remaining:
                by_csig.setdefault(_content_signature(q), []).append(q)
            rows = con.execute(
                "SELECT phrase, freq_msk_exact, content_signature FROM keywords WHERE content_signature IN (SELECT unnest(?))",
                [list(by_csig)],
            ).fetchall()
            for phrase, freq_val, csig in rows:
                for q in by_csig[csig]:
                    result[q] = {"phrase": phrase, "freq_msk_exact": freq_val, "matched_via": "stopwords"}
            remaining = [q for q in remaining if result[q] is None]

        # Tier 3: lemma_signature (word form ignored too)
        if remaining:
            lemma_sig_by_query = _lemma_signatures_batch(con, remaining)
            by_lsig: dict[str, list[str]] = {}
            for q, lsig in lemma_sig_by_query.items():
                if lsig:
                    by_lsig.setdefault(lsig, []).append(q)
            if by_lsig:
                rows = con.execute(
                    "SELECT phrase, freq_msk_exact, lemma_signature FROM keywords WHERE lemma_signature IN (SELECT unnest(?))",
                    [list(by_lsig)],
                ).fetchall()
                for phrase, freq_val, lsig in rows:
                    for q in by_lsig[lsig]:
                        result[q] = {"phrase": phrase, "freq_msk_exact": freq_val, "matched_via": "lemma"}

        return result
    finally:
        con.close()


def normalize(db_path: Path) -> dict:
    from bukvarix.core.normalize import normalize as _normalize

    con = connect(db_path)
    try:
        return _normalize(con)
    finally:
        con.close()


def stats(db_path: Path) -> dict:
    if not db_path.exists():
        # duckdb.connect(..., read_only=True) refuses to create a new file,
        # so there is nothing to open yet — report the all-zero state
        # instead of raising (stats is meant to work before any import).
        return {"db_path": str(db_path), "db_size_bytes": 0, "tables": [], "keywords_rows": 0}

    con = connect(db_path, read_only=True)
    try:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        row_count = _fetchone(con, "SELECT count(*) FROM keywords")[0] if "keywords" in tables else 0
    finally:
        con.close()
    return {
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size,
        "tables": tables,
        "keywords_rows": row_count,
    }
