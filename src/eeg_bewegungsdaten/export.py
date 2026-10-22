"""Write a dataset to share: the clean rows without the Anlagenschluessel, with the match to the
MaStR and a random id per plant.

Outputs, by default in ``export/``:

- ``eeg-bewegungsdaten-<year>.parquet``: one row per clean row of all TSOs of that year, with
  the columns of ``clean`` except ``anlagenschluessel``, a random ``anlage_id`` instead, and
  ``treffer``, ``eeg_mastr_nr_abgeglichen`` and ``energietraeger`` from ``mastr``
- ``sources.txt`` and ``sources.json``: source notes for the EEG data and the MaStR, and the
  source files with their SHA-256

``anlage_id`` is drawn at random for every TSO and Anlagenschluessel, so it cannot be computed
from the key. The mapping stays in ``anlage_id.parquet`` in the data folder, so that a key keeps
its id across years and runs. Nothing is read from ``text`` or ``quarantine``, whose raw lines
hold the Anlagenschluessel.

The export is pseudonymous, not anonymous: ``quelldatei`` and ``zeile`` lead to the line in the
source file.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import secrets
import shutil
import struct
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import polars as pl

from eeg_bewegungsdaten._version import __version__
from eeg_bewegungsdaten.links import PAGE_URL, UENB
from eeg_bewegungsdaten.mastr import HITS, MASTR_SCHEMA, MastrError, check_match, clean_files
from eeg_bewegungsdaten.paths import (
    anlage_id_path,
    export_dir,
    output_paths,
    utc_now,
    write_atomic,
    write_json,
    write_text,
)
from eeg_bewegungsdaten.progress import Progress
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA, OutdatedFileError, check_columns

log = logging.getLogger(__name__)

FILE_NAME = "eeg-bewegungsdaten-{}.parquet"
_FILE_PATTERN = re.compile(r"^eeg-bewegungsdaten-(\d{4})\.parquet$")
SOURCES_JSON = "sources.json"
SOURCES_TXT = "sources.txt"

MASTR_COLUMNS = ("treffer", "eeg_mastr_nr_abgeglichen", "energietraeger")
# The columns of `clean` without `anlagenschluessel`, and `MASTR_COLUMNS`. Listed one by one, so
# that a new column of `clean` is only exported after a check that it holds no personal data.
EXPORT_SCHEMA = pl.Schema(
    {
        "jahr": pl.Int16(),
        "uenb": pl.String(),
        "eeg_mastr_nr": pl.String(),
        "anlage_id": pl.Int64(),
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
        "treffer": pl.String(),
        "eeg_mastr_nr_abgeglichen": pl.String(),
        "energietraeger": pl.String(),
    }
)

ANLAGE_ID_SCHEMA = pl.Schema(
    {"uenb": pl.String(), "anlagenschluessel": pl.String(), "anlage_id": pl.Int64()}
)
# Ids have 12 digits at most, so they stay exact in JSON and spreadsheets.
ID_LIMIT = 10**12

UENB_NAMES = {
    "50hertz": "50Hertz Transmission GmbH",
    "amprion": "Amprion GmbH",
    "tennet": "TenneT TSO GmbH",
    "transnetbw": "TransnetBW GmbH",
}
EEG_NOTE = (
    "Datenquelle: EEG-Bewegungsdaten der Übertragungsnetzbetreiber {uenb}, {url}; Daten verändert "
    "(bereinigt, mit dem Marktstammdatenregister verknüpft, Anlagenschlüssel durch eine zufällige "
    "anlage_id ersetzt)."
)
# The official name, with en dashes.
DL_DE_BY = "Datenlizenz Deutschland – Namensnennung – Version 2.0"  # noqa: RUF001
# DL-DE-BY-2.0 asks to name the source, link the license and mark the changes.
MASTR_NOTE = (
    "Datenquelle: Marktstammdatenregister der Bundesnetzagentur, {export} vom {dates}, "
    f"https://www.marktstammdatenregister.de/MaStR/Datendownload, {DL_DE_BY} "
    "(www.govdata.de/dl-de/by-2-0); Daten verändert (bereinigt, verknüpft)."
)


class ExportError(RuntimeError):
    """Clean or mastr files are missing or do not fit together, the output folder holds other
    years or the id mapping, or the id mapping has other columns."""


def export(
    data_dir: Path,
    out_dir: Path | None = None,
    years: Iterable[int] | None = None,
    ids_path: Path | None = None,
    *,
    progress: Progress | None = None,
    on_year: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Write the export of `years` (default: every year with clean files) to `out_dir` (default:
    `export_dir`) and return the content of ``sources.json``. `ids_path` defaults to
    `anlage_id_path`. `progress` gets the clean files (years and TSOs) written so far and their
    number; `on_year` gets each year and its summary as soon as its file is written."""
    out_dir = out_dir or export_dir(data_dir)
    ids_path = ids_path or anlage_id_path(data_dir)
    if ids_path.resolve().is_relative_to(out_dir.resolve()):
        raise ExportError(f"{out_dir} holds {ids_path.name}, which must not be shared")
    chosen = set(years or ())
    files = [(jahr, uenb) for jahr, uenb in clean_files(data_dir) if not chosen or jahr in chosen]
    if not files:
        raise ExportError(f"No matching clean files in {data_dir}; run 'ingest' first")
    by_year: dict[int, list[str]] = {}
    for jahr, uenb in files:
        by_year.setdefault(jahr, []).append(uenb)
        paths = output_paths(data_dir, jahr, uenb)
        for path, command in (
            (paths.report, "ingest"),
            (paths.mastr, "mastr"),
            (paths.mastr_report, "mastr"),
        ):
            if not path.is_file():
                raise ExportError(f"{path} not found; run '{command}' first")
        try:
            check_columns(paths.clean, CLEAN_SCHEMA, "ingest")
            check_match(data_dir, jahr, uenb)
            check_columns(paths.mastr, MASTR_SCHEMA, "mastr")
        except (MastrError, OutdatedFileError) as e:
            raise ExportError(str(e)) from e
    others = sorted(
        path.name
        for path in out_dir.glob("*.parquet")
        if (match := _FILE_PATTERN.match(path.name)) and int(match[1]) not in by_year
    )
    if others:
        raise ExportError(
            f"{out_dir} holds the export of other years ({', '.join(others)}); "
            "remove them or choose another folder"
        )
    for jahr, uenbs in by_year.items():
        missing = [uenb for uenb in UENB if uenb not in uenbs]
        if missing:
            log.warning("%s: no clean files of %s", jahr, ", ".join(missing))

    # Unique per file first: half the memory of one unique over all rows.
    keys = pl.concat(
        pl.scan_parquet(output_paths(data_dir, jahr, uenb).clean)
        .select("uenb", "anlagenschluessel")
        .unique()
        for jahr, uenb in files
    )
    ids = _update_ids(ids_path, keys)
    out_dir.mkdir(parents=True, exist_ok=True)
    years_written: dict[str, dict[str, Any]] = {}
    written = 0
    if progress is not None:
        progress(0, len(files))
    for jahr, uenbs in by_year.items():
        path = out_dir / FILE_NAME.format(jahr)
        # One part per TSO, merged in a streaming way, so that memory stays at one clean file.
        work = Path(tempfile.mkdtemp(prefix=f".{jahr}-", dir=out_dir))
        try:
            parts = []
            for uenb in uenbs:
                parts.append(work / f"{uenb}.parquet")
                _rows(data_dir, jahr, uenb, ids).write_parquet(parts[-1])
                written += 1
                if progress is not None:
                    progress(written, len(files))
            write_atomic(path, pl.scan_parquet(parts).sink_parquet)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        years_written[str(jahr)] = _summary(path)
        if on_year is not None:
            on_year(jahr, years_written[str(jahr)])

    content = {
        "created_at": utc_now(),
        "tool_version": __version__,
        "years": years_written,
        **_sources(data_dir, files),
    }
    write_json(out_dir / SOURCES_JSON, content)
    write_text(out_dir / SOURCES_TXT, _sources_text(content))
    return content


