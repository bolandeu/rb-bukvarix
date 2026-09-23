from __future__ import annotations

from pathlib import Path

import pytest

from bukvarix.core import db


def test_import_keywords_collapses_word_order_permutations(imported_db: Path) -> None:
    result = db.stats(imported_db)
    assert result["keywords_rows"] == 6  # 7 raw rows, one permutation collapsed

    # the higher-frequency phrasing of the permutation pair survives
    row, matched_via = db._exact_lookup(db.connect(imported_db, read_only=True), "авто продажа")
    assert row is not None
    assert row["phrase"] == "продажа авто"
    assert row["freq_msk_exact"] == 500
    assert matched_via == "exact"


def test_freq_exact_match(imported_db: Path) -> None:
    result = db.freq(imported_db, "авто купить")  # word order doesn't matter
    assert result == {"phrase": "купить авто", "freq_msk_exact": 300, "matched_via": "exact"}


def test_freq_unknown_phrase_returns_none(imported_db: Path) -> None:
    assert db.freq(imported_db, "совершенно незнакомая фраза") is None


def test_freq_falls_back_to_stopwords_tier(normalized_db: Path) -> None:
    result = db.freq(normalized_db, "авто москве")  # same words as "авто в москве" minus "в"
    assert result is not None
    assert result["phrase"] == "авто в москве"
    assert result["matched_via"] == "stopwords"


def test_freq_falls_back_to_lemma_tier(normalized_db: Path) -> None:
    # "растаможки" only appears (as a word) in a different phrase in the
    # corpus — the lemma tier is what lets it resolve to "растаможка авто".
    result = db.freq(normalized_db, "растаможки авто")
    assert result is not None
    assert result["phrase"] == "растаможка авто"
    assert result["matched_via"] == "lemma"


def test_freq_batch_matches_single_freq_calls(normalized_db: Path) -> None:
    queries = ["авто купить", "авто москве", "растаможки авто", "нет такой фразы"]
    batch = db.freq_batch(normalized_db, queries)
    for q in queries:
        assert batch[q] == db.freq(normalized_db, q)


def test_search_smart_requires_all_words_as_lemmas(normalized_db: Path) -> None:
    result = db.search_smart(normalized_db, "авто", 20)
    assert result["total_count"] == 6  # every remaining row mentions "авто" somewhere
    assert result["total_freq_sum"] == 500 + 300 + 50 + 80 + 20 + 40
    assert result["results"][0]["phrase"] == "продажа авто"  # ordered by freq desc
    assert result["self"] is None  # "авто" alone is never a phrase of its own


def test_search_smart_excluded_word_drops_self(normalized_db: Path) -> None:
    result = db.search_smart(normalized_db, "авто -дешево", 20)
    assert result["self"] is None  # can't have one canonical phrase when excluding
    phrases = [r["phrase"] for r in result["results"]]
    assert "продажа авто дешево" not in phrases


def test_search_mode_exact(imported_db: Path) -> None:
    results = db.search(imported_db, "авто продажа", "exact", 20)
    assert results == [{"phrase": "продажа авто", "freq_msk_exact": 500, "matched_via": "exact"}]


def test_search_mode_prefix(imported_db: Path) -> None:
    results = db.search(imported_db, "продажа", "prefix", 20)
    # both phrases starting with "продажа", ordered by freq desc
    assert [r["phrase"] for r in results] == ["продажа авто", "продажа авто дешево"]


def test_search_mode_contains(imported_db: Path) -> None:
    results = db.search(imported_db, "авто дешево", "contains", 20)
    assert [r["phrase"] for r in results] == ["продажа авто дешево"]


def test_search_mode_words_ignores_order(imported_db: Path) -> None:
    results = db.search(imported_db, "дешево авто", "words", 20)
    assert [r["phrase"] for r in results] == ["продажа авто дешево"]


def test_search_mode_query_operators(imported_db: Path) -> None:
    results = db.search(imported_db, "авто -дешево", "query", 20)
    phrases = [r["phrase"] for r in results]
    assert "продажа авто дешево" not in phrases
    assert "продажа авто" in phrases


def test_build_indexes_is_idempotent(normalized_db: Path) -> None:
    db.build_indexes(normalized_db)
    db.build_indexes(normalized_db)  # re-running must not fail (CREATE INDEX IF NOT EXISTS)
    # indexing doesn't change query results
    result = db.freq(normalized_db, "авто купить")
    assert result is not None
    assert result["phrase"] == "купить авто"


def test_stats_on_missing_db_reports_zero(tmp_path: Path) -> None:
    result = db.stats(tmp_path / "does-not-exist.duckdb")
    assert result["keywords_rows"] == 0
    assert result["tables"] == []
    assert result["db_size_bytes"] == 0


def _phrases(result: dict) -> list[str]:
    return [r["phrase"] for r in result["results"]]


def test_search_smart_postings_matches_scan_on_whole_words(postings_db: Path) -> None:
    for query in ["авто продажа", "купить авто", "продажа -дешево", "москве", "растаможки"]:
        postings = db.search_smart(postings_db, query, 20)
        scan = db.search_smart(postings_db, query, 20, force_scan=True)
        assert postings["engine"] == "postings"
        assert scan["engine"] == "scan"
        for key in ("self", "results", "total_count", "total_freq_sum"):
            assert postings[key] == scan[key], (query, key)


