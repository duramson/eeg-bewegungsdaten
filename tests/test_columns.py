"""The column dictionary covers every column of every layer, and its codes match the checks."""

from __future__ import annotations

import polars as pl
import pytest

from eeg_bewegungsdaten.columns import (
    _EXPORT_CODES,
    COLUMNS,
    ENERGIETRAEGER,
    VERAEUSSERUNGSFORM,
    describe,
    describe_export,
)
from eeg_bewegungsdaten.export import ANLAGE_ID_SCHEMA, EXPORT_SCHEMA
from eeg_bewegungsdaten.links import UENB
from eeg_bewegungsdaten.mastr import MASTR_SCHEMA, TABLES
from eeg_bewegungsdaten.quality import NUMBER_COLUMNS
from eeg_bewegungsdaten.schema import (
    CLEAN_SCHEMA,
    QUARANTINE_SCHEMA,
    SOURCE_COLUMNS,
    TEXT_SCHEMA,
    VERAEUSSERUNGSFORM_CODES,
)

LAYERS = {
    "text": TEXT_SCHEMA,
    "clean": CLEAN_SCHEMA,
    "quarantine": QUARANTINE_SCHEMA,
    "mastr": MASTR_SCHEMA,
    "export": EXPORT_SCHEMA,
    "anlage_id": ANLAGE_ID_SCHEMA,
}


@pytest.mark.parametrize("layer", LAYERS)
def test_every_column_has_an_entry(layer: str) -> None:
    schema = LAYERS[layer]
    missing = [name for name in schema if name not in COLUMNS]
    assert not missing, f"{layer}: no entry in COLUMNS for {missing}"
    assert [column.name for column, _ in describe(schema)] == list(schema)


def test_every_entry_is_a_column() -> None:
    used = {name for schema in LAYERS.values() for name in schema}
    assert set(COLUMNS) - used == set()


@pytest.mark.parametrize("name", COLUMNS)
def test_entries_are_sentences(name: str) -> None:
    column = COLUMNS[name]
    assert column.description.endswith("."), name
    assert all(meaning for meaning in column.codes.values()), name


def test_only_amounts_have_a_unit() -> None:
    with_unit = {name for name, column in COLUMNS.items() if column.unit}
    assert with_unit == set(NUMBER_COLUMNS)
    assert all(CLEAN_SCHEMA[name] == pl.Float64() for name in with_unit)


def test_legend_names_are_read_into_their_column() -> None:
    for name, column in COLUMNS.items():
        if column.legend is not None:
            assert SOURCE_COLUMNS[column.legend.lower()] == name


def test_codes_match_the_values_the_tool_writes() -> None:
    assert set(COLUMNS["uenb"].codes) == set(UENB)
    assert set(ENERGIETRAEGER) == set(TABLES)
    assert set(VERAEUSSERUNGSFORM) == set(VERAEUSSERUNGSFORM_CODES.values())


def test_the_export_entries_name_nothing_the_export_does_not_hold() -> None:
    exported = describe_export(EXPORT_SCHEMA)
    texts = [column.description for column, _ in exported]
    texts += [meaning for column, _ in exported for meaning in column.codes.values()]
    for absent in ("In `text`", "text layer", "eeg_mastr_nrs_abgeglichen"):
        assert [text for text in texts if absent in text] == [], absent
    # Every replaced meaning belongs to a code of the export.
    codes = {(column.name, code) for column, _ in exported for code in column.codes}
    assert set(_EXPORT_CODES) <= codes
