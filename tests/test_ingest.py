from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from pathlib import Path

import httpx
import polars as pl
import pytest

import eeg_bewegungsdaten
from eeg_bewegungsdaten import cli
from eeg_bewegungsdaten.download import ArchivedFile, download, meta_path
from eeg_bewegungsdaten.ingest import ingest, latest_uploads, scan
from eeg_bewegungsdaten.links import PAGE_URL, find_links, older_links
from eeg_bewegungsdaten.paths import output_paths
from eeg_bewegungsdaten.quality import check
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA, QUARANTINE_SCHEMA, TEXT_SCHEMA, FormatError
from tests.builders import (
    HEADER,
    HEADER_TRANSNETBW_1,
    TENNET_PART,
    archive,
    archive_body,
    classified,
    csv,
    row,
    zip_bytes,
)
from tests.old_formats import (
    HEADER_50HERTZ_2014,
    HEADER_2017,
    HEADER_2020,
    cp1252_csv,
    row_2020,
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def amprion_members() -> dict[str, bytes]:
    return {
        "Bewegungsdaten_mpm.csv": csv(
            HEADER, row(form="2", month="1"), row("Grund_MASTRNR99", form="2", month="2")
        ),
        "Bewegungsdaten_Sanktion.csv": csv(HEADER, row(form="5"), row(form="9")),
    }


def test_ingest_writes_clean_rows_quarantine_and_report(data_dir: Path) -> None:
    archived = archive(data_dir, amprion_members())

    report = ingest(archived, data_dir)

    outputs = output_paths(data_dir, 2025, "amprion")
    clean = pl.read_parquet(outputs.clean)
    quarantine = pl.read_parquet(outputs.quarantine)
    assert clean.schema == CLEAN_SCHEMA
    assert quarantine.schema == QUARANTINE_SCHEMA
    assert clean.height == 3
    assert quarantine.select("quelldatei", "zeile", "fehlercode").row(0) == (
        "Bewegungsdaten_Sanktion.csv",
        3,
        "BAD_VERAEUSSERUNGSFORM",
    )
    assert report["rows"] == {"raw": 4, "clean": 3, "quarantine": 1}
    assert report["quarantine"] == {"BAD_VERAEUSSERUNGSFORM": 1}
    assert report["qa_flags"] == {"MASTR_NR_INVALID": 1}
    assert report["sums_clean"]["eeg_zahlung"] == pytest.approx(3 * 1128.2)
    assert [s["sha256"] for s in report["sources"]] == [archived.meta["sha256"]]
    assert [m["rows"] for m in report["members"]] == [2, 2]
    assert report["outputs"]["text"] == str(outputs.text)
    assert json.loads(outputs.report.read_text(encoding="utf-8")) == report


def test_text_layer_keeps_every_line_as_published(data_dir: Path) -> None:
    members = {"a.csv": csv(HEADER, row(" EEG900000000001 ", month=""), row() + ";", row(form="9"))}

    report = ingest(archive(data_dir, members), data_dir)

    text = pl.read_parquet(output_paths(data_dir, 2025, "amprion").text)
    assert text.schema == TEXT_SCHEMA
    assert text.height == report["rows"]["raw"] == 3
    assert text.select("eeg_mastr_nr", "monat", "veraeusserungsform").rows() == [
        (" EEG900000000001 ", "", "1"),
        ("EEG900000000001", "0", "1"),
        ("EEG900000000001", "0", "9"),
    ]
    # The whole line is only kept where the fields cannot hold it.
    assert text.select("felder", "felder_kopf", "rohzeile").rows() == [
        (10, 10, None),
        (11, 10, row() + ";"),
        (10, 10, None),
    ]


@pytest.mark.parametrize(
    ("year", "members"),
    [
        pytest.param(2025, amprion_members(), id="2025"),
        pytest.param(
            2017,
            {
                "a.csv": cp1252_csv(
                    HEADER_2017, "E1;10009001;Netz GmbH;SoK81a------01;2718;1383,46;", "E2;;;x;;;"
                )
            },
            id="2017",
        ),
        pytest.param(
            2020,
            {
                "a.csv": cp1252_csv(
                    HEADER_2020,
                    row_2020(),
                    row_2020(mastr_nr="", form="Marktprämie"),
                    row_2020(form=""),
                )
            },
            id="2020",
        ),
    ],
)
def test_clean_rows_can_be_rebuilt_from_the_text_layer(
    data_dir: Path, year: int, members: dict[str, bytes]
) -> None:
    ingest(archive(data_dir, members, year=year), data_dir)
    outputs = output_paths(data_dir, year, "amprion")

    clean, quarantine = check(pl.read_parquet(outputs.text))

    assert clean.equals(pl.read_parquet(outputs.clean))
    assert quarantine.drop("rohzeile").equals(pl.read_parquet(outputs.quarantine).drop("rohzeile"))


def test_bare_csv_file_before_2016_is_read_like_a_zip(data_dir: Path) -> None:
    body = csv(
        HEADER_50HERTZ_2014,
        "Netz GmbH;E40000000000001;SoK81a------01;8248;4175,14;0",
        "Netz GmbH;E40000000000001;So-vNNe--SpE07;0;0;12,5",
        bom=True,
    )
    catalog = next(link for link in older_links() if link.filename == "EEG_JEA_2014.csv")
    link = dataclasses.replace(catalog, sha256=hashlib.sha256(body).hexdigest())
    archived = archive_body(data_dir, link, body)

    report = ingest(archived, data_dir)

    assert (report["jahr"], report["uenb"]) == (2014, "50hertz")
    assert report["rows"] == {"raw": 2, "clean": 2, "quarantine": 0}
    assert [m["name"] for m in report["members"]] == ["EEG_JEA_2014.csv"]
    assert report["skipped_members"] == []
    assert report["sums_clean"]["vermiedene_netzentgelte"] == 12.5
    clean = pl.read_parquet(report["outputs"]["clean"])
    assert clean["netzbetreiber"].to_list() == ["Netz GmbH"] * 2
    assert clean["nb_bnr"].to_list() == [None, None]
    assert clean["quelldatei"].to_list() == ["EEG_JEA_2014.csv"] * 2


def test_ingest_leaves_no_temporary_files(data_dir: Path) -> None:
    ingest(archive(data_dir, amprion_members()), data_dir)

    hidden = [p for p in data_dir.rglob("*") if p.name.startswith(".") or p.suffix == ".tmp"]
    assert hidden == []


def test_ingest_twice_gives_the_same_rows(data_dir: Path) -> None:
    archived = archive(data_dir, amprion_members())
    ingest(archived, data_dir)
    first = pl.read_parquet(output_paths(data_dir, 2025, "amprion").clean)
    ingest(archived, data_dir)

    assert pl.read_parquet(output_paths(data_dir, 2025, "amprion").clean).equals(first)


def test_rows_from_many_chunks_keep_their_order(data_dir: Path) -> None:
    lines = [row(f"EEG9000000005{i:02d}") for i in range(40)]
    archived = archive(data_dir, {"a.csv": csv(HEADER, *lines)})

    report = ingest(archived, data_dir, block_size=200)

    clean = pl.read_parquet(output_paths(data_dir, 2025, "amprion").clean)
    assert clean["zeile"].to_list() == list(range(2, 42))
    assert report["rows"]["clean"] == 40


def test_zip_without_data_files_is_an_error(data_dir: Path) -> None:
    archived = archive(data_dir, {"liesmich.pdf": b"leer"})

    with pytest.raises(FormatError, match="no data files"):
        ingest(archived, data_dir)
    assert not output_paths(data_dir, 2025, "amprion").clean.exists()


def test_latest_uploads_takes_the_newest_upload(data_dir: Path) -> None:
    archive(data_dir, amprion_members())
    newer = archive(
        data_dir,
        {"neu.csv": csv(HEADER, row())},
        file_id="aaaaaaaa-1111-2222-3333-444444444444",
    )
    other = archive(data_dir, amprion_members(), uenb="tennet")

    assert [[a.path for a in upload] for upload in latest_uploads(data_dir)] == [
        [newer.path],
        [other.path],
    ]


def archive_part(
    data_dir: Path,
    part: int,
    members: dict[str, bytes],
    folder: str = "neu/2024",
    published: dt.date | None = None,
    expected_parts: tuple[int, ...] = (1, 2),
) -> ArchivedFile:
    """One part of TenneT 2024, which comes as several ZIPs without a CDN id."""
    body = zip_bytes(members)
    link = dataclasses.replace(
        classified(TENNET_PART.format(folder=folder, part=part), published),
        expected_parts=expected_parts,
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    with httpx.Client(transport=transport) as client:
        return download(link, data_dir, client)


OLD, NEW = dt.date(2025, 9, 16), dt.date(2025, 12, 8)


def downloaded_later(archived: ArchivedFile) -> None:
    """Move the download time of `archived` one hour on, so that it is the last download."""
    meta_file = meta_path(archived.path)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    later = dt.datetime.fromisoformat(meta["downloaded_at"]) + dt.timedelta(hours=1)
    meta_file.write_text(json.dumps({**meta, "downloaded_at": later.isoformat()}), encoding="utf-8")


def tennet_part(part: int, *lines: str) -> dict[str, bytes]:
    return {f"Bewegungsdaten_Teil {part}.csv": csv(HEADER, *lines)}


def test_parts_of_one_upload_are_read_together(data_dir: Path) -> None:
    second = archive_part(data_dir, 2, tennet_part(2, row(uenb="tennet")))
    first = archive_part(data_dir, 1, tennet_part(1, row(uenb="tennet"), row("", uenb="tennet")))

    [upload] = latest_uploads(data_dir)
    report = ingest(upload, data_dir)

    assert [f.path for f in upload] == [first.path, second.path]
    assert report["rows"] == {"raw": 3, "clean": 3, "quarantine": 0}
    assert [(s["part"], s["sha256"]) for s in report["sources"]] == [
        (1, first.meta["sha256"]),
        (2, second.meta["sha256"]),
    ]
    assert [(m["name"], m["archive"], m["rows"]) for m in report["members"]] == [
        ("Bewegungsdaten_Teil 1.csv", first.path.name, 2),
        ("Bewegungsdaten_Teil 2.csv", second.path.name, 1),
    ]
    clean = pl.read_parquet(output_paths(data_dir, 2024, "tennet").clean)
    assert clean.select("quelldatei", "zeile").rows() == [
        ("Bewegungsdaten_Teil 1.csv", 2),
        ("Bewegungsdaten_Teil 1.csv", 3),
        ("Bewegungsdaten_Teil 2.csv", 2),
    ]


CDN_PART = (
    "https://www.netztransparenz.de/cdn/files/{file_id}/"
    "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten%202026_teil%20{part}.zip"
)


def test_parts_on_the_cdn_are_read_together(data_dir: Path) -> None:
    """On the CDN, each part of an upload has its own id."""
    bodies = {
        CDN_PART.format(file_id="-".join(str(part) * n for n in (8, 4, 4, 4, 12)), part=part): (
            zip_bytes(tennet_part(part, row(uenb="tennet")))
        )
        for part in (1, 2)
    }
    page = "".join(f'<li><a href="{url}">Teil</a></li>' for url in bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAGE_URL:
            return httpx.Response(200, text=page)
        return httpx.Response(200, content=bodies[str(request.url)])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        files = [download(link, data_dir, client) for link in find_links(client, older=False)]
    [upload] = latest_uploads(data_dir)
    report = ingest(upload, data_dir)

    assert [f.path for f in upload] == [f.path for f in files]
    assert report["rows"] == {"raw": 2, "clean": 2, "quarantine": 0}


def test_a_newer_upload_replaces_all_parts_of_the_old_one(data_dir: Path) -> None:
    """Also when it has fewer parts: only the newest upload counts, not each part number."""
    for part in (1, 2, 3):
        archive_part(data_dir, part, tennet_part(part, row(uenb="tennet")), folder="alt/2024")
    new = [archive_part(data_dir, part, tennet_part(part, row(uenb="tennet"))) for part in (1, 2)]

    assert [[f.path for f in upload] for upload in latest_uploads(data_dir)] == [
        [f.path for f in new]
    ]


def test_an_older_upload_archived_later_does_not_replace_a_newer_one(data_dir: Path) -> None:
    """The superseded TenneT 2024 parts were archived after the current ones."""
    new = [
        archive_part(data_dir, part, tennet_part(part, row(uenb="tennet")), published=NEW)
        for part in (1, 2)
    ]
    for part in (1, 2, 3):
        old = archive_part(
            data_dir, part, tennet_part(part, row(uenb="tennet")), folder="alt/2024", published=OLD
        )
        downloaded_later(old)

    assert [[f.path for f in upload] for upload in latest_uploads(data_dir)] == [
        [f.path for f in new]
    ]


def test_an_upload_without_date_counts_as_published_when_downloaded(data_dir: Path) -> None:
    undated = archive_part(data_dir, 1, tennet_part(1, row(uenb="tennet")))
    old = archive_part(
        data_dir, 1, tennet_part(1, row(uenb="tennet")), folder="alt/2024", published=OLD
    )
    downloaded_later(old)

    assert [[f.path for f in upload] for upload in latest_uploads(data_dir)] == [[undated.path]]


NEWER_ID = "aaaaaaaa-1111-2222-3333-444444444444"


def outputs_of(data_dir: Path, year: int, uenb: str) -> dict[Path, bytes]:
    outputs = output_paths(data_dir, year, uenb)
    return {
        p: p.read_bytes() for p in (outputs.text, outputs.clean, outputs.quarantine, outputs.report)
    }


@pytest.mark.parametrize("damage", ["missing", "truncated"])
def test_a_damaged_newest_upload_is_not_replaced_by_an_older_one(
    data_dir: Path, damage: str
) -> None:
    archive(data_dir, {"data.csv": csv(HEADER, row(energy="10"))})
    new_members = {"data.csv": csv(HEADER, row(energy="20"))}
    new = archive(data_dir, new_members, file_id=NEWER_ID)
    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 0
    before = outputs_of(data_dir, 2025, "amprion")
    clean_path = output_paths(data_dir, 2025, "amprion").clean
    energy = pl.read_parquet(clean_path)["strommenge"].to_list()
    if damage == "missing":
        new.path.unlink()
    else:
        new.path.write_bytes(new.path.read_bytes()[:100])

    assert [[f.path for f in upload] for upload in latest_uploads(data_dir)] == [[new.path]]
    with pytest.raises(FormatError, match="missing or truncated"):
        ingest(latest_uploads(data_dir)[0], data_dir)
    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 1
    assert outputs_of(data_dir, 2025, "amprion") == before

    # Downloading the file again repairs the archive.
    archive(data_dir, new_members, file_id=NEWER_ID)
    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 0
    assert pl.read_parquet(clean_path)["strommenge"].to_list() == energy


def test_a_newer_multipart_upload_without_any_part_is_not_replaced(data_dir: Path) -> None:
    for part in (1, 2, 3):
        old_part = tennet_part(part, row(uenb="tennet", energy="10"))
        archive_part(data_dir, part, old_part, folder="alt/2024", published=OLD)
    new = [
        archive_part(data_dir, part, tennet_part(part, row(uenb="tennet")), published=NEW)
        for part in (1, 2)
    ]
    ingest(latest_uploads(data_dir)[0], data_dir)
    before = outputs_of(data_dir, 2024, "tennet")
    for f in new:
        f.path.unlink()

    assert [[f.path for f in upload] for upload in latest_uploads(data_dir)] == [
        [f.path for f in new]
    ]
    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 1
    assert outputs_of(data_dir, 2024, "tennet") == before


def test_same_file_name_in_two_parts_is_an_error(data_dir: Path) -> None:
    for part in (1, 2):
        archive_part(data_dir, part, {"Bewegungsdaten.csv": csv(HEADER, row(uenb="tennet"))})

    with pytest.raises(FormatError, match=r"Bewegungsdaten\.csv is in"):
        ingest(latest_uploads(data_dir)[0], data_dir)
    assert not output_paths(data_dir, 2024, "tennet").clean.exists()


def test_ingest_refuses_files_of_different_uploads(data_dir: Path) -> None:
    files = [
        archive(data_dir, amprion_members()),
        archive(data_dir, amprion_members(), year=2024, file_id="aaaaaaaa-1111-2222-3333-4444"),
    ]

    with pytest.raises(FormatError, match="Files from different uploads"):
        ingest(files, data_dir)


def test_ingest_needs_at_least_one_file(data_dir: Path) -> None:
    with pytest.raises(ValueError, match="Nothing to ingest"):
        ingest([], data_dir)


def test_report_counts_empty_amounts_and_leaves_out_missing_columns(data_dir: Path) -> None:
    lines = [
        "E1;10009001;Netz GmbH;SoK81a------01;2718;1383,46;",
        "E2;10009001;Netz GmbH;So-vNNe--SpE07;;;28,14",
    ]
    archived = archive(data_dir, {"a.csv": cp1252_csv(HEADER_2017, *lines)}, year=2017)

    report = ingest(archived, data_dir)

    assert report["members"][0]["empty_values"] == {
        "strommenge": 1,
        "eeg_zahlung": 1,
        "vermiedene_netzentgelte": 1,
    }
    assert report["sums_clean"] == {
        "strommenge": 2718.0,
        "eeg_zahlung": 1383.46,
        "eeg_einnahmen": None,
        "vermiedene_netzentgelte": 28.14,
    }
    assert report["qa_flags"] == {}


def test_cli_ingest_reads_all_parts_once(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for part in (1, 2):
        archive_part(data_dir, part, tennet_part(part, row(uenb="tennet")))

    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[:3] for line in lines[1:]] == [
        ["2024", "tennet", "2"],
        ["total", "2", "2"],
    ]


def test_scan_combines_years_and_tsos(data_dir: Path) -> None:
    ingest(archive(data_dir, amprion_members()), data_dir)
    transnetbw = {"Teil1.csv": csv(HEADER_TRANSNETBW_1, row(uenb="transnetbw", month=""))}
    ingest(archive(data_dir, transnetbw, uenb="transnetbw", year=2024), data_dir)

    combined = scan(data_dir).group_by("jahr", "uenb").len().sort("jahr").collect()

    assert combined.rows() == [(2024, "transnetbw", 1), (2025, "amprion", 3)]
    assert scan(data_dir, layer="quarantine").collect().height == 1
    assert scan(data_dir, layer="text").collect().height == 5


def test_scan_defaults_to_the_data_folder(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ingest(archive(data_dir, amprion_members()), data_dir)
    monkeypatch.setenv("EEG_BEWEGUNGSDATEN_DIR", str(data_dir))

    assert eeg_bewegungsdaten.scan().collect().height == 3


def test_cli_ingest_prints_counts(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive(data_dir, amprion_members())

    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 0

    out = capsys.readouterr().out
    assert "BAD_VERAEUSSERUNGSFORM 1" in out
    assert out.splitlines()[-1].split() == ["total", "4", "3", "1"]


def test_cli_ingest_without_downloads_fails(data_dir: Path) -> None:
    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 1


def test_cli_ingest_reports_format_errors_and_goes_on(data_dir: Path) -> None:
    archive(data_dir, {"a.csv": csv("Anlage;Wert", "x;1")})
    archive(data_dir, amprion_members(), uenb="tennet")

    assert cli.main(["--data-dir", str(data_dir), "ingest"]) == 1
    assert output_paths(data_dir, 2025, "tennet").clean.exists()


@pytest.mark.parametrize("missing", [2, 4])
def test_incomplete_new_upload_does_not_replace_existing_outputs(
    data_dir: Path, missing: int, caplog: pytest.LogCaptureFixture
) -> None:
    old = [
        archive_part(
            data_dir,
            part,
            tennet_part(part, row(uenb="tennet")),
            folder="alt/2024",
            published=OLD,
        )
        for part in (1, 2)
    ]
    ingest(old, data_dir)
    outputs = output_paths(data_dir, 2024, "tennet")
    written = (outputs.text, outputs.clean, outputs.quarantine, outputs.report)
    before = {path: path.read_bytes() for path in written}
    for part in (1, 2, 3, 4):
        if part != missing:
            archive_part(
                data_dir,
                part,
                tennet_part(part, row(uenb="tennet")),
                published=NEW,
                expected_parts=(1, 2, 3, 4),
            )

    assert cli.main(["--data-dir", str(data_dir), "ingest", "--year", "2024"]) == 1
    assert "expected [1, 2, 3, 4]" in caplog.text
    assert {path: path.read_bytes() for path in written} == before


def test_multipart_upload_without_expected_parts_is_refused(data_dir: Path) -> None:
    part = archive_part(data_dir, 1, tennet_part(1, row(uenb="tennet")), expected_parts=())

    with pytest.raises(FormatError, match="Unknown multipart upload"):
        ingest(part, data_dir)


def test_ingest_refuses_duplicate_parts(data_dir: Path) -> None:
    part = archive_part(data_dir, 1, tennet_part(1, row(uenb="tennet")), expected_parts=(1,))

    with pytest.raises(FormatError, match="duplicate upload parts"):
        ingest([part, part], data_dir)
