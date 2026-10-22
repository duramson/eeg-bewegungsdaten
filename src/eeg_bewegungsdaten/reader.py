"""Stream the CSV files inside a Bewegungsdaten ZIP without unpacking it.

The files are UTF-8 or cp1252 (most files up to 2023, Amprion and TransnetBW until 2025). Blocks
that are valid UTF-8 are decoded in one go; only the others are decoded line by line, so every
line gets the right encoding and is marked if it needed the fallback.

The data are in the CSV files of the ZIP; TenneT 2023 ships a single TXT file instead. 50Hertz
2014 and 2015 are a bare CSV file instead of a ZIP, read like a single member.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import polars as pl

from eeg_bewegungsdaten.schema import TEXT_COLUMNS, TEXT_SCHEMA, FormatError, map_header

# Keeps peak memory near 1 GB for the largest file so far (TenneT 2025, 3.8 million lines).
BLOCK_SIZE = 8 << 20

# Lines without any value: empty, or only separators, quotes and spaces (TransnetBW 2021 ends with
# 168,294 lines ';;;;;;;;;;;' that fill the sheet up to Excel's row limit).
_BLANK = r'^[\s;"]*$'


@dataclass(frozen=True)
class Chunk:
    """Consecutive lines of one CSV file inside the ZIP.

    `frame` has the columns `zeile` (line number in the CSV file, header = 1), `rohzeile`
    (decoded line without line break), `cp1252` and `undecodable`. Lines without any value are
    left out and only counted in `blank_lines`.
    """

    member: str
    header: list[str]
    columns: list[str | None]
    frame: pl.DataFrame
    blank_lines: int


def iter_chunks(path: Path, block_size: int = BLOCK_SIZE) -> Iterator[Chunk]:
    """Yield the data lines of every data file in the ZIP at `path`, in archive order, or of
    `path` itself when it is a bare CSV file.

    A file without data lines still yields one empty chunk, so it shows up in reports.
    """
    if _is_bare_csv(path):
        with path.open("rb") as stream:
            yield from _read_member(path.name, stream, block_size)
        return
    with zipfile.ZipFile(path) as archive:
        for info in _data_members(archive):
            with archive.open(info) as stream:
                yield from _read_member(info.filename, stream, block_size)


def to_text(chunk: Chunk, *, jahr: int, uenb: str) -> pl.DataFrame:
    """Split the lines of `chunk` into the columns of `TEXT_SCHEMA`.

    Fields stay exactly as published, without trimming. Columns that the file does not have
    are null. Fields under an empty header name go to `zusatzfelder`; columns that are read but
    not kept (`IGNORED_COLUMNS`) are left out. `rohzeile` holds every line here; `ingest` keeps
    it on disk only where the fields do not hold the whole line.
    """
    if chunk.frame.is_empty():
        return TEXT_SCHEMA.to_frame()
    names = [c if c is not None else f"_extra_{i}" for i, c in enumerate(chunk.columns)]
    extras = [n for n in names if n.startswith("_extra_")]
    return (
        chunk.frame.with_columns(
            _parts=pl.col("rohzeile")
            .str.split_exact(";", len(names) - 1)
            .struct.rename_fields(names),
        )
        .unnest("_parts")
        .select(
            jahr=pl.lit(jahr),
            uenb=pl.lit(uenb),
            quelldatei=pl.lit(chunk.member),
            zeile="zeile",
            kodierung=pl.when(pl.col("undecodable"))
            .then(pl.lit("undecodable"))
            .when(pl.col("cp1252"))
            .then(pl.lit("cp1252"))
            .otherwise(pl.lit("utf-8")),
            felder=pl.col("rohzeile").str.count_matches(";", literal=True) + 1,
            felder_kopf=pl.lit(len(names)),
            **{c: pl.col(c) if c in names else pl.lit(None, pl.String) for c in TEXT_COLUMNS},
            zusatzfelder=pl.concat_list(extras) if extras else pl.lit(None, pl.List(pl.String)),
            rohzeile="rohzeile",
        )
        .cast(TEXT_SCHEMA)
    )


def skipped_members(path: Path) -> list[str]:
    """Files in the ZIP that are neither folders nor data files; none for a bare CSV file."""
    if _is_bare_csv(path):
        return []
    with zipfile.ZipFile(path) as archive:
        data = {info.filename for info in _data_members(archive)}
        return [
            info.filename
            for info in archive.infolist()
            if not info.is_dir() and info.filename not in data
        ]


def _is_bare_csv(path: Path) -> bool:
    return path.suffix.lower() == ".csv"


def _data_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The CSV files, or the TXT files if there is no CSV file; reject duplicate full names."""
    files = [info for info in archive.infolist() if not info.is_dir()]
    for suffix in (".csv", ".txt"):
        found = [info for info in files if info.filename.lower().endswith(suffix)]
        if found:
            seen: set[str] = set()
            for info in found:
                if info.filename in seen:
                    raise FormatError(f"{archive.filename}: duplicate data member {info.filename}")
                seen.add(info.filename)
            return found
    return []


def _read_member(member: str, stream: IO[bytes], block_size: int) -> Iterator[Chunk]:
    header: list[str] | None = None
    columns: list[str | None] = []
    next_line = 1
    for block in _blocks(stream, block_size):
        lines, cp1252, undecodable = _decode(block)
        skip = 0
        if header is None:
            header = lines[0].removeprefix("\ufeff").removesuffix("\r").split(";")
            columns = map_header(header, member)
            skip = 1
        frame = _frame(lines[skip:], next_line + skip, cp1252, undecodable, skip)
        next_line += len(lines)
        blank = frame["rohzeile"].str.contains(_BLANK)
        yield Chunk(member, header, columns, frame.filter(~blank), int(blank.sum()))
    if header is None:
        yield Chunk(member, [], [], _frame([], 1, [], [], 0), 0)


def _frame(
    lines: list[str], first_line: int, cp1252: list[int], undecodable: list[int], skip: int
) -> pl.DataFrame:
    frame = pl.DataFrame({"rohzeile": lines}, schema={"rohzeile": pl.String}).with_row_index("i")
    return frame.select(
        zeile=(pl.col("i") + first_line).cast(pl.Int32),
        rohzeile=pl.col("rohzeile").str.strip_suffix("\r"),
        cp1252=pl.col("i").is_in([i - skip for i in cp1252]),
        undecodable=pl.col("i").is_in([i - skip for i in undecodable]),
    )


def _blocks(stream: IO[bytes], block_size: int) -> Iterator[bytes]:
    """Blocks of whole lines; the line break at the end of each block is removed."""
    rest = b""
    while data := stream.read(block_size):
        data = rest + data
        cut = data.rfind(b"\n")
        if cut == -1:
            rest = data
            continue
        yield data[:cut]
        rest = data[cut + 1 :]
    if rest:
        yield rest


def _decode(block: bytes) -> tuple[list[str], list[int], list[int]]:
    """Split a block into lines. Returns the lines and the indices of lines that needed cp1252
    or could not be decoded at all."""
    try:
        return block.decode("utf-8").split("\n"), [], []
    except UnicodeDecodeError:
        pass
    lines: list[str] = []
    cp1252: list[int] = []
    undecodable: list[int] = []
    for i, raw in enumerate(block.split(b"\n")):
        try:
            lines.append(raw.decode("utf-8"))
            continue
        except UnicodeDecodeError:
            pass
        try:
            lines.append(raw.decode("cp1252"))
            cp1252.append(i)
        except UnicodeDecodeError:
            lines.append(raw.decode("cp1252", errors="replace"))
            undecodable.append(i)
    return lines, cp1252, undecodable
