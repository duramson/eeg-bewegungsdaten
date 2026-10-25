"""What every column of the output means: the column dictionary.

One entry per column name, for all layers (`text`, `clean`, `quarantine`, `mastr` and the export):
its meaning, unit, the column of the TSOs' legend it comes from, and for columns that hold codes
the allowed codes with their meaning. The types are those of the schemas; the codes come from the
lists the checks use, so they cannot drift apart. The export's metadata and the docs are written
from here. Names follow the rule in CONTRIBUTING.md: columns German, descriptions and codes
English.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from eeg_bewegungsdaten.links import UENB_NAMES
from eeg_bewegungsdaten.mastr import MATCH_CODES
from eeg_bewegungsdaten.quality import QA_FLAGS, QUARANTINE_CODES
from eeg_bewegungsdaten.special_rows import SPECIAL_ROWS


@dataclass(frozen=True)
class Column:
    name: str
    description: str
    unit: str | None = None
    """`kWh` or `EUR`; None for columns without a unit"""
    legend: str | None = None
    """The column of the TSOs' legend that the values come from"""
    codes: Mapping[Any, str] = field(default_factory=dict)
    """The values the column may hold, with their meaning; empty for free values"""


VERAEUSSERUNGSFORM = {
    1: "Einspeiseverguetung: feed-in tariff",
    2: "Marktpraemie: market premium",
    3: "Mieterstromzuschlag: tenant electricity supplement",
    4: "Ausfallverguetung: fallback payment",
    5: "Sonstiges: other",
}
ENERGIETRAEGER = {
    "solar": "solar",
    "wind": "wind, onshore and offshore",
    "biomass": "biomass",
    "hydro": "hydropower",
    "gsgk": "geothermal energy, mine gas and pressure relief",
    "storage": "storage",
}
KODIERUNG = {
    "utf-8": "the line is valid UTF-8",
    "cp1252": "the line was decoded as cp1252",
    "undecodable": "the line is neither UTF-8 nor cp1252",
}

# Ends the description of the columns that `text` holds as published; the datacard leaves it out.
AS_PUBLISHED = (
    " In `text`, the field as published: not trimmed, empty fields are '', columns the file "
    "does not have are null."
)

