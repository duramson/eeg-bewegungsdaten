"""The common schema and the mapping from each TSO's CSV header to it."""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

# MaStR numbers of the four TSOs, from the "Netzbetreiber" sheet of the official legend.
UENB_MASTR_NR = {
    "50hertz": "SNB982046657236",
    "amprion": "SNB976890256486",
    "tennet": "SNB970033313272",
    "transnetbw": "SNB984944755380",
}

# Column name after `normalize_header` -> column in the common schema. Until 2023 the names differ
# by year and TSO; the comments name the files that use a spelling.
SOURCE_COLUMNS = {
    "eeg_mastr_nr": "eeg_mastr_nr",
    "eeg_anlagenschluessel": "anlagenschluessel",
    "anlagenschluessel": "anlagenschluessel",  # 2016, Amprion 2017
    "anlagennummer": "anlagenschluessel",  # 50Hertz 2016
    "nb_bnr": "nb_bnr",
    "netzbetreiber_betriebsnummer": "nb_bnr",  # TenneT and TransnetBW 2016, Amprion 2017
    "bnetzanummer": "nb_bnr",  # 50Hertz 2016
    "nb_mastr_nr": "nb_mastr_nr",
    "netzbetreiber": "netzbetreiber",
    "netzbetreiber_name": "netzbetreiber",  # TransnetBW 2016, Amprion 2017
    "name": "netzbetreiber",  # 50Hertz 2016
    "firma_netzbetreiber": "netzbetreiber",  # TransnetBW 2022
    "uenb_mastr_nr": "uenb_mastr_nr",
    "veraeusserungsform": "veraeusserungsform",
    "typ": "veraeusserungsform",  # Amprion 2018: EEG, DV, MSZ, AFV
    "verguetungskategorie": "verguetungskategorie",
    "kategorie": "verguetungskategorie",  # TenneT and TransnetBW 2016, Amprion 2017
    "eeg_verguetungsgruppe": "verguetungskategorie",  # 50Hertz 2016
    "strommenge": "strommenge",
    "einspeisung": "strommenge",  # TenneT 2016
    "phys": "strommenge",  # 50Hertz 2016, "PHYS [KWH]"
    "menge_kwh": "strommenge",  # Amprion 2016
    "jahresarbeit_kwh": "strommenge",  # Amprion 2017
    "in_eingespeiste_jahresarbeit": "strommenge",  # TransnetBW 2016
    "eeg_zahlung": "eeg_zahlung",
    "verguetung": "eeg_zahlung",  # TenneT 2016
    "finz": "eeg_zahlung",  # 50Hertz 2016, "FINZ [€]"
    "betrag_euro": "eeg_zahlung",  # Amprion 2016
    "verguetung_euro": "eeg_zahlung",  # Amprion 2017
    "verguetung_fuer_in_abgenommene_eeg_einspeisung": "eeg_zahlung",  # TransnetBW 2016
    "eeg_einnahmen": "eeg_einnahmen",
    "vermiedene_netzentgelte": "vermiedene_netzentgelte",
    "vnne": "vermiedene_netzentgelte",  # 50Hertz and TenneT 2016
    "vnne_euro": "vermiedene_netzentgelte",  # Amprion 2016 and 2017
    "vermiedene_netznutzungsentgelte": "vermiedene_netzentgelte",  # TransnetBW 2016
    "monat": "monat",
}

# Columns that are read but not kept: row numbers, the BDEW number of the TSO itself (Amprion
# 2018 and 2019) and the Bemessungsleistung in Amprion's vNNE file of 2016.
IGNORED_COLUMNS = frozenset({"id", "rownum", "zeilen_nr", "uenb_bnr", "bemessungsleistung_kw"})
IGNORED_PREFIX = "_ignored_"

