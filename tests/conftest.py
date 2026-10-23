"""Shared fixtures: rerun a test file under the CI environment, and give each test its own local database."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from bukvarix.core import db as bukvarix_db

# Typer reads these once, at import, and then forces terminal output — what a CI runner sets.
CI_ENV = {"GITHUB_ACTIONS": "true", "FORCE_COLOR": "1", "PY_COLORS": "1"}


@pytest.fixture
def same_in_ci(request: pytest.FixtureRequest) -> Callable[[str], None]:
    """Rerun this file's tests matching `-k` under CI_ENV in a fresh interpreter: the only way to set a value read at import."""
    if request.config.getoption("--snapshot-update"):
        pytest.skip("the snapshots are being written; there is nothing to compare yet")

    def run(selection: str) -> None:
        options = ["-k", selection, "-p", "no:cacheprovider", "--no-cov", "-q"]
        command = [sys.executable, "-m", "pytest", str(request.path), *options]
        env = {**os.environ, **CI_ENV}
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)  # noqa: S603
        assert result.returncode == 0, result.stdout[-3000:]

    return run


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


@pytest.fixture
def postings_db(normalized_db: Path) -> Path:
    """normalized_db, plus the lemma_postings inverted index."""
    bukvarix_db.build_lemma_postings(normalized_db)
    return normalized_db


CONTRACT_DATA = Path(__file__).parent / "contracts" / "data"


@pytest.fixture
def contract_bukvarix_db(tmp_path: Path, small_tsv: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Offline data for the consumers' contracts (tests/contracts/fixtures.yaml): _ROWS built into a full
    database with a data date, BUKVARIX_DB_PATH pointing at it, tests/contracts/data/ as the working dir."""
    db_path = tmp_path / "contract.duckdb"
    bukvarix_db.import_keywords(db_path, small_tsv, data_date=dt.date(2026, 9, 23))
    bukvarix_db.normalize(db_path)
    bukvarix_db.build_lemma_postings(db_path)
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(db_path))
    monkeypatch.chdir(CONTRACT_DATA)
    return db_path
