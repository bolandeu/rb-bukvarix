from __future__ import annotations

import duckdb


def _fetchone(con: duckdb.DuckDBPyConnection, sql: str) -> tuple:
    """`.fetchone()`, asserting the row exists — safe for aggregate queries
    (COUNT), which DuckDB always returns exactly one row for, even over
    zero matching rows.
    """
    result = con.execute(sql).fetchone()
    assert result is not None  # noqa: S101 -- type-narrowing an aggregate query's guaranteed row, not a security check
    return result


# Prepositions, conjunctions and particles — the same class of function words
# Yandex Wordstat ignores by default (unless forced back in with `+слово`).
# Deliberately excludes pronouns/demonstratives ("это", "весь", ...): those
# can carry real search intent, prepositions/conjunctions essentially never do.
STOPWORDS = [
    "в",
    "во",
    "на",
    "с",
    "со",
    "к",
    "ко",
    "у",
    "о",
    "об",
    "обо",
    "от",
    "ото",
    "до",
    "из",
    "изо",
    "для",
    "по",
    "при",
    "за",
    "над",
    "под",
    "про",
    "через",
    "между",
    "без",
    "вне",
    "около",
    "вокруг",
    "среди",
    "кроме",
    "сквозь",
    "вместо",
    "ради",
    "путём",
    "путем",
    "согласно",
    "благодаря",
    "вопреки",
    "и",
    "а",
    "но",
    "или",
    "либо",
    "да",
    "что",
    "как",
    "чтобы",
    "если",
    "хотя",
    "пока",
    "когда",
    "потому",
    "поэтому",
    "зато",
    "же",
    "ли",
    "бы",
    "не",
    "ни",
    "то",
]


def add_content_signature(con: duckdb.DuckDBPyConnection) -> None:
    """Stop-word-insensitive match key. Doesn't drop any rows (see PRD §4.1):
    unlike word-order permutations, phrases differing by a stop word are
    genuinely distinct source rows with their own frequency.
    """
    has_col = _fetchone(
        con,
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'keywords' AND column_name = 'content_signature'",
    )[0]
    if has_col:
        populated = _fetchone(con, "SELECT count(*) FROM keywords WHERE content_signature IS NOT NULL")[0]
        total = _fetchone(con, "SELECT count(*) FROM keywords")[0]
        if populated >= total - 1000:  # already done in a prior run, skip re-computing
            return

    sw_list = ", ".join(repr(w) for w in STOPWORDS)
    con.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS content_signature VARCHAR")
    con.execute(
        f"""
        UPDATE keywords
        SET content_signature = list_aggregate(
            list_sort(list_filter(str_split(lower(phrase), ' '), w -> w NOT IN ({sw_list}))),
            'string_agg', ' '
        )
        """
    )


def _lemmatize_words(words: list[str]) -> list[tuple[str, str]]:
    import pymorphy3

    morph = pymorphy3.MorphAnalyzer()
    return [(w, morph.parse(w)[0].normal_form) for w in words]


def add_lemma_signature(con: duckdb.DuckDBPyConnection) -> int:
    """Lemma-insensitive match key, via a word-level pymorphy3 dictionary.

    Lemmatizing per-phrase in Python would mean re-lemmatizing shared words
    over and over across ~114M rows; instead lemmatize each *distinct* word
    once (a few million, not hundreds of millions), load that as a small
    lookup table, and do the phrase-level explode/join/re-aggregate in SQL
    (vectorized, no per-row Python calls). Doesn't drop any rows — same
    reasoning as content_signature, plus pymorphy3 itself isn't always right
    on rare words/brands/slang, and a physical delete on top of a wrong
    lemma would be unrecoverable. Returns the number of distinct words
    lemmatized.
    """
    has_word_lemmas = _fetchone(con, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'word_lemmas'")[
        0
    ]
    if has_word_lemmas and _fetchone(con, "SELECT count(*) FROM word_lemmas")[0] > 0:
        word_count = _fetchone(con, "SELECT count(*) FROM word_lemmas")[0]
    else:
        words = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT word FROM (SELECT unnest(str_split(lower(phrase), ' ')) AS word FROM keywords) WHERE word != ''"
            ).fetchall()
        ]
        pairs = _lemmatize_words(words)
        con.execute("DROP TABLE IF EXISTS word_lemmas")
        con.execute("CREATE TABLE word_lemmas (word VARCHAR PRIMARY KEY, lemma VARCHAR)")
        con.executemany("INSERT INTO word_lemmas VALUES (?, ?)", pairs)
        word_count = len(words)

    # A single GROUP BY over all 113.7M rows OOM'd twice (12.7GB, then 9.3GB
    # even with preserve_insertion_order=false + memory_limit='10GB') — the
    # hash-aggregate needs a live entry per group, and ~114M mostly-unique
    # phrase groups makes that huge regardless of those settings. A row-wise
    # Python UDF (dict lookup per word, no GROUP BY) avoided the OOM but was
    # far too slow (~15min/5M rows -> hours for the full table — DuckDB's
    # Python UDF call overhead dominates even for a trivial dict.get()).
    # Fix: same JOIN+GROUP BY query, but run it over N slices of the table
    # (WHERE hash(phrase) % N = i) instead of all rows at once, so no single
    # hash-aggregate ever holds more than ~1/N of the groups. Verified one
    # slice (1/24, 4.74M rows) at 5.4s -> full table well under 3 minutes.
    con.execute("PRAGMA preserve_insertion_order = false")
    con.execute("SET memory_limit = '10GB'")
    con.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS lemma_signature VARCHAR")

    # lemma_postings is built from lemma_signature — drop it if any row is
    # about to get a (new) signature, so search can't read stale postings.
    # A fully normalized table is left alone: rebuilding postings is costly.
    if _fetchone(con, "SELECT count(*) FROM keywords WHERE lemma_signature IS NULL")[0]:
        from bukvarix.core.db import drop_lemma_postings

        drop_lemma_postings(con)

    n_chunks = 24
    for i in range(n_chunks):
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _chunk_lemma AS
            SELECT k.phrase,
                   list_aggregate(
                       list_sort(array_agg(coalesce(wl.lemma, tok.word))),
                       'string_agg', ' '
                   ) AS lemma_signature
            FROM keywords k, UNNEST(str_split(lower(k.phrase), ' ')) AS tok(word)
            LEFT JOIN word_lemmas wl ON wl.word = tok.word
            WHERE hash(k.phrase) % {n_chunks} = {i} AND k.lemma_signature IS NULL
            GROUP BY k.phrase
            """
        )
        con.execute(
            """
            UPDATE keywords
            SET lemma_signature = _chunk_lemma.lemma_signature
            FROM _chunk_lemma
            WHERE keywords.phrase = _chunk_lemma.phrase
            """
        )
    con.execute("DROP TABLE IF EXISTS _chunk_lemma")

    return word_count


def normalize(con: duckdb.DuckDBPyConnection) -> dict:
    add_content_signature(con)
    word_count = add_lemma_signature(con)
    con.execute("CHECKPOINT")
    return {"distinct_words_lemmatized": word_count}
