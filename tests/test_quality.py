"""The checks of `quality` on lines in every layout of 2016 to 2025, one test per known error
class, built after the patterns found in the real files."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eeg_bewegungsdaten.quality import QA_FLAGS, QUARANTINE_CODES
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA, QUARANTINE_SCHEMA
from tests.builders import (
    HEADER,
    HEADER_AMPRION_EXTRA,
    HEADER_TENNET,
    HEADER_TRANSNETBW_1,
    HEADER_TRANSNETBW_2,
    UENB_NR,
    csv,
    make_zip,
    read_lines,
    read_zip,
    row,
)
from tests.old_formats import (
    HEADER_50HERTZ_2016,
    HEADER_2017,
    HEADER_2020,
    HEADER_2021,
    HEADER_AMPRION_2016_VNE,
    HEADER_AMPRION_2017,
    HEADER_AMPRION_2018,
    HEADER_AMPRION_2018_VNE,
    HEADER_AMPRION_2022,
    HEADER_TENNET_2023,
    HEADER_TENNET_2024_FIRST,
    HEADER_TRANSNETBW_2016,
    HEADER_TRANSNETBW_2020,
    cp1252_csv,
    row_2020,
)


def flags_of(clean: pl.DataFrame) -> list[list[str]]:
    return [list(flags) for flags in clean["hinweise"].to_list()]


# --- rows that are kept -------------------------------------------------------------------


def test_valid_row_has_typed_values_and_no_flags(tmp_path: Path) -> None:
    clean, quarantine = read_lines(tmp_path, row(energy="28417,553", payment="-0,14", month="12"))

    assert quarantine.is_empty()
    assert clean.schema == CLEAN_SCHEMA
    record = clean.row(0, named=True)
    assert record["eeg_mastr_nr"] == "EEG900000000001"
    assert record["strommenge"] == pytest.approx(28417.553)
    assert record["eeg_zahlung"] == pytest.approx(-0.14)
    assert record["monat"] == 12
    assert record["veraeusserungsform"] == 1
    assert record["uenb"] == "amprion"
    assert record["jahr"] == 2025
    assert record["quelldatei"] == "Bewegungsdaten.csv"
    assert record["zeile"] == 2
    assert record["hinweise"] == []


def test_missing_mastr_nr_is_flagged(tmp_path: Path) -> None:
    clean, _ = read_lines(tmp_path, row(""))

    assert flags_of(clean) == [["MASTR_NR_MISSING"]]
    assert clean["eeg_mastr_nr"].to_list() == [None]


def test_missing_mastr_nr_and_plant_key_is_flagged(tmp_path: Path) -> None:
    clean, _ = read_lines(tmp_path, row("", key=""))

    assert flags_of(clean) == [["MASTR_NR_MISSING", "NO_PLANT_ID"]]


def test_missing_plant_key_alone_is_normal(tmp_path: Path) -> None:
    """50Hertz: 307,234 rows without key, but with MaStR number."""
    clean, _ = read_lines(tmp_path, row(key=""))

    assert flags_of(clean) == [[]]


def test_unit_number_instead_of_eeg_number_is_flagged(tmp_path: Path) -> None:
    """Amprion: SEE... in EEG_Mastr_Nr."""
    clean, _ = read_lines(tmp_path, row("SEE900000000002"))

    assert flags_of(clean) == [["MASTR_NR_IS_UNIT"]]


def test_trailing_no_break_space_is_trimmed(tmp_path: Path) -> None:
    """Amprion: 'SEE…\\xa0'."""
    clean, _ = read_lines(tmp_path, row("SEE900000000002\xa0", key=" E1 "))

    assert clean["eeg_mastr_nr"].to_list() == ["SEE900000000002"]
    assert clean["anlagenschluessel"].to_list() == ["E1"]


@pytest.mark.parametrize(
    "value",
    [
        "EEG900000000008,EEG900000000004",  # TenneT
        "EEG900000000008,EEG900000000004,EEG900000000005,EEG900000000006",
        "EEG900000000008 / EEG900000000004",  # Amprion
    ],
)
def test_several_mastr_numbers_are_flagged(tmp_path: Path, value: str) -> None:
    clean, _ = read_lines(tmp_path, row(value))

    assert flags_of(clean) == [["MASTR_NR_MULTIPLE"]]
    assert clean["eeg_mastr_nr"].to_list() == [value]


@pytest.mark.parametrize(
    "value",
    [
        "Grund_MASTRNR99",
        "GRUND_MASTRNR99",
        "Grund_MASTRNR_02",
        "Grund_Mastrnr01",
        "MASTRNR03",
        "KWK900000000001",
        "EEGDUMMY0000005",
        "SEEDUMMYBW",
        "EEG90000000000Y",  # typo, last digit is a letter
        "EEG9000ü0000001",  # 50Hertz
        "EEGEEG900000001",  # 50Hertz
        "EEG 90000000001",
        "9,59E+11",  # Excel number format
        "0",
        "Noch ausstehend",
        "Nicht vorhanden. Betreiber hat nicht registriert",
        "angefordert (22.10.)",
        "EEG900000000008 / EEG900000000004 (Erweiterung)",
        "EEG9000000000012",  # 13 digits
    ],
)
def test_invalid_mastr_numbers_are_flagged_and_kept(tmp_path: Path, value: str) -> None:
    clean, quarantine = read_lines(tmp_path, row(value))

    assert quarantine.is_empty()
    assert flags_of(clean) == [["MASTR_NR_INVALID"]]
    assert clean["eeg_mastr_nr"].to_list() == [value]


@pytest.mark.parametrize("value", ["", "SNB9", "ABC900000000001"])
def test_invalid_grid_operator_number_is_flagged(tmp_path: Path, value: str) -> None:
    clean, _ = read_lines(tmp_path, row(nb=value))

    assert flags_of(clean) == [["NB_MASTR_NR_INVALID"]]


def test_foreign_tso_number_is_flagged(tmp_path: Path) -> None:
    clean, _ = read_lines(tmp_path, row(uenb="tennet"))

    assert flags_of(clean) == [["UENB_MASTR_NR_MISMATCH"]]


def test_empty_month_is_read_as_annual_value(tmp_path: Path) -> None:
    """TransnetBW: Monat empty instead of 0 in about a million rows."""
    path = make_zip(
        tmp_path / "t.zip",
        {"Teil1.csv": csv(HEADER_TRANSNETBW_1, row(uenb="transnetbw", month=""))},
    )
    clean, _ = read_zip(path, "transnetbw")

    assert clean["monat"].to_list() == [0]
    assert flags_of(clean) == [["MONAT_EMPTY"]]


def test_cp1252_line_is_flagged(tmp_path: Path) -> None:
    line = row(key="E9000000Musterstraße-9-00010").encode("cp1252")
    path = make_zip(tmp_path / "t.zip", {"a.csv": HEADER.encode() + b"\r\n" + line + b"\r\n"})

    clean, _ = read_zip(path)

    assert clean["anlagenschluessel"].to_list() == ["E9000000Musterstraße-9-00010"]
    assert flags_of(clean) == [["ENCODING_CP1252"]]


def test_empty_extra_columns_are_dropped(tmp_path: Path) -> None:
    """Amprion 'ausgefoerdert': two empty columns without name."""
    path = make_zip(
        tmp_path / "t.zip", {"ausgefoerdert.csv": csv(HEADER_AMPRION_EXTRA, row(form="5") + ";;")}
    )
    clean, quarantine = read_zip(path)

    assert quarantine.is_empty()
    assert clean.columns == CLEAN_SCHEMA.names()
    assert clean["veraeusserungsform"].to_list() == [5]


def test_transnetbw_part_2_header_is_understood(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path / "t.zip",
        {"Teil2.csv": csv(HEADER_TRANSNETBW_2, row(uenb="transnetbw", form="2"))},
    )
    clean, _ = read_zip(path, "transnetbw")

    assert clean["veraeusserungsform"].to_list() == [2]


def test_tennet_layout_fills_grid_operator_columns(tmp_path: Path) -> None:
    line = ";".join(
        [
            "EEG900000000001",
            "",
            "10009008",
            "SNB900000000001",
            "Netz Musterstadt GmbH",
            UENB_NR["tennet"],
            "2",
            "SgK24---MPM--0",
            "214,000",
            "0,00",
            "0",
            "7",
        ]
    )
    path = make_zip(tmp_path / "t.zip", {"Teil1.csv": csv(HEADER_TENNET, line, newline="\n")})
    clean, _ = read_zip(path, "tennet")

    record = clean.row(0, named=True)
    assert record["nb_bnr"] == "10009008"
    assert record["netzbetreiber"] == "Netz Musterstadt GmbH"
    assert record["strommenge"] == 214.0
    assert record["monat"] == 7
    assert record["hinweise"] == []


def test_other_layouts_have_empty_grid_operator_columns(tmp_path: Path) -> None:
    clean, _ = read_lines(tmp_path, row())

    assert clean.select("nb_bnr", "netzbetreiber").row(0) == (None, None)


def test_value_in_unnamed_column_is_flagged_and_left_out(tmp_path: Path) -> None:
    """TransnetBW 2024 part 2: '1' after the last, unnamed column in 3,622 rows."""
    path = make_zip(
        tmp_path / "t.zip",
        {"a.csv": csv(HEADER_AMPRION_EXTRA, row(energy="20", payment="5") + ";;1")},
    )
    clean, quarantine = read_zip(path)

    assert quarantine.is_empty()
    assert clean.columns == CLEAN_SCHEMA.names()
    assert clean.select("strommenge", "eeg_zahlung").row(0) == (20.0, 5.0)
    assert flags_of(clean) == [["EXTRA_VALUE"]]


@pytest.mark.parametrize(
    ("value", "month"),
    [
        ("13", 13),
        ("001", 1),
        ("200", None),
        ("-1", None),
        ("Jan", None),
        ("1,5", None),
        ("1-12", None),
    ],
)
def test_month_outside_0_to_12_is_flagged(tmp_path: Path, value: str, month: int | None) -> None:
    """Amprion 2023: Monat 13 in 994 rows; Amprion 2021: '1-12' in 3 rows."""
    clean, quarantine = read_lines(tmp_path, row(month=value, payment="7,5"))

    assert quarantine.is_empty()
    assert clean["monat"].to_list() == [month]
    assert clean["eeg_zahlung"].to_list() == [7.5]
    assert flags_of(clean) == [["MONAT_INVALID"]]


# --- lines that go to quarantine ----------------------------------------------------------


def quarantined(
    tmp_path: Path, line: str, header: str = HEADER, valid: str = row()
) -> dict[str, object]:
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(header, valid, line)})
    clean, quarantine = read_zip(path)
    assert clean.height == 1
    assert quarantine.height == 1
    assert quarantine.schema == QUARANTINE_SCHEMA
    record: dict[str, object] = quarantine.row(0, named=True)
    assert record["zeile"] == 3
    assert record["rohzeile"] == line
    return record


@pytest.mark.parametrize(
    "line",
    [
        row() + ";",
        ";".join(row().split(";")[:-1]),
        "EEG900000000001;Anlage;mit;Semikolon;" + row().split(";", 1)[1],
    ],
)
def test_wrong_field_count_goes_to_quarantine(tmp_path: Path, line: str) -> None:
    record = quarantined(tmp_path, line)

    assert record["fehlercode"] == "FIELD_COUNT"
    assert str(record["detail"]).endswith("expected 10")


@pytest.mark.parametrize("value", ["6", "0", "", "1,0", "Prämie"])
def test_unknown_veraeusserungsform_goes_to_quarantine(tmp_path: Path, value: str) -> None:
    record = quarantined(tmp_path, row(form=value))

    assert record["fehlercode"] == "BAD_VERAEUSSERUNGSFORM"
    assert record["detail"] == f"veraeusserungsform='{value}'"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("energy", "12.5"),  # decimal point
        ("energy", "1.23,5"),  # not a thousands separator
        ("energy", "0.123"),
        ("payment", "9,59E+11"),  # Excel cut off digits
        ("payment", "12,5 €"),
        ("income", "NaN"),
        ("income", "1,"),
        ("energy", "１２３,４５"),  # noqa: RUF001  # digits outside ASCII
        ("energy", "9" * 400),  # infinity as a float
    ],
)
def test_unreadable_number_goes_to_quarantine(tmp_path: Path, column: str, value: str) -> None:
    record = quarantined(tmp_path, row(**{column: value}))

    names = {"energy": "strommenge", "payment": "eeg_zahlung", "income": "eeg_einnahmen"}
    assert record["fehlercode"] == "BAD_NUMBER"
    assert record["detail"] == f"{names[column]}='{value}'"


def test_unreadable_amount_sends_a_flagged_line_to_quarantine(tmp_path: Path) -> None:
    record = quarantined(
        tmp_path, row(month="13", energy="12.5") + ";;1", HEADER_AMPRION_EXTRA, row() + ";;"
    )

    assert record["fehlercode"] == "BAD_NUMBER"


def test_separator_inside_quotes_goes_to_quarantine(tmp_path: Path) -> None:
    """Amprion 2024: two lines with '"EEG…; EEG…"'. Quotes are not parsed as CSV quoting."""
    record = quarantined(tmp_path, row('"EEG900000000008; EEG900000000004"'))

    assert record["fehlercode"] == "FIELD_COUNT"


def test_undecodable_line_goes_to_quarantine(tmp_path: Path) -> None:
    """50Hertz 2020: eight keys with a DOS umlaut (0x81 = 'ü' in CP850) in a cp1252 file."""
    content = csv(HEADER, row()) + row(key="E3\x81").encode("latin-1") + b"\r\n"
    path = make_zip(tmp_path / "t.zip", {"a.csv": content})

    clean, quarantine = read_zip(path)

    assert clean.height == 1
    assert quarantine["fehlercode"].to_list() == ["UNDECODABLE"]


def test_first_failing_check_decides_the_code(tmp_path: Path) -> None:
    record = quarantined(tmp_path, row(form="9", energy="x"))

    assert record["fehlercode"] == "BAD_VERAEUSSERUNGSFORM"


def test_empty_amount_is_read_as_null(tmp_path: Path) -> None:
    clean, quarantine = read_lines(tmp_path, row(energy=""))

    assert quarantine.is_empty()
    assert clean.select("strommenge", "eeg_zahlung").row(0) == (None, pytest.approx(1128.2))
    assert flags_of(clean) == [[]]


# --- layouts of 2016 to 2023 --------------------------------------------------------------


def read_old(
    tmp_path: Path, header: str, *lines: str, uenb: str = "tennet", name: str = "a.csv"
) -> tuple[pl.DataFrame, pl.DataFrame]:
    path = make_zip(tmp_path / "t.zip", {name: cp1252_csv(header, *lines)})
    return read_zip(path, uenb)


def test_layout_up_to_2019_has_no_mastr_columns_and_no_mastr_flags(tmp_path: Path) -> None:
    lines = [
        "E90000000000000000000000000000003;10009001;Elektrizitätswerk Musterdorf GmbH;"
        "SoK81a------01;2718;1383,46;0,00",
        "E90000000000000000000000000000003;10009001;Netz GmbH;So-vNNe--SpE07;0;0,00;28,14",
    ]
    clean, quarantine = read_old(tmp_path, HEADER_2017, *lines)

    assert quarantine.is_empty()
    first = clean.row(0, named=True)
    assert first["anlagenschluessel"] == "E90000000000000000000000000000003"
    assert (first["nb_bnr"], first["netzbetreiber"]) == (
        "10009001",
        "Elektrizitätswerk Musterdorf GmbH",
    )
    assert (first["strommenge"], first["eeg_zahlung"]) == (2718.0, pytest.approx(1383.46))
    assert clean["vermiedene_netzentgelte"].to_list() == [0.0, pytest.approx(28.14)]
    for column in ("eeg_mastr_nr", "nb_mastr_nr", "uenb_mastr_nr", "veraeusserungsform"):
        assert clean[column].to_list() == [None, None]
    assert clean["eeg_einnahmen"].to_list() == [None, None]
    assert clean["monat"].to_list() == [0, 0]
    assert flags_of(clean) == [["ENCODING_CP1252"], []]


def test_50hertz_2016_has_quotes_row_numbers_and_empty_amounts(tmp_path: Path) -> None:
    key = '"E90000000000000000000000S00000004"'
    lines = [
        f'1;10009002;"Stadtwerke Musterstadt";{key};"Sg-vNNe--SpE07";;;131,07',
        f'2;10009002;"Stadtwerke Musterstadt";{key};"SgK330------11";6893;1980,51;',
    ]
    clean, quarantine = read_old(tmp_path, HEADER_50HERTZ_2016, *lines, uenb="50hertz")

    assert quarantine.is_empty()
    columns = ["netzbetreiber", "anlagenschluessel", "verguetungskategorie"]
    assert clean.select(columns).row(1) == (
        "Stadtwerke Musterstadt",
        "E90000000000000000000000S00000004",
        "SgK330------11",
    )
    amounts = ["strommenge", "eeg_zahlung", "vermiedene_netzentgelte"]
    assert clean.select(amounts).rows() == [
        (None, None, pytest.approx(131.07)),
        (6893.0, pytest.approx(1980.51), None),
    ]
    assert flags_of(clean) == [[], []]


def test_transnetbw_2016_has_an_id_column(tmp_path: Path) -> None:
    line = (
        '17902;10009003;"Stadtwerke Schwäbisch Musterhall GmbH";'
        "E90000000000000000000000S00000005;SgK33a2-----SV;4;0;0"
    )
    clean, _ = read_old(tmp_path, HEADER_TRANSNETBW_2016, line, uenb="transnetbw")

    assert clean.select("netzbetreiber", "strommenge").row(0) == (
        "Stadtwerke Schwäbisch Musterhall GmbH",
        4.0,
    )


def test_ignored_columns_may_hold_values(tmp_path: Path) -> None:
    """Amprion 2016 vNNE file: Bemessungsleistung is read but not kept."""
    line = "E90000000000000000000000000000006;Sg-vNNe--SpE07;4,87;12,5"
    clean, quarantine = read_old(tmp_path, HEADER_AMPRION_2016_VNE, line, uenb="amprion")

    assert quarantine.is_empty()
    assert clean.select("strommenge", "eeg_zahlung", "vermiedene_netzentgelte").row(0) == (
        None,
        None,
        pytest.approx(4.87),
    )


@pytest.mark.parametrize(
    ("energy", "payment", "expected"),
    [
        ("24.113,00", "5.890,80", (24113.0, 5890.8)),  # Amprion 2017
        ("618.243", "138.981,03", (618243.0, 138981.03)),  # 50Hertz 2020
        ("1.234.567", "-1.000,5", (1234567.0, -1000.5)),
    ],
)
def test_thousands_separators_are_read(
    tmp_path: Path, energy: str, payment: str, expected: tuple[float, float]
) -> None:
    clean, _ = read_old(tmp_path, HEADER_2020, row_2020(energy=energy, payment=payment))

    assert clean.select("strommenge", "eeg_zahlung").row(0) == pytest.approx(expected)


def test_padded_fields_and_negative_exponents_are_read(tmp_path: Path) -> None:
    """Amprion 2018 and 2019: values padded with spaces, rounding noise like '4,1023E-06'."""
    pad = " " * 24
    lines = [
        f"E90000000000000000000000000000007;10009004;10009005{pad};SgK4820--Sep17;2650;221,68;"
        f"0{pad};DV",
        f"E90000000000000000000000000000008;10009006;10009005;Sg-vNNe--SpE07;0{pad};0;4,1023E-06",
    ]
    path = make_zip(
        tmp_path / "t.zip",
        {
            "nur DV.csv": cp1252_csv(HEADER_AMPRION_2018, lines[0]),
            "nur vNE.csv": cp1252_csv(HEADER_AMPRION_2018_VNE, lines[1]),
        },
    )
    clean, quarantine = read_zip(path)

    assert quarantine.is_empty()
    assert clean.select("strommenge", "vermiedene_netzentgelte", "veraeusserungsform").rows() == [
        (2650.0, 0.0, 2),
        (0.0, pytest.approx(4.1023e-06), None),
    ]


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("Einspeisevergütung", 1),
        ("EEG-Einspeisevergütung", 1),  # TransnetBW 2020
        ("Marktprämie           ", 2),  # TransnetBW 2020
        ("Direktvermarktung", 2),
        ("Mieterstromzuschlag", 3),
        ("Mieterstromförderung", 3),
        ("Ausfallvergütung", 4),
        ("Ausfallvermarktung", 4),
        ("Sonstiges", 5),
        ("Sonstige", 5),
        ("Vermiedene Netzentgelte", 5),
        ("VNE", 5),
        ("Flexibilitätsprämie/-zuschlag", 5),
        ("Flexibilitätsprämie", 5),
        ("Selbstverbrauch", 5),
    ],
)
def test_veraeusserungsform_text_becomes_its_code(tmp_path: Path, value: str, code: int) -> None:
    clean, quarantine = read_old(tmp_path, HEADER_2020, row_2020(form=value))

    assert quarantine.is_empty()
    assert clean["veraeusserungsform"].to_list() == [code]


@pytest.mark.parametrize(("value", "code"), [("EEG", 1), ("DV", 2), ("MSZ", 3), ("AFV", 4)])
def test_amprion_2018_typ_becomes_veraeusserungsform(tmp_path: Path, value: str, code: int) -> None:
    line = f"E90000000000000000000000000000009;10009004;10009005;SgK4822--Aug18;263;22,94;0;{value}"
    clean, _ = read_old(tmp_path, HEADER_AMPRION_2018, line, uenb="amprion")

    assert clean["veraeusserungsform"].to_list() == [code]


def test_unknown_veraeusserungsform_text_goes_to_quarantine(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path / "t.zip",
        {"a.csv": cp1252_csv(HEADER_2020, row_2020(), row_2020(form="Prämie"))},
    )
    clean, quarantine = read_zip(path, "tennet")

    assert clean.height == 1
    assert quarantine.select("fehlercode", "detail").row(0) == (
        "BAD_VERAEUSSERUNGSFORM",
        "veraeusserungsform='Prämie'",
    )


def test_mastr_checks_run_from_2020_on(tmp_path: Path) -> None:
    """TransnetBW 2020 appends 'AN' to every NB_Mastr_Nr."""
    line = row_2020(mastr_nr="", nb="SNB900000000099AN", uenb="transnetbw")
    clean, _ = read_old(tmp_path, HEADER_TRANSNETBW_2020, line, uenb="transnetbw")

    assert flags_of(clean) == [["MASTR_NR_MISSING", "NB_MASTR_NR_INVALID", "ENCODING_CP1252"]]


@pytest.mark.parametrize(("value", "month"), [("0", 0), ("09", 9), ("12", 12)])
def test_month_with_leading_zero_is_read(tmp_path: Path, value: str, month: int) -> None:
    """Amprion 2021: '09' in 54 rows."""
    clean, _ = read_old(tmp_path, HEADER_2021, row_2020(month=value))

    assert clean["monat"].to_list() == [month]


def test_tennet_2024_first_upload_without_monat_has_annual_values(tmp_path: Path) -> None:
    without_monat = row("", uenb="tennet", energy="6509", payment="2547,62").split(";")[:-1]
    line = ";".join(without_monat)
    path = make_zip(tmp_path / "t.zip", {"Teil 1.csv": csv(HEADER_TENNET_2024_FIRST, line)})

    clean, quarantine = read_zip(path, "tennet")

    assert quarantine.is_empty()
    record = clean.row(0, named=True)
    assert (record["monat"], record["strommenge"], record["eeg_einnahmen"]) == (0, 6509.0, 0.0)
    assert record["hinweise"] == ["MASTR_NR_MISSING"]


def test_tennet_2023_txt_file_with_quoted_fields(tmp_path: Path) -> None:
    line = (
        '"E90000000000000000000000000000010";"EEG900000000001";"10009001";"SNB900000000001";'
        '"Elektrizitätswerk Musterdorf GmbH";"SNB970033313272";"SgK4820--Jul22";"4180,000";'
        '"260,83";"0,00";1;"7"'
    )
    clean, quarantine = read_old(tmp_path, HEADER_TENNET_2023, line, name="Bewegungsdaten 2023.txt")

    assert quarantine.is_empty()
    record = clean.row(0, named=True)
    assert record["anlagenschluessel"] == "E90000000000000000000000000000010"
    assert record["netzbetreiber"] == "Elektrizitätswerk Musterdorf GmbH"
    assert (record["strommenge"], record["veraeusserungsform"], record["monat"]) == (4180.0, 1, 7)
    assert record["quelldatei"] == "Bewegungsdaten 2023.txt"
    assert record["hinweise"] == ["ENCODING_CP1252"]


def test_amprion_2022_layout_has_text_form_and_vnne(tmp_path: Path) -> None:
    line = (
        "EEG900000000001;E90000000000000000000000000000011;SNB900000000001;SNB976890256486;"
        "Einspeisevergütung;SgK330------10;14211;5562,02;0;10"
    )
    clean, _ = read_old(tmp_path, HEADER_AMPRION_2022, line, uenb="amprion")

    record = clean.row(0, named=True)
    assert (record["veraeusserungsform"], record["monat"]) == (1, 10)
    assert (record["vermiedene_netzentgelte"], record["eeg_einnahmen"]) == (0.0, None)
    assert record["hinweise"] == ["ENCODING_CP1252"]


def test_amprion_2017_row_numbers_are_dropped(tmp_path: Path) -> None:
    line = (
        "1;E90000000000000000000000000000012;10009007;Netzgesellschaft Musterbecke mbH;"
        "So-vNNe--SpE07;0;0;9,85"
    )
    clean, _ = read_old(tmp_path, HEADER_AMPRION_2017, line, uenb="amprion")

    assert clean.columns == CLEAN_SCHEMA.names()
    assert clean.select("anlagenschluessel", "vermiedene_netzentgelte").row(0) == (
        "E90000000000000000000000000000012",
        pytest.approx(9.85),
    )


# --- bookkeeping --------------------------------------------------------------------------


def test_every_line_ends_up_exactly_once(tmp_path: Path) -> None:
    lines = [row(), row(form="7"), row(""), row(month="99"), row() + ";", row(energy="")]
    path = make_zip(tmp_path / "t.zip", {"a.csv": csv(HEADER, *lines)})

    clean, quarantine = read_zip(path, block_size=120)

    assert clean.height + quarantine.height == len(lines)
    assert sorted([*clean["zeile"], *quarantine["zeile"]]) == list(range(2, 2 + len(lines)))


def test_codes_are_documented() -> None:
    assert set(QUARANTINE_CODES).isdisjoint(QA_FLAGS)
    assert all(text for text in [*QUARANTINE_CODES.values(), *QA_FLAGS.values()])