# The column sets that occur in the files. A header must give one of them, so a changed format
# stops the file instead of filling a column with nulls.
_UP_TO_2019 = frozenset(
    {
        "anlagenschluessel",
        "nb_bnr",
        "netzbetreiber",
        "verguetungskategorie",
        "strommenge",
        "eeg_zahlung",
        "vermiedene_netzentgelte",
    }
)
_AMPRION_2018 = _UP_TO_2019 - {"netzbetreiber"}
_2020 = _UP_TO_2019 | {"eeg_mastr_nr", "nb_mastr_nr", "uenb_mastr_nr", "veraeusserungsform"}
_FROM_2024 = frozenset(
    {
        "eeg_mastr_nr",
        "anlagenschluessel",
        "nb_mastr_nr",
        "uenb_mastr_nr",
        "veraeusserungsform",
        "verguetungskategorie",
        "strommenge",
        "eeg_zahlung",
        "eeg_einnahmen",
        "monat",
    }
)
LAYOUTS = (
    _UP_TO_2019,  # 2016 to 2019, except Amprion 2016, 2018 and 2019
    # Amprion 2016: payments and vNNE in two files
    frozenset({"anlagenschluessel", "verguetungskategorie", "strommenge", "eeg_zahlung"}),
    frozenset({"anlagenschluessel", "verguetungskategorie", "vermiedene_netzentgelte"}),
    _AMPRION_2018,  # Amprion 2018 and 2019
    _AMPRION_2018 | {"veraeusserungsform"},  # Amprion 2018 with column TYP
    _2020,
    _2020 | {"monat"},  # 2021 and 2022; 50Hertz and TenneT 2023
    # Amprion 2022 and 2023, TransnetBW 2023
    (_FROM_2024 - {"eeg_einnahmen"}) | {"vermiedene_netzentgelte"},
    _FROM_2024,
    _FROM_2024 - {"monat"},  # TenneT 2024, first upload of September 2025
    _FROM_2024 | {"nb_bnr", "netzbetreiber"},  # TenneT 2025
)

# Veraeusserungsform as code (from 2023) or as text (2020 to 2022, Amprion 2018), after
# lower-casing and replacing umlauts. Codes as in the official legend.
VERAEUSSERUNGSFORM_CODES = {
    **{str(code): code for code in range(1, 6)},
    "einspeiseverguetung": 1,
    "eeg-einspeiseverguetung": 1,  # TransnetBW 2020
    "eeg": 1,
    "marktpraemie": 2,
    "direktvermarktung": 2,  # 2020
    "dv": 2,
    "mieterstromzuschlag": 3,
    "mieterstromfoerderung": 3,  # TransnetBW 2020
    "msz": 3,
    "ausfallverguetung": 4,
    "ausfallvermarktung": 4,  # TransnetBW 2020
    "afv": 4,
    "sonstiges": 5,
    "sonstige": 5,  # TransnetBW 2020
    # Shown separately in 2020, part of "Sonstiges" from 2021 on.
    "vermiedene netzentgelte": 5,
    "vne": 5,
    "flexibilitaetspraemie/-zuschlag": 5,
    "flexibilitaetspraemie": 5,
    "selbstverbrauch": 5,
}

# The source columns in the order of the common schema.
TEXT_COLUMNS = (
    "eeg_mastr_nr",
    "anlagenschluessel",
    "nb_mastr_nr",
    "nb_bnr",
    "netzbetreiber",
    "uenb_mastr_nr",
    "veraeusserungsform",
    "verguetungskategorie",
    "strommenge",
    "eeg_zahlung",
    "eeg_einnahmen",
    "vermiedene_netzentgelte",
    "monat",
)

# Every data line as published: all fields as text, nothing trimmed or converted. Columns that
# the file does not have are null, empty fields are ''. Columns in `IGNORED_COLUMNS` are only
# in `rohzeile`.
TEXT_SCHEMA = pl.Schema(
    {
        "jahr": pl.Int16(),
        "uenb": pl.String(),
        "quelldatei": pl.String(),
        "zeile": pl.Int32(),
        "kodierung": pl.String(),
        "felder": pl.Int32(),
        "felder_kopf": pl.Int32(),
        **{column: pl.String() for column in TEXT_COLUMNS},
        "zusatzfelder": pl.List(pl.String()),
        "rohzeile": pl.String(),
    }
)

