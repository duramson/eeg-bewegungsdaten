"""The datacard of an export: ``README.md`` in English, next to the Parquet files.

It is for whoever gets only the export, by mail, from a CKAN or on a share. It is written from the
same data as ``datapackage.json``: the content of ``sources.json`` and the column dictionary, so the
same input gives the same card. It has headings and lists, but no tables, as CKAN does not render
Markdown tables. Its links are absolute, as the card is read outside the repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import polars as pl

from eeg_bewegungsdaten.columns import AS_PUBLISHED, Column, describe
from eeg_bewegungsdaten.datapackage import (
    DATAPACKAGE_JSON,
    DL_DE_BY_2_0,
    EEG_DATA,
    PRIMARY_KEY,
    REPOSITORY,
    datapackage,
)
from eeg_bewegungsdaten.links import UENB_NAMES
from eeg_bewegungsdaten.mastr import HITS, MASTR_SCHEMA
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA
from eeg_bewegungsdaten.special_rows import SPECIAL_ROWS, generation, grid_feed_in

DATACARD = "README.md"
# Sections of the README of eeg-bewegungsdaten that the card links to
SUMMING_ENERGY = f"{REPOSITORY}#summing-energy"
ADD_MASTR_DATA = f"{REPOSITORY}#add-mastr-data"
PERSONAL_DATA = f"{REPOSITORY}#personal-data"


def datacard(content: dict[str, Any], schema: pl.Schema) -> str:
    """The datacard of an export, from the content of its ``sources.json`` and its schema."""
    package = datapackage(content, schema)
    years = sorted(int(year) for year in content["years"])
    notes = content["source_notes"]
    dates = ", ".join(content["mastr_export_dates"]) or "an unknown date"
    # The columns that come from `mastr`
    mastr = _join(
        [f"`{name}`" for name in schema if name in MASTR_SCHEMA and name not in CLEAN_SCHEMA]
    )
    lines = [
        f"# {package['title']}",
        "",
        package["description"],
        "",
        f"Written by [eeg-bewegungsdaten]({REPOSITORY}) {content['tool_version']} on "
        f"{content['created_at'][:10]}.",
        "",
        "## Files",
        "",
        *(_year(content, year) for year in years),
        "- `sources.txt`: the source notes to copy, in German, and the source files of the TSOs "
        "with their SHA-256.",
        "- `sources.json`: the same and the numbers above, as JSON.",
        f"- `{DATAPACKAGE_JSON}`: this description as metadata in OEMetadata v2, the standard of "
        "the Open Energy Platform.",
        "",
        "## Notes for use",
        "",
        "### Summing energy",
        "",
        "Do not add up `strommenge` over all rows: some rows repeat or deduct energy of other "
        "rows. The column `sonderzeile` marks them.",
        "",
        "- Energy fed into the grid: leave out the rows whose `sonderzeile` is "
        f"{_left_out(grid_feed_in())}. The sum of the other rows matches the energy of the EEG "
        "annual account.",
        "- Energy generated, grid feed-in plus self-consumption: leave out the rows whose "
        f"`sonderzeile` is {_left_out(generation())}.",
        "",
        f"See [Summing energy]({SUMMING_ENERGY}) in the README of eeg-bewegungsdaten.",
        "",
        "### MaStR data",
        "",
        f"- {mastr} come from the Marktstammdatenregister (MaStR), export of {dates}. Rows whose "
        f"`treffer` is {_join([f'`{code}`' for code in HITS], 'or')} count as matched.",
        "- The MaStR shows the state on the day of its export, not in the payment year. Plants "
        "deleted from the register since the payment year cannot be found.",
        "- So the export adds only what holds for the payment year: the EEG number and the energy "
        "source. Join capacity, location or operator yourself by `eeg_mastr_nr_abgeglichen`, not "
        "`eeg_mastr_nr`; they too show the day of the MaStR export. See "
        f"[Add MaStR data]({ADD_MASTR_DATA}).",
        "",
        "### Pseudonymous, not anonymous",
        "",
        "- `anlage_id` replaces the plant key of the TSOs (EEG-Anlagenschluessel), which may "
        "contain names of people or streets. It is drawn at random per TSO and plant key, so it "
        "cannot be computed from the key.",
        "- `quelldatei` and `zeile` lead to the line in the source file, plant key included. "
        "`eeg_mastr_nr` and `eeg_mastr_nr_abgeglichen` lead to the plant in the public MaStR.",
        "- Many rows belong to small PV plants of private persons. If you share data derived from "
        "this export, you process personal data and are responsible for it under the GDPR. Show "
        "plants up to 30 kW only in aggregates, and hide aggregates with fewer than 10 plants. "
        f"See [Personal data]({PERSONAL_DATA}).",
        "",
        "## Columns",
        "",
        f"The types are those of polars. {_join([f'`{name}`' for name in PRIMARY_KEY])} identify a "
        "row: the line of the source file it comes from.",
        "",
        *(_column(column, dtype) for column, dtype in describe(schema)),
        "",
        "## Licenses",
        "",
        f"- [{EEG_DATA['title']}]({EEG_DATA['path']}). {EEG_DATA['instruction']}",
        f"- [{DL_DE_BY_2_0['title']}]({DL_DE_BY_2_0['path']}) ({DL_DE_BY_2_0['name']}) for the "
        f"MaStR data in {mastr}. {DL_DE_BY_2_0['instruction']}",
        "",
        "## Source notes",
        "",
        "Name the sources when you share data derived from this export. The notes are those of "
        "`sources.txt`, in German like the licenses:",
        "",
        f"- {notes['eeg']}",
        f"- {notes['mastr']}",
    ]
    return "\n".join(lines) + "\n"


def _year(content: dict[str, Any], year: int) -> str:
    summary = content["years"][str(year)]
    files = [f for f in content["files"] if int(f["jahr"]) == year]
    uenbs = [UENB_NAMES[uenb] for uenb in UENB_NAMES if any(f["uenb"] == uenb for f in files)]
    rate = summary["match_rate"]
    return (
        f"- `{summary['file']}`: {year}, {summary['rows']:,} rows of {_join(uenbs)}; "
        f"{summary['anlage_ids']:,} plants (`anlage_id`), "
        f"{summary['rows_without_anlage_id']:,} rows without one"
        + ("" if rate is None else f"; {rate:.1%} of the rows matched with the MaStR")
        + "."
    )


def _column(column: Column, dtype: pl.DataType) -> str:
    """One item of the list, with the codes as a nested list (four spaces, as CKAN needs)."""
    unit = f", {column.unit}" if column.unit else ""
    description = column.description.removesuffix(AS_PUBLISHED)
    lines = [f"- `{column.name}`: {dtype}{unit}. {description}"]
    lines += [f"    - `{code}`: {meaning}" for code, meaning in column.codes.items()]
    return "\n".join(lines)


def _left_out(keep: pl.Expr) -> str:
    """The codes of `sonderzeile` whose rows the filter `keep` leaves out, taken from the filter."""
    codes = pl.DataFrame({"sonderzeile": list(SPECIAL_ROWS)}).filter(~keep)["sonderzeile"]
    return _join([f"`{code}`" for code in codes], "or")


def _join(items: Sequence[str], word: str = "and") -> str:
    """['a', 'b', 'c'] -> 'a, b and c'."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {word} {items[-1]}"
