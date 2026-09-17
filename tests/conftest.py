from __future__ import annotations

from pathlib import Path

import pytest

from bukvarix.core import db as bukvarix_db

# A tiny, hand-picked corpus exercising every match tier without touching
# the real 44GB production database:
#   - "продажа авто" / "авто продажа" are word-order permutations of each
#     other -> import_keywords collapses them, keeping the higher-freq one.
#   - "растаможки автомобиля" exists only so "растаможки" and "автомобиля"
#     become known corpus words with pymorphy3 lemmas ("растаможка",
#     "автомобиль") — the lemma tier can only resolve a query word it saw
#     at import time (see core/db.py's word_lemmas table).
#   - "авто в москве" has a stop word ("в"), exercising content_signature.
_ROWS = [
    ("продажа авто", 500),
    ("авто продажа", 200),  # permutation duplicate of the row above
    ("купить авто", 300),
    ("продажа авто дешево", 50),
    ("растаможка авто", 80),
    ("растаможки автомобиля", 20),
    ("авто в москве", 40),
]


@pytest.fixture
def small_tsv(tmp_path: Path) -> Path:
    path = tmp_path / "keywords.tsv"
    path.write_text("".join(f"{phrase}\t{freq}\n" for phrase, freq in _ROWS), encoding="utf-8")
    return path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "bukvarix.duckdb"


@pytest.fixture
def imported_db(db_path: Path, small_tsv: Path) -> Path:
    """A db_path with _ROWS imported (no normalize/index yet)."""
    bukvarix_db.import_keywords(db_path, small_tsv)
    return db_path


@pytest.fixture
def normalized_db(imported_db: Path) -> Path:
    """imported_db, plus content_signature/lemma_signature populated."""
    bukvarix_db.normalize(imported_db)
    return imported_db