CLEAN_SCHEMA = pl.Schema(
    {
        "jahr": pl.Int16(),
        "uenb": pl.String(),
        "eeg_mastr_nr": pl.String(),
        "anlagenschluessel": pl.String(),
        "nb_mastr_nr": pl.String(),
        "nb_bnr": pl.String(),
        "netzbetreiber": pl.String(),
        "uenb_mastr_nr": pl.String(),
        "veraeusserungsform": pl.Int8(),
        "verguetungskategorie": pl.String(),
        "sonderzeile": pl.String(),
        "strommenge": pl.Float64(),
        "eeg_zahlung": pl.Float64(),
        "eeg_einnahmen": pl.Float64(),
        "vermiedene_netzentgelte": pl.Float64(),
        "monat": pl.Int8(),
        "hinweise": pl.List(pl.String()),
        "quelldatei": pl.String(),
        "zeile": pl.Int32(),
    }
)

QUARANTINE_SCHEMA = pl.Schema(
    {
        "jahr": pl.Int16(),
        "uenb": pl.String(),
        "quelldatei": pl.String(),
        "zeile": pl.Int32(),
        "fehlercode": pl.String(),
        "detail": pl.String(),
        "rohzeile": pl.String(),
    }
)

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


class FormatError(ValueError):
    """A CSV header that does not match the known formats."""


class OutdatedFileError(RuntimeError):
    """An output file written by an older version of the tool, without the current columns."""


def check_columns(path: Path, schema: pl.Schema, command: str) -> None:
    """Raise `OutdatedFileError` if the Parquet file `path` lacks a column of `schema`, as files
    written before a column was renamed do; `command` writes the file again."""
    columns = pl.read_parquet_schema(path)
    missing = [name for name in schema if name not in columns]
    if missing:
        raise OutdatedFileError(
            f"{path} has no column {', '.join(missing)}: it was written by an older version of "
            f"the tool; run '{command}' again"
        )


def normalize_header(name: str) -> str:
    """'EEG-Anlagenschluessel' -> 'eeg_anlagenschluessel', 'Veraeusserungsform (Code)' ->
    'veraeusserungsform', '"PHYS [KWH]"' -> 'phys', 'in 2016 eingespeiste Jahresarbeit' ->
    'in_eingespeiste_jahresarbeit'. Empty names (as in a trailing ``;;``) stay empty."""
    name = name.strip().lstrip("\ufeff").strip('"').lower().translate(_UMLAUTS)
    name = re.sub(r"\(.*?\)|\[.*?\]", "", name)
    name = re.sub(r"(?<!\d)20\d\d(?!\d)", "", name)
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_")


def map_header(header: list[str], source: str) -> list[str | None]:
    """Common-schema name for every header field: None for unnamed fields, a name
    starting with `IGNORED_PREFIX` for columns that are read but not kept."""
    mapped: list[str | None] = []
    for raw in header:
        key = normalize_header(raw)
        if not key:
            mapped.append(None)
        elif key in IGNORED_COLUMNS:
            mapped.append(IGNORED_PREFIX + key)
        elif key in SOURCE_COLUMNS:
            mapped.append(SOURCE_COLUMNS[key])
        else:
            raise FormatError(f"{source}: unknown column {raw!r}")

    named = [m for m in mapped if m is not None]
    duplicates = sorted({m for m in named if named.count(m) > 1})
    if duplicates:
        raise FormatError(f"{source}: duplicate columns {duplicates}")
    used = frozenset(m for m in named if not m.startswith(IGNORED_PREFIX))
    if used not in LAYOUTS:
        closest = min(LAYOUTS, key=lambda layout: (len(layout ^ used), sorted(layout)))
        raise FormatError(
            f"{source}: no known layout; compared with the closest one, "
            f"missing columns {sorted(closest - used)}, unexpected columns {sorted(used - closest)}"
        )
    return mapped
