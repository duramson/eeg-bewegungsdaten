"""Turn an archived upload (one ZIP, or all parts of a year) into Parquet files and a report.

Outputs per year and TSO:

- ``text/<year>/<tso>.parquet``: every line with all fields as text, as published
- ``clean/<year>/<tso>.parquet``: rows in the common schema, checked from the text layer
- ``quarantine/<year>/<tso>.parquet``: lines that could not be read, with error code
- ``reports/<year>/<tso>.json``: source file, row counts, codes and sums

Chunks are written as temporary Parquet parts and merged at the end, so memory stays bounded.
All four are written next to the old ones first, listed (`write_pending`) and then put in place
(`finish_replacing`): `scan`, `mastr` and `export` refuse a mix of old and new files.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

import polars as pl

from eeg_bewegungsdaten._version import __version__
from eeg_bewegungsdaten.download import ArchivedFile, archived_files
from eeg_bewegungsdaten.paths import (
    check_finished,
    clean_dir,
    default_data_dir,
    finish_replacing,
    output_paths,
    quarantine_dir,
    staged,
    text_dir,
    utc_now,
    write_atomic,
    write_json,
    write_pending,
)
from eeg_bewegungsdaten.progress import Progress
from eeg_bewegungsdaten.quality import NUMBER_COLUMNS, check
from eeg_bewegungsdaten.reader import (
    BLOCK_SIZE,
    data_size,
    iter_chunks,
    skipped_members,
    to_text,
)
from eeg_bewegungsdaten.schema import (
    CLEAN_SCHEMA,
    IGNORED_PREFIX,
    QUARANTINE_SCHEMA,
    TEXT_SCHEMA,
    FormatError,
    check_columns,
)

log = logging.getLogger(__name__)

Layer = Literal["text", "clean", "quarantine"]
_LAYER_DIRS = {"text": text_dir, "clean": clean_dir, "quarantine": quarantine_dir}
_LAYER_SCHEMAS = {"text": TEXT_SCHEMA, "clean": CLEAN_SCHEMA, "quarantine": QUARANTINE_SCHEMA}


def latest_uploads(data_dir: Path) -> list[list[ArchivedFile]]:
    """The newest upload per year and TSO, sorted by year and TSO.

    An upload is one archive folder: a single ZIP, or all parts of a year that a TSO split into
    several ZIPs (TenneT 2024), sorted by part. The newest upload is the one published last;
    without a publication date, the download date stands in for it. Older uploads that were
    archived later (TransnetBW 2020 of July 2021) therefore do not replace newer ones.

    Missing and truncated files count too, so that `ingest` refuses the newest upload instead of
    quietly falling back to an older one.
    """
    latest: dict[tuple[int, str], tuple[tuple[str, str], Path]] = {}
    folders: dict[Path, list[ArchivedFile]] = {}
    for archived in archived_files(data_dir, include_incomplete=True):
        meta = archived.meta
        if meta["kind"] != "bewegungsdaten":
            continue
        folders.setdefault(archived.path.parent, []).append(archived)
        order = (meta.get("published") or meta["downloaded_at"][:10], meta["downloaded_at"])
        key = (meta["year"], meta["uenb"])
        if key not in latest or order >= latest[key][0]:
            latest[key] = (order, archived.path.parent)
    return [
        sorted(folders[latest[key][1]], key=lambda a: (a.meta.get("part") or 0, str(a.path)))
        for key in sorted(latest)
    ]


def _check_parts(files: Sequence[ArchivedFile]) -> None:
    """Reject partial or mixed uploads before replacing any existing output."""
    if len({f.path.parent for f in files}) != 1:
        raise FormatError("Files from different uploads cannot be ingested together")
    for f in files:
        if not f.path.is_file() or f.path.stat().st_size != f.meta.get("size"):
            raise FormatError(
                f"{f.path}: archived file is missing or truncated; run 'download' again"
            )
    parts = [f.meta.get("part") for f in files]
    if all(part is None for part in parts):
        return
    expected = {part for f in files for part in f.meta.get("expected_parts", ())}
    if not expected:
        raise FormatError("Unknown multipart upload; run 'download' again to record all parts")
    if set(parts) != expected or len(parts) != len(expected):
        raise FormatError(
            f"Incomplete or duplicate upload parts: got {parts}, expected {sorted(expected)}; "
            "run 'download' again"
        )


def ingest(
    upload: ArchivedFile | Sequence[ArchivedFile],
    data_dir: Path,
    block_size: int = BLOCK_SIZE,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Read one upload (a ZIP or all its parts), write text layer, clean rows, quarantine and
    report; return the report. `progress` gets the uncompressed bytes of the CSV files read so
    far and their total; writing the outputs follows once all are read."""
    files = [upload] if isinstance(upload, ArchivedFile) else list(upload)
    if not files:
        raise ValueError("Nothing to ingest")
    jahr = int(files[0].meta["year"])
    uenb = str(files[0].meta["uenb"])
    outputs = output_paths(data_dir, jahr, uenb)
    replaced = [outputs.text, outputs.clean, outputs.quarantine, outputs.report]
    # The new files of a run that stopped while it replaced them are complete: put them in place
    # first, whatever happens to this run.
    if finish_replacing(data_dir, outputs.pending):
        log.warning("%s %s: finished replacing the files of an earlier ingest", jahr, uenb)
    _check_parts(files)
    log.debug("Reading %s %s from %s", jahr, uenb, ", ".join(str(f.path) for f in files))
    total = sum(data_size(f.path) for f in files) if progress is not None else None
    positions: dict[tuple[str, str], int] = {}
    if progress is not None:
        progress(0, total)

    members: dict[str, dict[str, Any]] = {}
    codes: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    sums: dict[str, float] = {}
    rows_raw = rows_clean = rows_quarantine = 0

    data_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{jahr}-{uenb}-", dir=data_dir))
    try:
        text_parts: list[Path] = []
        clean_parts: list[Path] = []
        quarantine_parts: list[Path] = []
        chunks = ((f, chunk) for f in files for chunk in iter_chunks(f.path, block_size))
        for i, (archived, chunk) in enumerate(chunks):
            text = to_text(chunk, jahr=jahr, uenb=uenb)
            clean, quarantine = check(text)
            amounts = [c for c in NUMBER_COLUMNS if c in chunk.columns]
            member = members.setdefault(
                chunk.member,
                {
                    "name": chunk.member,
                    "archive": archived.path.name,
                    "header": chunk.header,
                    "rows": 0,
                    "blank_lines": 0,
                    "empty_values": dict.fromkeys(amounts, 0),
                },
            )
            if member["archive"] != archived.path.name:
                # `quelldatei` and `zeile` must point to one line.
                raise FormatError(
                    f"{chunk.member} is in {member['archive']} and in {archived.path}"
                )
            member["rows"] += chunk.frame.height
            member["blank_lines"] += chunk.blank_lines
            if amounts:
                totals = clean.select(
                    *(pl.col(c).null_count().alias(f"empty_{c}") for c in amounts),
                    *(pl.col(c).sum() for c in amounts),
                ).row(0, named=True)
                for column in amounts:
                    member["empty_values"][column] += totals[f"empty_{column}"]
                    sums[column] = sums.get(column, 0.0) + totals[column]
            rows_raw += chunk.frame.height
            rows_clean += clean.height
            rows_quarantine += quarantine.height
            codes.update(dict(quarantine["fehlercode"].value_counts().iter_rows()))
            flags.update(dict(clean["hinweise"].explode().drop_nulls().value_counts().iter_rows()))

            text_parts.append(work / f"text-{i:05d}.parquet")
            clean_parts.append(work / f"clean-{i:05d}.parquet")
            quarantine_parts.append(work / f"quarantine-{i:05d}.parquet")
            # On disk, the whole line is only needed where the fields do not hold all of it: a
            # wrong number of fields, or a column that is not kept (`IGNORED_COLUMNS`).
            ignored = any(c is not None and c.startswith(IGNORED_PREFIX) for c in chunk.columns)
            text.with_columns(
                rohzeile=pl.when(
                    pl.lit(ignored) | (pl.col("felder") != pl.col("felder_kopf"))
                ).then("rohzeile")
            ).write_parquet(text_parts[-1])
            clean.write_parquet(clean_parts[-1])
            quarantine.write_parquet(quarantine_parts[-1])
            if progress is not None:
                positions[archived.path.name, chunk.member] = chunk.position
                progress(sum(positions.values()), total)

        if not members:
            raise FormatError(f"{', '.join(str(f.path) for f in files)}: no data files in the ZIP")
        assert rows_clean + rows_quarantine == rows_raw, (jahr, uenb)
        # The new files go next to the old ones first, so that a failure up to the end of this
        # block leaves the old ones as they are.
        _merge(text_parts, staged(outputs.text))
        _merge(clean_parts, staged(outputs.clean))
        _merge(quarantine_parts, staged(outputs.quarantine))
        report = {
            "jahr": jahr,
            "uenb": uenb,
            "sources": [
                {
                    key: f.meta.get(key)
                    for key in (
                        "url",
                        "filename",
                        "file_id",
                        "part",
                        "published",
                        "sha256",
                        "size",
                        "downloaded_at",
                    )
                }
                for f in files
            ],
            "rows": {"raw": rows_raw, "clean": rows_clean, "quarantine": rows_quarantine},
            "quarantine": dict(sorted(codes.items())),
            "qa_flags": dict(sorted(flags.items())),
            # None for amounts that the files do not have, such as EEG_Einnahmen before 2024.
            "sums_clean": {c: round(sums[c], 3) if c in sums else None for c in NUMBER_COLUMNS},
            "members": list(members.values()),
            "skipped_members": [name for f in files for name in skipped_members(f.path)],
            "outputs": {
                "text": str(outputs.text),
                "clean": str(outputs.clean),
                "quarantine": str(outputs.quarantine),
            },
            "created_at": utc_now(),
            "tool_version": __version__,
        }
        write_json(staged(outputs.report), report)
        write_pending(data_dir, outputs.pending, replaced)
    except BaseException:
        # Nothing is renamed yet. The list goes first: staged files without a list are never put
        # in place, while a list whose staged files were deleted would take them as renamed.
        outputs.pending.unlink(missing_ok=True)
        for path in replaced:
            staged(path).unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # Only the renames happen outside the block: the staged files must stay for the list.
    finish_replacing(data_dir, outputs.pending)
    return report


def _merge(parts: Iterable[Path], target: Path) -> None:
    """Concatenate Parquet parts into `target` in a streaming way, replacing it atomically."""
    write_atomic(target, pl.scan_parquet(list(parts)).sink_parquet)


def scan(data_dir: Path | None = None, *, layer: Layer = "clean") -> pl.LazyFrame:
    """One layer of every year and TSO as one LazyFrame: ``"clean"`` rows in the common
    schema, ``"quarantine"`` for lines that could not be read, ``"text"`` for every line with
    all fields as published. Raises `OutdatedFileError` if a file has the columns of an older
    version of the tool, and `UnfinishedIngestError` if `ingest` stopped while it replaced the
    files of a year and TSO."""
    data_dir = data_dir or default_data_dir()
    for path in sorted(_LAYER_DIRS[layer](data_dir).glob("*/*.parquet")):
        check_finished(path)
        check_columns(path, _LAYER_SCHEMAS[layer], "ingest")
    return pl.scan_parquet(_LAYER_DIRS[layer](data_dir) / "*" / "*.parquet")