COLUMNS: dict[str, Column] = {
    column.name: column
    for column in (
        Column("jahr", "Year of the data, from the file name."),
        Column(
            "uenb", "Transmission system operator (TSO) that published the file.", codes=UENB_NAMES
        ),
        Column(
            "quelldatei",
            "File inside the ZIP that holds the line (TenneT 2023: a TXT file). With `zeile`, it "
            "traces every row to its line as published.",
        ),
        Column("zeile", "Line in `quelldatei`; the header is line 1."),
        Column("kodierung", "Encoding of the line.", codes=KODIERUNG),
        Column("felder", "Number of fields in the line."),
        Column("felder_kopf", "Number of fields in the header."),
        Column(
            "eeg_mastr_nr",
            "EEG number of the plant in the Marktstammdatenregister (MaStR), as the TSO "
            "published it, from 2020; see `hinweise`. The number found in the MaStR is "
            "`eeg_mastr_nr_abgeglichen`." + AS_PUBLISHED,
            legend="EEG_Mastr_Nr",
        ),
        Column(
            "anlagenschluessel",
            "Plant key (EEG-Anlagenschluessel), free text; it may contain names of people or "
            "streets, so the export leaves it out." + AS_PUBLISHED,
            legend="EEG_Anlagenschluessel",
        ),
        Column(
            "nb_mastr_nr",
            "MaStR number of the grid operator, from 2020." + AS_PUBLISHED,
            legend="NB_Mastr_Nr",
        ),
        Column(
            "nb_bnr",
            "BDEW number of the grid operator: most files up to 2023, TenneT 2025." + AS_PUBLISHED,
            legend="NB_BNR",
        ),
        Column(
            "netzbetreiber",
            "Name of the grid operator: the files with `nb_bnr`, except Amprion 2018 and 2019."
            + AS_PUBLISHED,
            legend="Netzbetreiber",
        ),
        Column(
            "uenb_mastr_nr",
            "MaStR number of the TSO, from 2020." + AS_PUBLISHED,
            legend="UENB_Mastr_Nr",
        ),
        Column(
            "veraeusserungsform",
            "Form of sale: how the electricity was sold and paid. Null before 2020, except "
            "Amprion 2018 (column TYP)." + AS_PUBLISHED,
            legend="Veraeusserungsform",
            codes=VERAEUSSERUNGSFORM,
        ),
        Column(
            "verguetungskategorie",
            "Payment category of the TSOs' legend." + AS_PUBLISHED,
            legend="Verguetungskategorie",
        ),
        Column(
            "sonderzeile",
            "Kind of row whose amounts need care when you add them up; null for ordinary rows. "
            "Do not sum `strommenge` over all rows.",
            codes={code: meaning for code, (_, meaning) in SPECIAL_ROWS.items()},
        ),
        Column(
            "strommenge",
            "Energy of the row. Some rows repeat or deduct energy of other rows, see "
            "`sonderzeile`. The legend states no unit; the sums match the EEG annual account in "
            "kWh." + AS_PUBLISHED,
            unit="kWh",
            legend="Strommenge",
        ),
        Column(
            "eeg_zahlung",
            "EEG payment to the plant operator." + AS_PUBLISHED,
            unit="EUR",
            legend="EEG_Zahlung",
        ),
        Column(
            "eeg_einnahmen",
            "EEG receipts, from 2024." + AS_PUBLISHED,
            unit="EUR",
            legend="EEG_Einnahmen",
        ),
        Column(
            "vermiedene_netzentgelte",
            "Avoided grid fees (vNNE), until 2023." + AS_PUBLISHED,
            unit="EUR",
            legend="Vermiedene_Netzentgelte",
        ),
        Column(
            "monat",
            "Month 1 to 12, or 0 for the annual value; always 0 before 2021. Other values, or "
            "null, come with `MONAT_INVALID` in `hinweise`." + AS_PUBLISHED,
            legend="Monat",
        ),
        Column(
            "hinweise",
            "Quality notes on a row that is kept; empty for most rows.",
            codes=QA_FLAGS,
        ),
        Column(
            "zusatzfelder",
            "Fields under an empty header name, such as the two extra columns of Amprion's "
            "`ausgefoerdert.csv`; null if the file has none.",
        ),
        Column(
            "rohzeile",
            "The whole line as decoded. In `text` only if `felder` differs from `felder_kopf` or "
            "the file has a column that is not kept; otherwise the fields hold the whole line. In "
            "`quarantine` always.",
        ),
        Column(
            "fehlercode",
            "Why the line is in quarantine: the first check it fails.",
            codes=QUARANTINE_CODES,
        ),
        Column("detail", "The value that failed the check, e.g. `strommenge='12.5'`."),
        Column("treffer", "How the row was matched with the MaStR.", codes=MATCH_CODES),
        Column(
            "eeg_mastr_nr_abgeglichen",
            "`EegMastrNummer` of the plant found in the MaStR; null unless exactly one plant was "
            "found. Use it, not `eeg_mastr_nr`, to join MaStR data.",
        ),
        Column(
            "eeg_mastr_nrs_abgeglichen",
            "All EEG numbers found in the MaStR, only for `MULTIPLE`.",
        ),
        Column(
            "energietraeger",
            "Energy source of the plant found, from the MaStR table it is in.",
            codes=ENERGIETRAEGER,
        ),
        Column(
            "anlage_id",
            "Random number per TSO and plant key, in place of `anlagenschluessel`; the same in "
            "every year of one export. Null if the row has no plant key. Pseudonymous, not "
            "anonymous: with the MaStR number, the plant can be found.",
        ),
    )
}


def describe(schema: pl.Schema) -> list[tuple[Column, pl.DataType]]:
    """The entry and type of every column of `schema`, in its order. KeyError for a column without
    an entry."""
    return [(COLUMNS[name], dtype) for name, dtype in schema.items()]
