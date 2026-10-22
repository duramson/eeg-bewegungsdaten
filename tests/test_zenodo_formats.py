from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eeg_bewegungsdaten.quality import check
from eeg_bewegungsdaten.reader import iter_chunks, to_text
from tests.builders import csv

DERIVED = '"Unit_ID";"EEG_tariff";"Generation";"Payment";"ANC"'
HERTZ_2010 = (
    "Netzbetreiber;Anlagenschlüssel;Energieträger;Standort der Anlage Ort/ Gemarkung;PLZ;"
    "Straße/ Flurstück;Bundesland;Installierte Leistung [kW];EEG Strom [kWh];Vergütung [Euro];"
    "vNNE [Euro];Einsatzstoffe nach §8Abs.2 bei Biomasseanlagen;Inbetriebnahmejahr;"
    "Einspeisespannungsebene"
)


def test_derived_amounts_keep_decimal_commas_and_missing_values(tmp_path: Path) -> None:
    path = tmp_path / "2012_a.txt"
    path.write_bytes(csv(DERIVED, '"E900001";"SoK330------11";7852;2256,66;NA'))
    [chunk] = list(iter_chunks(path))
    text = to_text(chunk, jahr=2012, uenb="amprion")

    clean, quarantine = check(text)

    assert quarantine.is_empty()
    assert text["vermiedene_netzentgelte"].to_list() == ["NA"]
    assert text["eeg_zahlung"].to_list() == ["2256,66"]
    assert clean.select("strommenge", "eeg_zahlung", "vermiedene_netzentgelte").rows() == [
        (7852.0, 2256.66, None)
    ]
    text.write_parquet(tmp_path / "text.parquet")
    rebuilt, _ = check(pl.read_parquet(tmp_path / "text.parquet"))
    assert rebuilt.equals(clean)


@pytest.mark.parametrize(
    ("filename", "header"),
    [
        (
            "Internet_Bewegungsdaten_VNNE_2013.csv",
            "ANLAGENSCHLUESSEL;VERGUETUNGSKATEGORIE;MENGE [KWH];BETRAG [EURO]",
        ),
        (
            "Bewegungsdaten_VNNE_2014.csv",
            "ANLAGENSCHLUESSEL;VERGUETUNGSKATEGORIE;MENGE_KWH;BETRAG_EURO",
        ),
    ],
)
def test_fee_files_keep_the_amount_out_of_payments(
    tmp_path: Path, filename: str, header: str
) -> None:
    path = tmp_path / filename
    path.write_bytes(csv(header, "E900001;So-vNNe-----13;1200;12,34"))
    [chunk] = list(iter_chunks(path))

    clean, quarantine = check(to_text(chunk, jahr=2013, uenb="amprion"))

    assert quarantine.is_empty()
    assert clean.select("strommenge", "eeg_zahlung", "vermiedene_netzentgelte").rows() == [
        (1200.0, None, 12.34)
    ]


def test_50hertz_2010_preserves_addresses_without_inventing_categories(tmp_path: Path) -> None:
    path = tmp_path / "2010.csv"
    line = "Netz GmbH;E900001;Solar;Musterort;12345;Teststraße 1;Land;9;100;25,5;1,2;;2009;NS"
    path.write_bytes(csv(HERTZ_2010, line))
    [chunk] = list(iter_chunks(path))
    text = to_text(chunk, jahr=2010, uenb="50hertz")

    clean, quarantine = check(text)

    assert quarantine.is_empty()
    assert text["rohzeile"].to_list() == [line]
    assert clean["verguetungskategorie"].to_list() == [None]
    assert clean.select("strommenge", "eeg_zahlung", "vermiedene_netzentgelte").rows() == [
        (100.0, 25.5, 1.2)
    ]
    assert "Teststraße" not in str(clean.to_dicts())


@pytest.mark.parametrize("year", range(2011, 2016))
def test_transnetbw_headers_map_the_same_amounts(tmp_path: Path, year: int) -> None:
    if year < 2013:
        header = "Netzbetreiber;Anlagenschlüssel;Vergütungskategorie;kWh;Vergütung;"
        header += "vNE" if year == 2012 else "vNNE"
        line = "Netz GmbH;E900001;SoK330------11;100;25,5;1,2"
    else:
        header = (
            "Netzbetreiber Name;Anlagenschlüssel;Kategorie;"
            f"in {year} eingespeiste Jahresarbeit [kWh];"
            f"Vergütung für in {year} abgenommene EEG-Einspeisung [EUR];"
            "vermiedene Netznutzungsentgelte (vNNE) [EUR]"
        )
        if year >= 2014:
            header = header.replace("Anlagenschlüssel", "ÜNB-Anlagennummer")
        line = "Netz GmbH;E900001;SoK330------11;100;25,5;1,2"
        if year == 2015:
            header = " ID;Netzbetreiber Betriebsnummer;" + header
            line = "1;10000000;" + line
    path = tmp_path / f"{year}.csv"
    path.write_bytes(csv(header, line))
    [chunk] = list(iter_chunks(path))

    clean, quarantine = check(to_text(chunk, jahr=year, uenb="transnetbw"))

    assert quarantine.is_empty()
    assert clean.select("strommenge", "eeg_zahlung", "vermiedene_netzentgelte").rows() == [
        (100.0, 25.5, 1.2)
    ]


def test_older_transnetbw_reshaped_columns_need_no_new_numeric_rules(tmp_path: Path) -> None:
    path = tmp_path / "2007.csv"
    line = "E900001;Solar;;4389;2221,71;8,78"
    path.write_bytes(csv("Unit_ID;Tech;Tariff;gen;pay;ANC", line))
    [chunk] = list(iter_chunks(path))
    text = to_text(chunk, jahr=2007, uenb="transnetbw")

    clean, quarantine = check(text)

    assert quarantine.is_empty()
    assert clean["verguetungskategorie"].to_list() == [None]
    assert text["rohzeile"].to_list() == [line]
    assert clean.select("strommenge", "eeg_zahlung", "vermiedene_netzentgelte").rows() == [
        (4389.0, 2221.71, 8.78)
    ]