def _update_ids(path: Path, keys: pl.LazyFrame) -> pl.DataFrame:
    """The mapping in `path` (`ANLAGE_ID_SCHEMA`), with a new random id for every `uenb` and
    `anlagenschluessel` of `keys` that it lacks; saved before it is returned."""
    ids = pl.read_parquet(path) if path.exists() else ANLAGE_ID_SCHEMA.to_frame()
    if ids.schema != ANLAGE_ID_SCHEMA:
        raise ExportError(f"{path}: expected the columns {ANLAGE_ID_SCHEMA}, found {ids.schema}")
    new = (
        keys.filter(pl.col("anlagenschluessel").is_not_null())
        .unique()
        .join(ids.lazy(), on=["uenb", "anlagenschluessel"], how="anti")
        .sort("uenb", "anlagenschluessel")
        .collect()
    )
    log.info(
        "anlage_id: %s Anlagenschluessel, %s new", f"{ids.height + new.height:,}", f"{new.height:,}"
    )
    if new.height:
        ids = pl.concat([ids, new.with_columns(_new_ids(new.height, ids["anlage_id"]))])
        write_atomic(path, ids.write_parquet)
    return ids


def _new_ids(n: int, taken: pl.Series) -> pl.Series:
    """`n` distinct random ids from 1 to `ID_LIMIT` - 1 that are not in `taken`."""
    schema = pl.Schema({"anlage_id": pl.Int64()})
    taken_frame = taken.to_frame("anlage_id")
    ids = schema.to_frame()
    while ids.height < n:
        need = n - ids.height
        drawn = struct.unpack(f"<{need}Q", secrets.token_bytes(8 * need))
        batch = pl.DataFrame({"anlage_id": [1 + x % (ID_LIMIT - 1) for x in drawn]}, schema=schema)
        ids = pl.concat([ids, batch.join(taken_frame, on="anlage_id", how="anti")]).unique()
    return ids.to_series()