def test_search_smart_postings_is_whole_word(postings_db: Path) -> None:
    # lemma "автомобиль" contains the substring "авто": the scan matches it,
    # postings don't — an intentional change from substring to whole-word.
    postings = db.search_smart(postings_db, "авто", 20)
    scan = db.search_smart(postings_db, "авто", 20, force_scan=True)
    assert "растаможки автомобиля" in _phrases(scan)
    assert "растаможки автомобиля" not in _phrases(postings)
    assert postings["total_count"] == scan["total_count"] - 1
    assert postings["total_freq_sum"] == 500 + 300 + 50 + 80 + 40


def test_search_smart_postings_lemmatizes_query(postings_db: Path) -> None:
    # "растаможки" -> "растаможка": both растаможка phrases match
    result = db.search_smart(postings_db, "растаможки", 20)
    assert result["engine"] == "postings"
    assert _phrases(result) == ["растаможка авто", "растаможки автомобиля"]


def test_search_smart_postings_excluded(postings_db: Path) -> None:
    result = db.search_smart(postings_db, "авто -дешево", 20)
    assert result["engine"] == "postings"
    assert result["self"] is None
    assert "продажа авто дешево" not in _phrases(result)
    assert "продажа авто" in _phrases(result)


def test_search_smart_postings_stopword_is_searchable(postings_db: Path) -> None:
    result = db.search_smart(postings_db, "+в авто", 20)
    assert _phrases(result) == ["авто в москве"]


def test_search_smart_postings_respects_limit_and_order(postings_db: Path) -> None:
    result = db.search_smart(postings_db, "авто", 2)
    assert _phrases(result) == ["продажа авто", "купить авто"]
    assert result["total_count"] == 5


def test_search_smart_unknown_word_is_empty(postings_db: Path) -> None:
    result = db.search_smart(postings_db, "авто звездолёт", 20)
    assert result["engine"] == "postings"
    assert result["results"] == []
    assert result["total_count"] == 0
    assert result["total_freq_sum"] == 0


def test_search_smart_falls_back_to_scan_without_postings(normalized_db: Path) -> None:
    assert db.search_smart(normalized_db, "авто", 20)["engine"] == "scan"


def test_search_smart_ignores_stale_postings(postings_db: Path, capsys) -> None:
    con = db.connect(postings_db)
    con.execute("UPDATE lemma_postings_meta SET keywords_rows = keywords_rows + 1")
    con.close()
    assert db.search_smart(postings_db, "авто", 20)["engine"] == "scan"
    assert "stale" in capsys.readouterr().err


def test_build_lemma_postings_meta(postings_db: Path) -> None:
    con = db.connect(postings_db, read_only=True)
    keywords_rows, postings_rows = db._fetchone(con, "SELECT keywords_rows, postings_rows FROM lemma_postings_meta")
    assert keywords_rows == db._fetchone(con, "SELECT count(*) FROM keywords")[0]
    # one posting per word of every phrase
    assert postings_rows == db._fetchone(con, "SELECT sum(len(str_split(lemma_signature, ' '))) FROM keywords")[0]
    # every posting points at a phrase that really contains that lemma, with its freq
    bad = db._fetchone(
        con,
        """
        SELECT count(*) FROM lemma_postings p JOIN keywords k ON k.rowid = p.kw_id
        WHERE NOT list_contains(str_split(k.lemma_signature, ' '), p.lemma) OR p.freq != k.freq_msk_exact
        """,
    )[0]
    assert bad == 0
    con.close()


def test_build_lemma_postings_requires_normalize(imported_db: Path) -> None:
    from bukvarix.core.errors import CliError

    with pytest.raises(CliError) as exc:
        db.build_lemma_postings(imported_db)
    assert exc.value.code == "DATABASE_NOT_READY"


def test_import_keywords_drops_postings(postings_db: Path, small_tsv: Path) -> None:
    db.import_keywords(postings_db, small_tsv)
    assert "lemma_postings" not in db.stats(postings_db)["tables"]


def test_normalize_keeps_postings_when_nothing_changes(postings_db: Path) -> None:
    db.normalize(postings_db)  # already normalized -> no signature recomputed
    assert "lemma_postings" in db.stats(postings_db)["tables"]


def test_build_lemma_postings_chunks_keep_global_sort(normalized_db: Path, tmp_path: Path) -> None:
    staging_dir = tmp_path / "staging"
    result = db.build_lemma_postings(normalized_db, temp_directory=staging_dir, chunk_rows=3)
    assert result["chunks"] > 1
    assert not list(staging_dir.glob("*.duckdb*"))  # staging file cleaned up
    con = db.connect(normalized_db, read_only=True)
    rows = con.execute("SELECT lemma, kw_id FROM lemma_postings").fetchall()
    con.close()
    assert rows == sorted(rows)
    assert len(rows) == result["postings_rows"]
