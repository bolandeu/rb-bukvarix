from __future__ import annotations

from pathlib import Path

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
