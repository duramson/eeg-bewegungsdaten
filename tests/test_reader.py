from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eeg_bewegungsdaten.reader import iter_chunks, skipped_members, to_text
from eeg_bewegungsdaten.schema import TEXT_SCHEMA, FormatError
from tests.builders import HEADER, HEADER_AMPRION_EXTRA, HEADER_TENNET, csv, make_zip, row
from tests.old_formats import (
    HEADER_50HERTZ_2015,
    HEADER_2020,
    HEADER_AMPRION_2017,
    HEADER_TENNET_2023,
    cp1252_csv,
    row_2020,
)


def lines_of(path: Path, block_size: int = 1 << 20) -> list[tuple[str, int, str, bool, bool]]:
    return [
        (chunk.member, *line)
        for chunk in iter_chunks(path, block_size)
        for line in chunk.frame.rows()
    ]


def test_reads_every_csv_in_archive_order_and_skips_other_files(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path / "t.zip",
        {
            "Bewegungsdaten 2025/": b"",
            "Bewegungsdaten 2025/Teil1.csv": csv(HEADER, row("EEG900000000001")),
            "Bewegungsdaten 2025/Teil2.csv": csv(HEADER, row("EEG900000000002")),
            "Liesmich.txt": b"Hinweise",
        },
    )

    lines = lines_of(path)

    assert [(member, number) for member, number, *_ in lines] == [
        ("Bewegungsdaten 2025/Teil1.csv", 2),
        ("Bewegungsdaten 2025/Teil2.csv", 2),
    ]
    assert skipped_members(path) == ["Liesmich.txt"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.zip"]


def test_txt_file_is_read_when_the_zip_has_no_csv(tmp_path: Path) -> None:
    """TenneT 2023: a single TXT file."""
    line = ";".join(f'"{v}"' for v in row_2020(month="0").split(";"))
    path = make_zip(
        tmp_path / "t.zip",
        {"Bewegungsdaten 2023.txt": cp1252_csv(HEADER_TENNET_2023, line)},
    )

    [chunk] = list(iter_chunks(path))

    assert chunk.member == "Bewegungsdaten 2023.txt"
    assert chunk.header[0] == '"EEG_Anlagenschlüssel"'
    assert chunk.columns[0] == "anlagenschluessel"
    assert chunk.frame["rohzeile"].to_list() == [line]
    assert skipped_members(path) == []


def test_bare_csv_file_is_read_as_one_member(tmp_path: Path) -> None:
    """50Hertz 2014 and 2015: a CSV file instead of a ZIP."""
    line = "1;10000086;Städtische Werke Musterstadt;E40000000000001;WiK71a------01;983840;89529,44;"
    path = tmp_path / "50Hertz_Bewegungsdaten_2015[1].csv"
    path.write_bytes(cp1252_csv(HEADER_50HERTZ_2015, line))

    [chunk] = list(iter_chunks(path))

    assert chunk.member == "50Hertz_Bewegungsdaten_2015[1].csv"
    assert chunk.header[-1] == "VNNE [€]"
    assert chunk.columns[1:3] == ["nb_bnr", "netzbetreiber"]
    assert chunk.frame.select("zeile", "rohzeile", "cp1252").rows() == [(2, line, True)]
    assert skipped_members(path) == []


def test_byte_order_mark_and_crlf_are_removed(tmp_path: Path) -> None:
    """50Hertz: UTF-8 with BOM and CRLF."""
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER, row(), bom=True)})

    chunk = next(iter_chunks(path))

    assert chunk.header[0] == "EEG_Mastr_Nr"
    assert chunk.columns[0] == "eeg_mastr_nr"
    assert chunk.frame["rohzeile"].to_list() == [row()]


def test_lf_line_endings_work(tmp_path: Path) -> None:
    """TenneT: LF only."""
    line = "EEG900000000001;;10009008;SNB900000000001;Netz GmbH;SNB970033313272;1;x;1,000;0,00;0;0"
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER_TENNET, line, newline="\n")})

    assert [text for *_, text, _, _ in lines_of(path)] == [line]


