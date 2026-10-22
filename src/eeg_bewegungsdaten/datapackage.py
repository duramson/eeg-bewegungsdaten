"""The metadata of an export: ``datapackage.json`` in OEMetadata v2.

OEMetadata is the metadata standard of the Open Energy Platform and builds on the Frictionless
Data Package, so general tools read it too. There is one resource per exported year; its fields
come from the column dictionary (`columns.py`), its sources are the files of the TSOs that year
and the MaStR export. The EEG data have no settled license, and the metadata say so instead of
naming one. No person is named unless `export` is given a contributor: whoever publishes an export
adds themselves; the tool is named as its context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from eeg_bewegungsdaten.columns import describe
from eeg_bewegungsdaten.links import UENB_NAMES

CONTEXT = (
    "https://raw.githubusercontent.com/OpenEnergyPlatform/oemetadata/production/oemetadata/v2/"
    "v20/context.json"
)
METADATA_VERSION = "OEMetadata-2.0.4"
DATAPACKAGE_JSON = "datapackage.json"
REPOSITORY = "https://github.com/duramson/eeg-bewegungsdaten"
MASTR_DOWNLOAD = "https://www.marktstammdatenregister.de/MaStR/Datendownload"
# The row of the export: year, TSO and the line in the file as published.
PRIMARY_KEY = ("jahr", "uenb", "quelldatei", "zeile")

DL_DE_BY_2_0 = {
    "name": "DL-DE-BY-2.0",
    "title": "Datenlizenz Deutschland – Namensnennung – Version 2.0",  # noqa: RUF001
    "path": "https://www.govdata.de/dl-de/by-2-0",
    "instruction": "You may use, change and share the data, but must name the source, link the "
    "license and mark the changes.",
}
EEG_DATA = {
    "name": None,
    "title": "EEG-Bewegungsdaten: no settled license",
    "path": f"{REPOSITORY}#data-license",
    "instruction": "The TSOs publish the files under section 51 EnFG without a license. They hold "
    "personal data. Read 'Data license' in the README of eeg-bewegungsdaten before you share "
    "anything derived from them.",
}
CC0 = {
    "name": "CC0-1.0",
    "title": "Creative Commons Zero v1.0 Universal",
    "path": "https://creativecommons.org/publicdomain/zero/1.0",
}

# Polars types as the database types that the OEP uses
_TYPES: dict[type[pl.DataType], str] = {
    pl.Int8: "smallint",
    pl.Int16: "smallint",
    pl.Int32: "integer",
    pl.Int64: "bigint",
    pl.Float64: "double precision",
    pl.String: "text",
}


@dataclass(frozen=True)
class Contributor:
    """The person who publishes an export, e.g. on a CKAN."""

    name: str
    url: str | None = None


def datapackage(
    content: dict[str, Any], schema: pl.Schema, contributor: Contributor | None = None
) -> dict[str, Any]:
    """The OEMetadata of an export, from the content of its ``sources.json`` and its schema."""
    years = sorted(int(year) for year in content["years"])
    span = f"{years[0]}" if len(years) == 1 else f"{years[0]} to {years[-1]}"
    return {
        "@context": CONTEXT,
        "name": "eeg_bewegungsdaten",
        "title": f"EEG-Bewegungsdaten {span}, cleaned and matched with the Marktstammdatenregister",
        "description": (
            "Per-plant payments under the German Renewable Energy Sources Act (EEG) as the four "
            f"transmission system operators published them, {span}: cleaned into one schema, "
            "matched with the Marktstammdatenregister (MaStR), the plant key replaced by a random "
            "anlage_id. One Parquet file per year."
        ),
        "resources": [_resource(content, schema, year, contributor) for year in years],
        "metaMetadata": {"metadataVersion": METADATA_VERSION, "metadataLicense": CC0},
    }


def _resource(
    content: dict[str, Any], schema: pl.Schema, year: int, contributor: Contributor | None
) -> dict[str, Any]:
    summary = content["years"][str(year)]
    created = content["created_at"][:10]
    files = [f for f in content["files"] if int(f["jahr"]) == year]
    uenbs = [UENB_NAMES[uenb] for uenb in UENB_NAMES if any(f["uenb"] == uenb for f in files)]
    return {
        "name": f"eeg_bewegungsdaten_{year}",
        "title": f"EEG-Bewegungsdaten {year}",
        "path": summary["file"],
        "description": (
            f"{summary['rows']:,} rows of {', '.join(uenbs)}; annual values, monthly ones from "
            f"2021. Written by eeg-bewegungsdaten {content['tool_version']} on {created}."
        ),
        "languages": ["en-GB"],
        "keywords": ["EEG", "renewable energy", "payments", "Marktstammdatenregister", "Germany"],
        "publicationDate": created,
        "context": {
            "title": "eeg-bewegungsdaten",
            "homepage": REPOSITORY,
            "documentation": f"{REPOSITORY}#readme",
            "sourceCode": REPOSITORY,
        },
        "spatial": {"extent": {"name": "Germany", "@id": "https://www.wikidata.org/wiki/Q183"}},
        "temporal": {
            "referenceDate": f"{year}-12-31",
            "timeseries": [
                {
                    "start": f"{year}-01-01T00:00:00+01:00",
                    "end": f"{year}-12-31T23:59:59+01:00",
                    "resolutionValue": "1",
                    "resolutionUnit": "year" if year < 2021 else "month",
                    "aggregationType": "sum",
                }
            ],
        },
        "sources": [*map(_tso_source, files), _mastr_source(content)],
        "licenses": [
            EEG_DATA | {"attribution": content["source_notes"]["eeg"]},
            _mastr_license(content),
        ],
        "contributors": [] if contributor is None else [_contributor(contributor, created)],
        "type": "table",
        "format": "Parquet",
        "encoding": "UTF-8",
        "schema": {"fields": _fields(schema), "primaryKey": list(PRIMARY_KEY)},
    }


def _tso_source(file: dict[str, Any]) -> dict[str, Any]:
    name = UENB_NAMES[file["uenb"]]
    published = file.get("published") or ""
    return {
        "title": f"EEG-Bewegungsdaten {file['jahr']}, {name}: {file['filename']}",
        "authors": [name],
        "description": f"SHA-256 {file['sha256']}"
        + (f", downloaded from {file['source_url']}" if file.get("source_url") else ""),
        "publicationYear": published[:4] or None,
        "path": file["url"],
        "sourceLicenses": [EEG_DATA],
    }


def _mastr_source(content: dict[str, Any]) -> dict[str, Any]:
    dates = content["mastr_export_dates"]
    return {
        "title": "Marktstammdatenregister, Gesamtdatenexport",
        "authors": ["Bundesnetzagentur"],
        "description": "Export of " + (", ".join(dates) or "an unknown date"),
        "publicationYear": dates[-1][:4] if dates else None,
        "path": MASTR_DOWNLOAD,
        "sourceLicenses": [_mastr_license(content)],
    }


def _mastr_license(content: dict[str, Any]) -> dict[str, Any]:
    return DL_DE_BY_2_0 | {"attribution": content["source_notes"]["mastr"]}


def _contributor(contributor: Contributor, date: str) -> dict[str, Any]:
    return {
        "title": contributor.name,
        "path": contributor.url,
        "roles": ["DataCurator"],
        "date": date,
        "object": "data",
        "comment": "Export with eeg-bewegungsdaten",
    }


def _fields(schema: pl.Schema) -> list[dict[str, Any]]:
    fields = []
    for column, dtype in describe(schema):
        field: dict[str, Any] = {
            "name": column.name,
            "description": column.description,
            "type": _type(dtype),
            "nullable": column.name not in PRIMARY_KEY,
            "unit": column.unit,
        }
        if column.codes:
            field["valueReference"] = [
                {"value": str(value), "name": meaning, "@id": None}
                for value, meaning in column.codes.items()
            ]
        fields.append(field)
    return fields


def _type(dtype: Any) -> str:
    """A polars type, or the class of one (the inner type of a list)."""
    if isinstance(dtype, pl.List):
        return _type(dtype.inner) + "[]"
    return _TYPES[dtype if isinstance(dtype, type) else type(dtype)]