def _rows(data_dir: Path, jahr: int, uenb: str, ids: pl.DataFrame) -> pl.DataFrame:
    """The export rows of one clean file, in its order."""
    key = ["jahr", "uenb", "quelldatei", "zeile"]
    paths = output_paths(data_dir, jahr, uenb)
    clean = pl.read_parquet(paths.clean)
    # `mastr` writes one row per clean row, in the same order: no join needed. `check_match`
    # knows the clean file the match was made from, but not whether the mastr file is whole.
    mastr = pl.read_parquet(paths.mastr, columns=[*key, *MASTR_COLUMNS])
    if not clean.select(key).equals(mastr.select(key)):
        raise ExportError(
            f"{jahr} {uenb}: the mastr file does not fit the clean file; run 'mastr' again"
        )
    plants = ids.filter(pl.col("uenb") == uenb)
    anlage_id = pl.col("anlagenschluessel").replace_strict(
        plants["anlagenschluessel"], plants["anlage_id"], default=None, return_dtype=pl.Int64()
    )
    return (
        clean.hstack(mastr.select(MASTR_COLUMNS).get_columns())
        .with_columns(anlage_id=anlage_id)
        .select(EXPORT_SCHEMA.names())
    )


def _summary(path: Path) -> dict[str, Any]:
    """Rows, plants and share of rows matched to the MaStR in one exported file."""
    anlage_id = pl.col("anlage_id")
    rows, plants, without_id, hits = (
        pl.scan_parquet(path)
        .select(
            pl.len(),
            plants=anlage_id.drop_nulls().n_unique(),
            without_id=anlage_id.null_count(),
            hits=pl.col("treffer").is_in(HITS).sum(),
        )
        .collect()
        .row(0)
    )
    return {
        "file": path.name,
        "rows": rows,
        "anlage_ids": plants,
        "rows_without_anlage_id": without_id,
        "match_rate": round(hits / rows, 6) if rows else None,
    }


def _sources(data_dir: Path, files: Sequence[tuple[int, str]]) -> dict[str, Any]:
    """Source notes, MaStR export dates and source files, from the reports of `ingest` and
    `mastr`."""
    source_files: list[dict[str, Any]] = []
    dates: set[str] = set()
    for jahr, uenb in files:
        paths = output_paths(data_dir, jahr, uenb)
        report = json.loads(paths.report.read_text(encoding="utf-8"))
        source_files += [
            {
                "jahr": jahr,
                "uenb": uenb,
                **{
                    key: source[key]
                    for key in ("filename", "url", "source_url", "published", "sha256")
                    if source.get(key)
                },
            }
            for source in report["sources"]
        ]
        mastr_report = json.loads(paths.mastr_report.read_text(encoding="utf-8"))
        dates.update(
            date
            for table in mastr_report["mastr"]["tables"].values()
            for date in table["datum_download"]
        )
    export_dates = sorted({_iso_date(date) for date in dates})
    if not export_dates:
        log.warning(
            "The mastr reports give no MaStR export date; fill in TT.MM.JJJJ in %s", SOURCES_TXT
        )
    german = [dt.date.fromisoformat(date).strftime("%d.%m.%Y") for date in export_dates]
    uenbs = [UENB_NAMES[uenb] for uenb in UENB if any(u == uenb for _, u in files)]
    return {
        "source_notes": {
            "eeg": EEG_NOTE.format(uenb=_enumerate(uenbs), url=PAGE_URL),
            "mastr": MASTR_NOTE.format(
                export="Gesamtdatenexporte" if len(german) > 1 else "Gesamtdatenexport",
                dates=_enumerate(german or ["TT.MM.JJJJ"]),
            ),
        },
        "mastr_export_dates": export_dates,
        "files": source_files,
    }


def _sources_text(content: dict[str, Any]) -> str:
    notes = content["source_notes"]
    created, version = content["created_at"][:10], content["tool_version"]
    lines = [
        "EEG-Bewegungsdaten, bereinigt und mit dem Marktstammdatenregister verknüpft",
        f"Erstellt am {created} mit eeg-bewegungsdaten {version}",
        "",
        "EEG-Bewegungsdaten",
        notes["eeg"],
        "",
        "Marktstammdatenregister (Spalten treffer, eeg_mastr_nr_abgeglichen, energietraeger)",
        notes["mastr"],
        "",
        "Quelldateien (Jahr, ÜNB, veröffentlicht, SHA-256, URL)",
        *(
            f"{f['jahr']}  {f['uenb']:<11} {f.get('published', '-'):<10}  {f['sha256']}  {f['url']}"
            for f in content["files"]
        ),
    ]
    return "\n".join(lines) + "\n"


def _enumerate(items: Sequence[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b und c'."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} und {items[-1]}"


def _iso_date(value: str) -> str:
    """open-MaStR's `DatumDownload`, '20261017', as '2026-10-17'."""
    digits = re.sub(r"\D", "", value)[:8]
    return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