def test_cp1252_lines_inside_utf8_files_are_decoded_and_marked(tmp_path: Path) -> None:
    """Amprion and TransnetBW: single lines in cp1252, the rest UTF-8."""
    utf8 = row(key="E3000000Lönsweg-Süd-00000000001").encode("utf-8")
    cp1252 = row(key="E3000000Lönsweg-Süd-00000000002").encode("cp1252")
    content = HEADER.encode() + b"\r\n" + utf8 + b"\r\n" + cp1252 + b"\r\n"
    path = make_zip(tmp_path / "t.zip", {"a.csv": content})

    lines = lines_of(path)

    assert [(text.split(";")[1], cp) for _, _, text, cp, _ in lines] == [
        ("E3000000Lönsweg-Süd-00000000001", False),
        ("E3000000Lönsweg-Süd-00000000002", True),
    ]


def test_files_in_cp1252_throughout_are_decoded(tmp_path: Path) -> None:
    """Most files up to 2023: header and lines in cp1252; ASCII lines need no fallback."""
    lines = [row_2020(name="Netz GmbH", form="Marktpraemie"), row_2020()]
    path = make_zip(tmp_path / "t.zip", {"a.csv": cp1252_csv(HEADER_2020, *lines)})

    [chunk] = list(iter_chunks(path))

    assert chunk.header[0] == "EEG_Anlagenschlüssel"
    assert chunk.header[-1] == "Veräußerungsform"
    assert chunk.frame["rohzeile"].to_list() == lines
    assert chunk.frame["cp1252"].to_list() == [False, True]


def test_bytes_undefined_in_cp1252_are_marked_undecodable(tmp_path: Path) -> None:
    content = HEADER.encode() + b"\r\n" + row(key="E3\x81\x8d").encode("latin-1") + b"\r\n"
    path = make_zip(tmp_path / "t.zip", {"a.csv": content})

    [(_, _, text, cp1252, undecodable)] = lines_of(path)

    assert undecodable
    assert not cp1252
    assert "�" in text


def test_line_numbers_survive_block_boundaries(tmp_path: Path) -> None:
    rows = [row(f"EEG9000000005{i:02d}") for i in range(30)]
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER, *rows)})

    lines = lines_of(path, block_size=150)

    assert [number for _, number, *_ in lines] == list(range(2, 32))
    assert [text for _, _, text, *_ in lines] == rows


def test_blank_lines_are_counted_not_returned(tmp_path: Path) -> None:
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER, row(), "", "  ", row())})

    chunks = list(iter_chunks(path))

    assert sum(c.blank_lines for c in chunks) == 2
    assert [n for c in chunks for n in c.frame["zeile"]] == [2, 5]


def test_lines_with_only_separators_are_blank(tmp_path: Path) -> None:
    """TransnetBW 2021: 168,294 lines ';;;;;;;;;;;' fill the sheet up to Excel's row limit."""
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER, row(), ";" * 9, '"";" ";""')})

    chunks = list(iter_chunks(path))

    assert sum(c.blank_lines for c in chunks) == 2
    assert [n for c in chunks for n in c.frame["zeile"]] == [2]


def test_last_line_without_line_break_is_read(tmp_path: Path) -> None:
    content = (HEADER + "\r\n" + row()).encode()
    path = make_zip(tmp_path / "t.zip", {"a.csv": content})

    assert [text for *_, text, _, _ in lines_of(path)] == [row()]


def test_empty_csv_still_yields_a_chunk(tmp_path: Path) -> None:
    path = make_zip(tmp_path / "t.zip", {"leer.csv": b"", "nur_kopf.csv": csv(HEADER)})

    chunks = list(iter_chunks(path))

    assert [(c.member, c.frame.height) for c in chunks] == [("leer.csv", 0), ("nur_kopf.csv", 0)]


def test_unknown_header_stops_the_file(tmp_path: Path) -> None:
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv("Anlage;Wert", "a;1")})

    with pytest.raises(FormatError, match=r"a\.csv"):
        list(iter_chunks(path))


def text_of(path: Path, uenb: str = "amprion") -> pl.DataFrame:
    return pl.concat(to_text(chunk, jahr=2025, uenb=uenb) for chunk in iter_chunks(path))


