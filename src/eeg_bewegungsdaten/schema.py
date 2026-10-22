"""The common schema and the mapping from each TSO's CSV header to it."""

from __future__ import annotations

import re

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
    "tariff": "verguetungskategorie",  # TransnetBW 2007 to 2010 (Zenodo)
    "gen": "strommenge",
    "pay": "eeg_zahlung",
    "unit_id": "anlagenschluessel",  # Amprion 2012, 50Hertz 2012 and 2013 (Zenodo)
    "eeg_tariff": "verguetungskategorie",
    "generation": "strommenge",
    "payment": "eeg_zahlung",
    "anc": "vermiedene_netzentgelte",
    "uenb_anlagennummer": "anlagenschluessel",  # TransnetBW 2014 to 2016
    "eeg_strom": "strommenge",  # 50Hertz 2010
    "eeg_mastr_nr": "eeg_mastr_nr",
    "eeg_anlagenschluessel": "anlagenschluessel",
    "anlagenschluessel": "anlagenschluessel",  # 2011 to 2016, Amprion 2017
    "anlagennummer": "anlagenschluessel",  # 50Hertz 2016
    "nb_bnr": "nb_bnr",
    "netzbetreiber_betriebsnummer": "nb_bnr",  # TenneT and TransnetBW 2016, Amprion 2017
    "bnetzanummer": "nb_bnr",  # 50Hertz 2015 and 2016
    "bnetza_id": "nb_bnr",  # TenneT 2011 and 2013 to 2015
    "nb_mastr_nr": "nb_mastr_nr",
    "netzbetreiber": "netzbetreiber",
    "netzbetreiber_name": "netzbetreiber",  # TransnetBW 2016, Amprion 2017
    "name": "netzbetreiber",  # 50Hertz 2015 and 2016
    "firma": "netzbetreiber",  # TenneT 2011 to 2013 and 2015
    "firma_netzbetreiber": "netzbetreiber",  # TransnetBW 2022
    "uenb_mastr_nr": "uenb_mastr_nr",
    "veraeusserungsform": "veraeusserungsform",
    "typ": "veraeusserungsform",  # Amprion 2018: EEG, DV, MSZ, AFV
    "verguetungskategorie": "verguetungskategorie",
    "kategorie": "verguetungskategorie",  # TenneT 2011 to 2016, TransnetBW 2016, Amprion 2017
    "eeg_verguetungsgruppe": "verguetungskategorie",  # 50Hertz 2015 and 2016
    "strommenge": "strommenge",
    "einspeisung": "strommenge",  # TenneT 2011 to 2016
    "phys": "strommenge",  # 50Hertz 2015 and 2016, "PHYS [KWH]"
    "kwh": "strommenge",  # 50Hertz 2014
    "menge": "strommenge",  # Amprion 2011 and 2013, "MENGE [KWH]"
    "menge_kwh": "strommenge",  # Amprion 2016
    "jahresarbeit_kwh": "strommenge",  # Amprion 2017
    "in_eingespeiste_jahresarbeit": "strommenge",  # TransnetBW 2016
    "eeg_zahlung": "eeg_zahlung",
    "verguetung": "eeg_zahlung",  # TenneT 2011 to 2016, 50Hertz 2014 and 2015
    "finz": "eeg_zahlung",  # 50Hertz 2016, "FINZ [€]"
    "euro": "eeg_zahlung",  # Amprion 2011
    "betrag": "eeg_zahlung",  # Amprion 2013, "BETRAG [EURO]"
    "betrag_euro": "eeg_zahlung",  # Amprion 2016
    "verguetung_euro": "eeg_zahlung",  # Amprion 2017
    "verguetung_fuer_in_abgenommene_eeg_einspeisung": "eeg_zahlung",  # TransnetBW 2016
    "eeg_einnahmen": "eeg_einnahmen",
    "vermiedene_netzentgelte": "vermiedene_netzentgelte",
    "vnne": "vermiedene_netzentgelte",  # TenneT 2011 and 2013 to 2016, 50Hertz 2014 to 2016
    "vne": "vermiedene_netzentgelte",  # Amprion 2011
    "vnne_euro": "vermiedene_netzentgelte",  # Amprion 2016 and 2017
    "vermiedene_netznutzungsentgelte": "vermiedene_netzentgelte",  # TransnetBW 2016
    "monat": "monat",
}

# Columns that are read but not kept: row numbers, the TSO itself (its name in TenneT 2011, its
# BDEW number in Amprion 2018 and 2019), the year (TenneT 2012), the Bemessungsleistung in Amprion's
# vNNE file of 2016, the biomass fuel in TenneT 2012 and the address of the plant in Amprion 2011.
IGNORED_COLUMNS = frozenset(
    {
        "id",
        "rownum",
        "zeilen_nr",
        "lfd_nr",
        "uenb",
        "uenb_bnr",
        "jahr",
        "bemessungsleistung_kw",
        "einsatzstoff_biomasse",
        "strasse",
        "plz",
        "ort",
        "bundesland",
        "tech",  # TransnetBW 2007 to 2010: energy source, not tariff category
        "energietraeger",  # 50Hertz 2010: plant attributes, not tariff categories
        "standort_der_anlage_ort_gemarkung",
        "strasse_flurstueck",
        "installierte_leistung",
        "einsatzstoffe_nach_8abs_2_bei_biomasseanlagen",
        "inbetriebnahmejahr",
        "einspeisespannungsebene",
    }
)
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
    # 2016 to 2019, except Amprion 2016, 2018 and 2019; TenneT 2011, 2013 and 2015, 50Hertz 2015
    _UP_TO_2019,
    _UP_TO_2019 - {"nb_bnr", "verguetungskategorie"},  # 50Hertz 2010: no tariff categories
    _UP_TO_2019 - {"nb_bnr"},  # 50Hertz 2014: grid operator by name only
    _UP_TO_2019 - {"nb_bnr", "netzbetreiber"},  # Amprion 2011: no grid operator
    _UP_TO_2019 - {"nb_bnr", "vermiedene_netzentgelte"},  # TenneT 2012: by name, no vNNE
    # Amprion 2016: payments and vNNE in two files; Amprion 2013: the payments file only
    frozenset({"anlagenschluessel", "verguetungskategorie", "strommenge", "eeg_zahlung"}),
    frozenset({"anlagenschluessel", "verguetungskategorie", "vermiedene_netzentgelte"}),
    # Amprion 2013 and 2014: fee files also retain the reported energy.
    frozenset(
        {"anlagenschluessel", "verguetungskategorie", "strommenge", "vermiedene_netzentgelte"}
    ),
    _AMPRION_2018,  # Amprion 2018 and 2019; TenneT 2014
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
        "qa_flags": pl.List(pl.String()),
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
        elif key in {"betrag", "betrag_euro"} and re.search(r"(?i)(?:^|[/_])vnne[_.]", source):
            mapped.append("vermiedene_netzentgelte")
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
