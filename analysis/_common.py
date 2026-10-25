"""What the analysis scripts share: the matched clean rows of a year and the open-MaStR database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import polars as pl

from eeg_bewegungsdaten.mastr import (
    HITS,
    MASTR_SCHEMA,
    check_match,
    clean_files,
    default_db_path,
)
from eeg_bewegungsdaten.paths import default_data_dir, output_paths
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA, check_columns

# Matches that lead to exactly one EEG plant.
SINGLE_PLANT = [hit for hit in HITS if hit != "MULTIPLE"]
KEY = ["jahr", "uenb", "quelldatei", "zeile"]
# SHA-256 of the clean files for `check_match`: a script reads several years, and each file is
# hashed once per run.
_HASHES: dict[Path, str] = {}


def matched_rows(jahr: int, energietraeger: str) -> pl.LazyFrame:
    """The clean rows of `jahr` that `mastr` matched to exactly one plant of `energietraeger`,
    with `eeg_mastr_nr_abgeglichen`."""
    data = default_data_dir()
    uenbs = [uenb for year, uenb in clean_files(data) if year == jahr]
    clean = [output_paths(data, jahr, uenb).clean for uenb in uenbs]
    matches = [check_match(data, jahr, uenb, hashes=_HASHES) for uenb in uenbs]
    for path in clean:
        check_columns(path, CLEAN_SCHEMA, "ingest")
    for path in matches:
        check_columns(path, MASTR_SCHEMA, "mastr")
    return (
        pl.scan_parquet(clean)
        .join(pl.scan_parquet(matches), on=KEY)
        .filter(
            (pl.col("energietraeger") == energietraeger) & pl.col("treffer").is_in(SINGLE_PLANT)
        )
    )


def query_mastr(sql: str) -> list[tuple[Any, ...]]:
    """The rows of a query against the open-MaStR database, opened read-only."""
    with closing(sqlite3.connect(f"file:{default_db_path()}?mode=ro", uri=True)) as con:
        return con.execute(sql).fetchall()
