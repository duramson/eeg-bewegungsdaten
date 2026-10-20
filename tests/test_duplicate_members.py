"""Duplicate selected ZIP members must not produce ambiguous row identifiers."""

from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
import polars as pl
import pytest

from eeg_bewegungsdaten.download import download
from eeg_bewegungsdaten.ingest import ingest
from eeg_bewegungsdaten.paths import output_paths
from eeg_bewegungsdaten.reader import iter_chunks, skipped_members
from eeg_bewegungsdaten.schema import FormatError
from tests.builders import HEADER, archive, classified, csv, make_zip, row


def duplicate_zip(path: Path, member: str, others: dict[str, bytes] | None = None) -> Path:
    make_zip(path, {**(others or {}), member: csv(HEADER, row(energy="10"))})
    with (
        zipfile.ZipFile(path, "a") as zipped,
        pytest.warns(UserWarning, match="Duplicate name"),
    ):
        zipped.writestr(member, csv(HEADER, row(energy="20")))
    return path


@pytest.mark.parametrize("suffix", [".csv", ".CSV", ".txt", ".TXT"])
def test_duplicate_members_are_rejected_before_the_first_chunk(tmp_path: Path, suffix: str) -> None:
    member = f"data/part{suffix}"
    path = duplicate_zip(tmp_path / "duplicate.zip", member, {f"first{suffix}": csv(HEADER, row())})

    with pytest.raises(FormatError) as error:
        next(iter_chunks(path))

    assert str(path) in str(error.value)
    assert member in str(error.value)


@pytest.mark.parametrize("member", ["readme.txt", "notes.pdf", "folder/"])
def test_duplicate_unselected_members_are_still_skipped(tmp_path: Path, member: str) -> None:
    path = duplicate_zip(tmp_path / "skipped.zip", member, {"data.csv": csv(HEADER, row())})

    chunks = list(iter_chunks(path))

    assert [chunk.member for chunk in chunks] == ["data.csv"]
    assert skipped_members(path) == ([] if member.endswith("/") else [member, member])


@pytest.mark.parametrize("suffix", [".csv", ".txt"])
def test_duplicate_members_do_not_replace_existing_outputs(tmp_path: Path, suffix: str) -> None:
    data_dir = tmp_path / "data"
    ingest(archive(data_dir, {"previous.csv": csv(HEADER, row())}), data_dir)
    outputs = output_paths(data_dir, 2025, "amprion")
    written = (outputs.text, outputs.clean, outputs.quarantine, outputs.report)
    before = {path: path.read_bytes() for path in written}
    member = f"data{suffix}"
    path = duplicate_zip(tmp_path / "duplicate.zip", member)
    link = classified("https://example.com/amprion-bewegungsdaten-2025.zip")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=path.read_bytes()))
    with httpx.Client(transport=transport) as client:
        downloaded = download(link, data_dir, client)

    with pytest.raises(FormatError) as error:
        ingest(downloaded, data_dir)

    assert str(downloaded.path) in str(error.value)
    assert member in str(error.value)
    assert {path: path.read_bytes() for path in written} == before
    assert not any(path.name.startswith(".") for path in data_dir.iterdir())


def test_distinct_full_member_names_keep_unique_row_identifiers(tmp_path: Path) -> None:
    members = {
        "first/data.csv": csv(HEADER, row(energy="10")),
        "second/data.csv": csv(HEADER, row(energy="20")),
        "first/DATA.CSV": csv(HEADER, row(energy="30")),
    }
    downloaded = archive(tmp_path, members)

    report = ingest(downloaded, tmp_path)

    clean = pl.read_parquet(output_paths(tmp_path, 2025, "amprion").clean)
    assert clean["quelldatei"].to_list() == list(members)
    assert clean.select("jahr", "uenb", "quelldatei", "zeile").n_unique() == 3
    assert report["rows"] == {"raw": 3, "clean": 3, "quarantine": 0}
    assert report["sums_clean"]["strommenge"] == 60
