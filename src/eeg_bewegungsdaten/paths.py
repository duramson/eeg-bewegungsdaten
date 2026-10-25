"""Where downloaded and generated files live, and how they are written."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from eeg_bewegungsdaten.schema import OutdatedFileError

ENV_VAR = "EEG_BEWEGUNGSDATEN_DIR"


class Outputs(NamedTuple):
    """The files that `ingest` and `mastr` write for one year and TSO."""

    text: Path
    clean: Path
    quarantine: Path
    report: Path
    mastr: Path
    mastr_report: Path
    # Lists text, clean, quarantine and report while `ingest` replaces them; see `replace_together`.
    pending: Path


class UnfinishedIngestError(OutdatedFileError):
    """Output files of a year and TSO that `ingest` began to replace and did not finish: some are
    new, some old. A kind of `OutdatedFileError`, so that whatever refuses outdated files refuses
    these too."""


def default_data_dir() -> Path:
    """`$EEG_BEWEGUNGSDATEN_DIR`, or `~/.eeg-bewegungsdaten`."""
    configured = os.environ.get(ENV_VAR)
    return Path(configured).expanduser() if configured else Path.home() / ".eeg-bewegungsdaten"


def raw_dir(data_dir: Path) -> Path:
    """Archive of the original files, one folder per upload."""
    return data_dir / "raw"


def text_dir(data_dir: Path) -> Path:
    """Every line with all fields as text, ``text/<year>/<tso>.parquet``."""
    return data_dir / "text"


def clean_dir(data_dir: Path) -> Path:
    """Parquet files in the common schema, ``clean/<year>/<tso>.parquet``."""
    return data_dir / "clean"


def quarantine_dir(data_dir: Path) -> Path:
    """Lines that could not be read, ``quarantine/<year>/<tso>.parquet``."""
    return data_dir / "quarantine"


def mastr_dir(data_dir: Path) -> Path:
    """Match of every clean row to the MaStR, ``mastr/<year>/<tso>.parquet``."""
    return data_dir / "mastr"


def reports_dir(data_dir: Path) -> Path:
    """Row counts and quality codes per file, ``reports/<year>/<tso>.json``."""
    return data_dir / "reports"


def export_dir(data_dir: Path) -> Path:
    """Dataset to share, ``export/eeg-bewegungsdaten-<year>.parquet`` and the source notes."""
    return data_dir / "export"


def anlage_id_path(data_dir: Path) -> Path:
    """Random id per TSO and Anlagenschluessel. It leads from the export back to the
    Anlagenschluessel, so it stays outside `export_dir`."""
    return data_dir / "anlage_id.parquet"


def output_paths(data_dir: Path, jahr: int, uenb: str) -> Outputs:
    name = f"{jahr}/{uenb}"
    return Outputs(
        text=text_dir(data_dir) / f"{name}.parquet",
        clean=clean_dir(data_dir) / f"{name}.parquet",
        quarantine=quarantine_dir(data_dir) / f"{name}.parquet",
        report=reports_dir(data_dir) / f"{name}.json",
        mastr=mastr_dir(data_dir) / f"{name}.parquet",
        mastr_report=reports_dir(data_dir) / f"{name}.mastr.json",
        pending=reports_dir(data_dir) / f"{name}.pending.json",
    )


def write_atomic(path: Path, write: Callable[[Path], object]) -> None:
    """Let `write` fill a temporary file next to `path`, then rename it, so that an aborted run
    never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_text(path: Path, text: str) -> None:
    write_atomic(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def staged(path: Path) -> Path:
    """Where the new version of `path` waits until `replace_together` puts it in place."""
    return path.with_name(path.name + ".new")


def replace_together(data_dir: Path, pending: Path, paths: Sequence[Path]) -> None:
    """Replace each of `paths`, files in `data_dir`, with its `staged` version, as one step for
    the readers that call `check_finished`: write the list `pending`, rename, delete `pending`.
    If a rename fails, `pending` stays, and `finish_replacing` does the renames still to do."""
    write_json(pending, {"replace": [path.relative_to(data_dir).as_posix() for path in paths]})
    finish_replacing(data_dir, pending)


def finish_replacing(data_dir: Path, pending: Path) -> bool:
    """Do the renames that `pending` lists and that are still to do, then delete it; return
    whether it existed. A file without a staged version was renamed already."""
    if not pending.is_file():
        return False
    for name in json.loads(pending.read_text(encoding="utf-8"))["replace"]:
        path = data_dir / name
        if staged(path).exists():
            staged(path).replace(path)
    pending.unlink()
    return True


def check_finished(path: Path) -> None:
    """Raise `UnfinishedIngestError` if `ingest` began to replace the files of the year and TSO
    of `path` (``clean/<year>/<tso>.parquet`` and the like) and did not finish."""
    year, tso = path.parent.name, path.stem
    pending = reports_dir(path.parents[2]) / year / f"{tso}.pending.json"  # as in `output_paths`
    if pending.exists():
        raise UnfinishedIngestError(
            f"{year} {tso}: ingest stopped while it replaced the output files; run 'ingest' again"
        )


def utc_now() -> str:
    """The current time in UTC to the second, as written into metadata and reports."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
