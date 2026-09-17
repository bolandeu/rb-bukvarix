from __future__ import annotations

import os
from pathlib import Path

import duckdb


# Real, hardcoded default — not just ~/.local/share/bukvarix/bukvarix.duckdb.
# BUKVARIX_DB_PATH was set via ~/.zshrc when the DB was moved here, but that
# only loads for interactive shells that source it — any other process
# (a subprocess spawned by a different tool, a GUI app, a service) won't see
# it and silently falls back to this default. Point the default at the real
# file so it works even without the env var; BUKVARIX_DB_PATH remains an
# explicit override for anyone who wants a different location.
DEFAULT_DB_PATH = Path("/Users/bolandau/Projects/bukvarix/bukvarix.duckdb")


def db_path() -> Path:
    override = os.environ.get("BUKVARIX_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def import_keywords(tsv_path: Path) -> dict:
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
    con = connect()
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
        raw_rows = con.execute("SELECT count(*) FROM keywords").fetchone()[0]

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
        deduped_rows = con.execute("SELECT count(*) FROM keywords").fetchone()[0]

        con.execute("CHECKPOINT")
        return {
            "raw_rows": raw_rows,
            "deduped_rows": deduped_rows,
            "removed_permutations": raw_rows - deduped_rows,
        }
    finally:
        con.close()


def build_indexes() -> None:
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
    con = connect()
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_keywords_signature ON keywords(signature)")

        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'keywords'"
        ).fetchall()}
        if "content_signature" in cols:
            con.execute("CREATE INDEX IF NOT EXISTS idx_keywords_content_signature ON keywords(content_signature)")
        if "lemma_signature" in cols:
            con.execute("CREATE INDEX IF NOT EXISTS idx_keywords_lemma_signature ON keywords(lemma_signature)")

        con.execute("CHECKPOINT")
    finally:
        con.close()


def _signature(phrase: str) -> str:
    """Mirror of the SQL signature used at import time: lowercased words, sorted.

    Lets a query in any word order resolve to the canonical (post-dedup)
    phrase, e.g. "оформление таможенное" -> "таможенное оформление".
    """
    return " ".join(sorted(phrase.lower().split(" ")))


def _content_signature(phrase: str) -> str:
    from bukvarix.normalize import STOPWORDS

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
    rows = dict(con.execute(
        "SELECT word, lemma FROM word_lemmas WHERE word IN (SELECT unnest(?))", [words]
    ).fetchall())
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
    row = con.execute(
        "SELECT phrase, freq_msk_exact FROM keywords WHERE signature = ?", [_signature(query)]
    ).fetchone()
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


def search_smart(query: str, limit: int) -> dict:
    """Default search behavior — mirrors how real Wordstat answers a query:
    both the phrase's own frequency (`self`, via the tiered exact lookup)
    AND a depth list of related phrases (`results`), in one response.

    The depth list matches on `lemma_signature` (word order AND word form
    insensitive) via a strict AND-of-ILIKE scan — falls back to `phrase`
    if normalize() hasn't run yet, same degrade-gracefully approach as
    _exact_lookup(). This is looser than --mode words (which only ignores
    order, not form) — same accuracy tradeoff already accepted for `freq`.

    ⚠️ FTS/BM25 was tried here (see BUILD.md problem 5) and reverted: BM25
    is OR-semantics (ranks phrases with ANY query word), so a query
    containing a common word (e.g. the preposition "в") matched millions
    of largely-unrelated rows — both slower than the plain AND scan AND
    wrong. AND/ILIKE is slower per query (16-30s+) but always correct: a
    phrase only counts if it contains every query word (as lemmas).

    Also recognizes +word/-word operators (see query_syntax.py) right here
    in the default, lemmatized — unlike --mode query, which is literal.
    """
    from bukvarix.query_syntax import parse_query

    parsed = parse_query(query)
    con = connect(read_only=True)
    try:
        if parsed.excluded:
            self_result = None
        else:
            self_row, matched_via = _exact_lookup(con, query)
            self_result = {**self_row, "matched_via": matched_via} if self_row else None

        has_lemma_col = con.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'keywords' AND column_name = 'lemma_signature'"
        ).fetchone()[0]
        col = "lemma_signature" if has_lemma_col else "phrase"

        required = _lemmatize_words_list(con, parsed.required) if has_lemma_col else parsed.required
        excluded = _lemmatize_words_list(con, parsed.excluded) if has_lemma_col else parsed.excluded

        if not required:
            return {"self": self_result, "results": [], "total_count": 0, "total_freq_sum": 0}

        conditions = [f"{col} NOT ILIKE '%' || ? || '%'" for _ in excluded]
        params: list = list(excluded)
        for w in required:
            conditions.append(f"{col} ILIKE '%' || ? || '%'")
            params.append(w)
        where = " AND ".join(conditions)

        total_count, total_freq_sum = con.execute(
            f"SELECT count(*), coalesce(sum(freq_msk_exact), 0) FROM keywords WHERE {where}", params
        ).fetchone()
        sql = f"SELECT phrase, freq_msk_exact FROM keywords WHERE {where} ORDER BY freq_msk_exact DESC LIMIT ?"
        rows = con.execute(sql, [*params, limit]).fetchall()
        results = [{"phrase": r[0], "freq_msk_exact": r[1]} for r in rows]
        return {
            "self": self_result,
            "results": results,
            "total_count": total_count,
            "total_freq_sum": total_freq_sum,
        }
    finally:
        con.close()


def search_query(query: str, limit: int) -> list[dict]:
    """search --mode query: parses +word/-word operators (see query_syntax.py)."""
    from bukvarix.query_syntax import parse_query

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
    con = connect(read_only=True)
    try:
        rows = con.execute(sql, params).fetchall()
        return [{"phrase": r[0], "freq_msk_exact": r[1]} for r in rows]
    finally:
        con.close()


def search(query: str, mode: str, limit: int) -> list[dict]:
    if mode == "query":
        return search_query(query, limit)
    con = connect(read_only=True)
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


def freq(query: str) -> dict | None:
    con = connect(read_only=True)
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
        return {p: None for p in phrases}

    all_words = list({w for words in phrase_words.values() for w in words})
    lemma_by_word = dict(con.execute(
        "SELECT word, lemma FROM word_lemmas WHERE word IN (SELECT unnest(?))", [all_words]
    ).fetchall()) if all_words else {}

    return {
        p: " ".join(sorted(lemma_by_word.get(w, w) for w in words)) if words else None
        for p, words in phrase_words.items()
    }


def freq_batch(queries: list[str]) -> dict[str, dict | None]:
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

    con = connect(read_only=True)
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
        has_content_sig = remaining and con.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'keywords' AND column_name = 'content_signature'"
        ).fetchone()
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


def normalize() -> dict:
    from bukvarix.normalize import normalize as _normalize

    con = connect()
    try:
        return _normalize(con)
    finally:
        con.close()


def stats() -> dict:
    path = db_path()
    con = connect(read_only=True)
    try:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        row_count = (
            con.execute("SELECT count(*) FROM keywords").fetchone()[0]
            if "keywords" in tables
            else 0
        )
    finally:
        con.close()
    return {
        "db_path": str(path),
        "db_size_bytes": path.stat().st_size if path.exists() else 0,
        "tables": tables,
        "keywords_rows": row_count,
    }