def test_text_layer_keeps_fields_as_published(tmp_path: Path) -> None:
    line = row(" EEG900000000001\xa0", key="", energy="1.234,5", month="")
    path = make_zip(tmp_path / "t.zip", {"Teil 1.csv": csv(HEADER, line)})

    text = text_of(path)

    assert text.schema == TEXT_SCHEMA
    record = text.row(0, named=True)
    assert record["eeg_mastr_nr"] == " EEG900000000001\xa0"
    assert record["anlagenschluessel"] == ""
    assert record["strommenge"] == "1.234,5"
    assert record["monat"] == ""
    assert (record["nb_bnr"], record["netzbetreiber"]) == (None, None)
    assert (record["jahr"], record["uenb"], record["quelldatei"], record["zeile"]) == (
        2025,
        "amprion",
        "Teil 1.csv",
        2,
    )
    assert (record["kodierung"], record["felder"], record["felder_kopf"]) == ("utf-8", 10, 10)
    assert record["zusatzfelder"] is None
    assert record["rohzeile"] == line


def test_text_layer_maps_tennet_columns(tmp_path: Path) -> None:
    line = "EEG900000000001;;10009008;SNB900000000001;Netz GmbH;SNB970033313272;1;x;1,000;0,00;0;0"
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER_TENNET, line, newline="\n")})

    record = text_of(path, "tennet").row(0, named=True)

    assert (record["nb_bnr"], record["nb_mastr_nr"], record["netzbetreiber"]) == (
        "10009008",
        "SNB900000000001",
        "Netz GmbH",
    )
    assert record["veraeusserungsform"] == "1"


def test_text_layer_puts_fields_without_header_name_into_zusatzfelder(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path / "t.zip", {"a.csv": csv(HEADER_AMPRION_EXTRA, row() + ";;", row() + ";;0")}
    )

    text = text_of(path)

    assert text["zusatzfelder"].to_list() == [["", ""], ["", "0"]]
    assert text["felder_kopf"].to_list() == [12, 12]


def test_text_layer_marks_the_encoding(tmp_path: Path) -> None:
    content = (
        HEADER.encode()
        + b"\r\n"
        + row(key="Lönsweg").encode("utf-8")
        + b"\r\n"
        + row(key="Lönsweg").encode("cp1252")
        + b"\r\n"
        + row(key="E3\x81").encode("latin-1")
        + b"\r\n"
    )
    path = make_zip(tmp_path / "t.zip", {"a.csv": content})

    text = text_of(path)

    assert text["kodierung"].to_list() == ["utf-8", "cp1252", "undecodable"]
    assert text["anlagenschluessel"].to_list()[:2] == ["Lönsweg", "Lönsweg"]


def test_text_layer_of_short_and_long_lines(tmp_path: Path) -> None:
    short = ";".join(row().split(";")[:-2])
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER, short, row() + ";x")})

    text = text_of(path)

    assert text.select("felder", "eeg_einnahmen", "monat").rows() == [
        (8, None, None),
        (11, "0", "0"),
    ]
    assert text["rohzeile"].to_list() == [short, row() + ";x"]


def test_text_layer_of_an_old_layout(tmp_path: Path) -> None:
    line = "7;E90000000000000000000000000000012;10009001;Netz GmbH;SoK81a------01;;0,00;9,85"
    path = make_zip(tmp_path / "t.zip", {"a.csv": cp1252_csv(HEADER_AMPRION_2017, line)})

    record = text_of(path).row(0, named=True)

    # Columns the file does not have are null, empty fields ''; the row number ZEILEN_NR is
    # not kept and only in `rohzeile`.
    assert (record["eeg_mastr_nr"], record["monat"], record["eeg_einnahmen"]) == (None,) * 3
    assert record["anlagenschluessel"] == "E90000000000000000000000000000012"
    assert (record["nb_bnr"], record["netzbetreiber"]) == ("10009001", "Netz GmbH")
    assert (record["strommenge"], record["vermiedene_netzentgelte"]) == ("", "9,85")
    assert (record["felder"], record["felder_kopf"], record["zusatzfelder"]) == (8, 8, None)
    assert record["rohzeile"] == line


def test_text_layer_of_empty_file_is_empty(tmp_path: Path) -> None:
    path = make_zip(tmp_path / "t.zip", {"nur_kopf.csv": csv(HEADER)})

    text = text_of(path)

    assert text.is_empty()
    assert text.schema == TEXT_SCHEMA
