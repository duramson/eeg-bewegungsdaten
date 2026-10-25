from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import ANY

import polars as pl
import pytest

from eeg_bewegungsdaten import cli, mastr
from eeg_bewegungsdaten.download import sha256_of
from eeg_bewegungsdaten.ingest import ingest
from eeg_bewegungsdaten.mastr import (
    MASTR_SCHEMA,
    MATCH_CODES,
    Bridge,
    MastrError,
    check_match,
    clean_files,
    default_db_path,
    link,
    link_file,
    load_bridge,
    load_register,
)
from eeg_bewegungsdaten.paths import output_paths
from tests.builders import (
    HEADER,
    PLANT_COLUMNS,
    archive,
    csv,
    deleted_units,
    kwk_units,
    make_mastr_db,
    plants,
    row,
    units,
)
from tests.old_formats import HEADER_2017, cp1252_csv

KEY_A = "E92000000000000000000000000000001"
KEY_SHARED = "E92000000000000000000000000000002"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return make_mastr_db(
        tmp_path / "open-mastr.db",
        {
            "AnlagenEegSolar": plants(
                ("EEG900000000001", KEY_A + " "),
                ("EEG900000000002", KEY_SHARED),
                ("EEG900000000004", ""),
            ),
            "EinheitenSolar": units(
                ("SEE900000000011", "EEG900000000002"),
                ("SEE900000000012", None),
            ),
            "AnlagenEegWind": plants(("EEG900000000008", KEY_SHARED)),
            "EinheitenWind": units(("SEE900000000013", "EEG900000000008")),
            "AnlagenEegBiomasse": plants(
                ("EEG900000000005", None), ("EEG900000000006", None), ("EEG900000000007", None)
            ),
            "EinheitenBiomasse": kwk_units(
                # Two units of one EEG plant and one KWK plant.
                ("SEE900000000021", "EEG900000000005", "KWK900000000031"),
                ("SEE900000000022", "EEG900000000005", "KWK900000000031"),
                # One KWK plant spanning two EEG plants.
                ("SEE900000000023", "EEG900000000006", "KWK900000000032"),
                ("SEE900000000024", "EEG900000000007", "KWK900000000032 "),
                ("SEE900000000025", None, "KWK900000000033"),
            ),
            # Deleted units are no longer in the unit tables.
            "GeloeschteUndDeaktivierteEinheiten": deleted_units(
                "SEE900000000041", "SEE900000000042"
            ),
        },
    )


def test_load_register_reads_plants_units_and_keys(db: Path) -> None:
    register = load_register(db)

    assert register.plants.sort("eeg_nr").rows() == [
        ("EEG900000000001", KEY_A, "solar"),
        ("EEG900000000002", KEY_SHARED, "solar"),
        ("EEG900000000004", None, "solar"),
        ("EEG900000000005", None, "biomass"),
        ("EEG900000000006", None, "biomass"),
        ("EEG900000000007", None, "biomass"),
        ("EEG900000000008", KEY_SHARED, "wind"),
    ]
    # Units without an EEG number are left out. Only biomass and GSGK units have KWK numbers.
    assert register.units.sort("einheit_nr").rows() == [
        ("SEE900000000011", "EEG900000000002", None, "solar"),
        ("SEE900000000013", "EEG900000000008", None, "wind"),
        ("SEE900000000021", "EEG900000000005", "KWK900000000031", "biomass"),
        ("SEE900000000022", "EEG900000000005", "KWK900000000031", "biomass"),
        ("SEE900000000023", "EEG900000000006", "KWK900000000032", "biomass"),
        ("SEE900000000024", "EEG900000000007", "KWK900000000032", "biomass"),
    ]
    keys = register.keys.sort("anlagenschluessel").select("anlagenschluessel", "plants")
    assert keys.rows() == [(KEY_A, 1), (KEY_SHARED, 2)]


def test_load_register_counts_eeg_plants_per_kwk_number(db: Path) -> None:
    kwk = load_register(db).kwk.sort("kwk_nr")

    assert kwk.select("kwk_nr", "plants").rows() == [
        ("KWK900000000031", 1),
        ("KWK900000000032", 2),
    ]
    assert kwk.row(0) == ("KWK900000000031", 1, "EEG900000000005", "biomass")


def test_load_register_reads_deleted_units(db: Path) -> None:
    register = load_register(db)

    assert register.deleted.sort("einheit_nr").rows() == [
        ("SEE900000000041",),
        ("SEE900000000042",),
    ]
    assert register.source["tables"]["GeloeschteUndDeaktivierteEinheiten"]["rows"] == 2


def test_load_register_describes_the_source(db: Path) -> None:
    source = load_register(db).source

    assert source["db"] == str(db)
    assert source["size"] == db.stat().st_size
    assert source["tables"]["AnlagenEegSolar"] == {
        "name": "AnlagenEegSolar",
        "rows": 3,
        "datum_download": ["20261017"],
    }
    assert source["tables"]["EinheitenSolar"]["rows"] == 1
    assert "AnlagenEegWasser" in source["missing_tables"]
    assert "EinheitenStromSpeicher" in source["missing_tables"]
    assert len(source["tables"]) + len(source["missing_tables"]) == 13


def test_load_register_does_not_change_the_database(db: Path) -> None:
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    load_register(db)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in db.parent.iterdir()) == ["open-mastr.db"]


def test_load_register_reads_table_names_of_open_mastr_before_1_0(tmp_path: Path) -> None:
    db = make_mastr_db(
        tmp_path / "old.db",
        {
            "solar_eeg": plants(("EEG900000000001", KEY_A)),
            "solar_extended": units(("SEE900000000011", "EEG900000000001")),
            "deleted_units": deleted_units("SEE900000000041"),
        },
    )

    register = load_register(db)

    assert register.plants["eeg_nr"].to_list() == ["EEG900000000001"]
    assert register.units["einheit_nr"].to_list() == ["SEE900000000011"]
    assert register.kwk.is_empty()
    assert register.deleted["einheit_nr"].to_list() == ["SEE900000000041"]
    assert register.source["tables"]["AnlagenEegSolar"]["name"] == "solar_eeg"
    assert register.source["tables"]["GeloeschteUndDeaktivierteEinheiten"]["name"] == (
        "deleted_units"
    )


def test_load_register_without_units_and_download_date_gives_empty_frames(tmp_path: Path) -> None:
    db = make_mastr_db(
        tmp_path / "x.db",
        {"AnlagenEegWind": (PLANT_COLUMNS[:2], [("EEG900000000008", None)])},
    )

    register = load_register(db)

    assert register.units.is_empty()
    assert register.units.columns == ["einheit_nr", "eeg_nr", "kwk_nr", "energietraeger"]
    assert register.keys.is_empty()
    assert register.kwk.is_empty()
    assert register.deleted.is_empty()
    assert register.deleted.schema == pl.Schema({"einheit_nr": pl.String()})
    assert "GeloeschteUndDeaktivierteEinheiten" in register.source["missing_tables"]
    assert register.source["tables"]["AnlagenEegWind"]["datum_download"] == []


def test_load_register_needs_eeg_plants(tmp_path: Path) -> None:
    db = make_mastr_db(
        tmp_path / "x.db", {"EinheitenSolar": units(("SEE900000000011", "EEG900000000001"))}
    )
    with pytest.raises(MastrError, match="no table of EEG plants"):
        load_register(db)


def test_load_register_rejects_english_column_names(tmp_path: Path) -> None:
    db = make_mastr_db(
        tmp_path / "x.db",
        {"AnlagenEegSolar": (("eegMastrNumber", "plantKeyEeg"), [("EEG900000000001", KEY_A)])},
    )
    with pytest.raises(
        MastrError, match=r"no columns \['AnlagenschluesselEeg', 'EegMastrNummer'\]"
    ):
        load_register(db)


def test_load_register_without_database_fails(tmp_path: Path) -> None:
    with pytest.raises(MastrError, match="not found"):
        load_register(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


def test_load_register_reads_large_tables_in_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("eeg_bewegungsdaten.mastr._BATCH", 2)
    rows = [(f"EEG9000000005{i:02d}", None) for i in range(5)]
    db = make_mastr_db(tmp_path / "x.db", {"AnlagenEegSolar": plants(*rows)})

    assert load_register(db).plants["eeg_nr"].to_list() == [nr for nr, _ in rows]


def test_default_db_path_follows_open_mastr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SQLITE_DATABASE_PATH", raising=False)
    monkeypatch.delenv("MASTR_PROJECT_HOME_DIR", raising=False)
    # The home directory comes from HOME on POSIX and from USERPROFILE on Windows.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_db_path() == tmp_path / ".open-MaStR" / "data" / "sqlite" / "open-mastr.db"

    monkeypatch.setenv("MASTR_PROJECT_HOME_DIR", str(tmp_path / "home"))
    assert default_db_path() == tmp_path / "home" / "data" / "sqlite" / "open-mastr.db"

    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "db.sqlite"))
    assert default_db_path() == tmp_path / "db.sqlite"


def test_register_frames_are_strings(db: Path) -> None:
    register = load_register(db)
    for frame in (register.plants, register.units):
        assert set(frame.schema.dtypes()) == {pl.String()}


# EEG_Mastr_Nr, EEG_Anlagenschluessel
# -> treffer, eeg_mastr_nr_abgeglichen, eeg_mastr_nrs_abgeglichen, energietraeger
CASES = [
    (("EEG900000000001", ""), ("EEG_NR", "EEG900000000001", None, "solar")),
    (("SEE900000000011", KEY_A), ("UNIT_NR", "EEG900000000002", None, "solar")),
    # Two plants; the Anlagenschluessel is not used once a number matched.
    (
        ("EEG900000000001,EEG900000000008", KEY_A),
        ("MULTIPLE", None, ["EEG900000000001", "EEG900000000008"], None),
    ),
    # Both numbers lead to the same plant.
    (
        ("EEG900000000002 / SEE900000000011", ""),
        ("MULTIPLE", "EEG900000000002", ["EEG900000000002"], "solar"),
    ),
    (
        ("EEG900000000001,EEG999999999999", ""),
        ("MULTIPLE", "EEG900000000001", ["EEG900000000001"], "solar"),
    ),
    (("", KEY_A), ("ANLAGENSCHLUESSEL", "EEG900000000001", None, "solar")),
    (("Grund_MASTRNR99", KEY_A), ("ANLAGENSCHLUESSEL", "EEG900000000001", None, "solar")),
    (("EEG999999999999", KEY_A), ("ANLAGENSCHLUESSEL", "EEG900000000001", None, "solar")),
    (
        ("EEG999999999998,EEG999999999999", KEY_A),
        ("ANLAGENSCHLUESSEL", "EEG900000000001", None, "solar"),
    ),
    (("", KEY_SHARED), ("AMBIGUOUS", None, None, None)),
    (("KWK900000000031", ""), ("KWK_NR", "EEG900000000005", None, "biomass")),
    # The Anlagenschluessel comes first; the KWK number only resolves an ambiguous one.
    (("KWK900000000031", KEY_A), ("ANLAGENSCHLUESSEL", "EEG900000000001", None, "solar")),
    (("KWK900000000031", KEY_SHARED), ("KWK_NR", "EEG900000000005", None, "biomass")),
    (("KWK900000000032", ""), ("AMBIGUOUS", None, None, None)),
    # The KWK unit has no EEG number.
    (("KWK900000000033", ""), ("NOT_FOUND", None, None, None)),
    # Deleted units; the Anlagenschluessel still comes first.
    (("SEE900000000041", ""), ("DELETED", None, None, None)),
    (("SEE900000000041", KEY_A), ("ANLAGENSCHLUESSEL", "EEG900000000001", None, "solar")),
    (("EEG999999999999,SEE900000000042", ""), ("DELETED", None, None, None)),
    # The unit exists but has no EEG number.
    (("SEE900000000012", "E92000000000000000000000000000009"), ("NOT_FOUND", None, None, None)),
    (("EEG999999999999", ""), ("NOT_FOUND", None, None, None)),
    (("Grund_MASTRNR99", ""), ("NO_ID", None, None, None)),
    (("", ""), ("NO_ID", None, None, None)),
    (("KWK90000000003", ""), ("NO_ID", None, None, None)),
    # Invalid: 13 digits. Its first 12 digits must not be taken as a number.
    (("EEG9000000000011", ""), ("NO_ID", None, None, None)),
]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    lines = [row(nr, key=key) for (nr, key), _ in CASES]
    # Without a payment in the last row, the hit rate of the payments differs from that of rows.
    lines[-1] = row(*CASES[-1][0][:1], key="", payment="0")
    ingest(archive(data_dir, {"Bewegungsdaten_mpm.csv": csv(HEADER, *lines)}), data_dir)
    return data_dir


def test_mastr_refuses_clean_files_of_an_older_version(data_dir: Path, db: Path) -> None:
    path = output_paths(data_dir, 2025, "amprion").clean
    pl.read_parquet(path).rename({"hinweise": "qa_flags"}).write_parquet(path)
    register = load_register(db)

    with pytest.raises(MastrError, match=r"has no column hinweise.*run 'ingest' again"):
        load_bridge(data_dir, register)
    with pytest.raises(MastrError, match=r"has no column hinweise.*run 'ingest' again"):
        link_file(data_dir, 2025, "amprion", register)
    assert not output_paths(data_dir, 2025, "amprion").mastr.exists()


def test_link_file_matches_every_case(data_dir: Path, db: Path) -> None:
    link_file(data_dir, 2025, "amprion", load_register(db))

    linked = pl.read_parquet(output_paths(data_dir, 2025, "amprion").mastr)
    assert linked.schema == MASTR_SCHEMA
    assert linked["zeile"].to_list() == list(range(2, len(CASES) + 2))
    got = linked.select(
        "treffer", "eeg_mastr_nr_abgeglichen", "eeg_mastr_nrs_abgeglichen", "energietraeger"
    ).rows()
    assert got == [expected for _, expected in CASES]


def test_link_resolves_an_eeg_number_given_for_several_plants(tmp_path: Path) -> None:
    key_1, key_2, key_3, key_shared, key_x, key_y, key_z = (
        f"E9200000000000000000000000000010{i}" for i in range(7)
    )
    db = make_mastr_db(
        tmp_path / "x.db",
        {
            "AnlagenEegWind": plants(
                ("EEG900000000101", key_1),
                ("EEG900000000102", key_2),
                ("EEG900000000103", key_shared),
                ("EEG900000000104", key_shared),
                ("EEG900000000105", key_3),
            ),
            "AnlagenEegBiomasse": plants(("EEG900000000201", key_x)),
        },
    )
    # EEG_Mastr_Nr, EEG_Anlagenschluessel -> treffer, eeg_mastr_nr_abgeglichen, energietraeger
    cases = [
        # A wind farm: the grid operator gives the number of the first turbine in the rows of
        # all turbines, each with the turbine's own Anlagenschluessel.
        (("EEG900000000101", key_1), ("EEG_NR", "EEG900000000101", "wind")),
        (("EEG900000000101", key_1), ("EEG_NR", "EEG900000000101", "wind")),
        (("EEG900000000101", key_2), ("ANLAGENSCHLUESSEL", "EEG900000000102", "wind")),
        # Anlagenschluessel not in the MaStR, of several plants, or missing.
        (("EEG900000000101", key_y), ("EEG_NR_CONFLICT", None, None)),
        (("EEG900000000101", key_shared), ("EEG_NR_CONFLICT", None, None)),
        (("EEG900000000101", None), ("EEG_NR_CONFLICT", None, None)),
        # Several Anlagenschluessel, but none of another plant: perhaps engines of one plant.
        (("EEG900000000201", key_x), ("EEG_NR", "EEG900000000201", "biomass")),
        (("EEG900000000201", key_z), ("EEG_NR", "EEG900000000201", "biomass")),
        # One Anlagenschluessel, of another plant: the number stays.
        (("EEG900000000105", key_2), ("EEG_NR", "EEG900000000105", "wind")),
    ]
    clean = pl.LazyFrame(
        {
            "eeg_mastr_nr": [nr for (nr, _), _ in cases],
            "anlagenschluessel": [key for (_, key), _ in cases],
            "hinweise": [[] for _ in cases],
        },
        schema={
            "eeg_mastr_nr": pl.String(),
            "anlagenschluessel": pl.String(),
            "hinweise": pl.List(pl.String()),
        },
    )

    linked = link(clean, load_register(db)).collect()

    got = linked.select("treffer", "eeg_mastr_nr_abgeglichen", "energietraeger").rows()
    assert got == [expected for _, expected in cases]


KEY_B, KEY_C, KEY_D, KEY_E, KEY_F = (f"E9200000000000000000000000000020{i}" for i in range(5))


@pytest.fixture
def years_dir(tmp_path: Path) -> Path:
    """2025 and 2024 with MaStR numbers, 2019 without; Anlagenschluessel not in the MaStR or
    of several plants."""
    data_dir = tmp_path / "data"
    lines_2025 = [
        row("EEG900000000004", key=KEY_B),
        row("SEE900000000013", key=KEY_SHARED),
        row("EEG900000000002 / SEE900000000011", key=KEY_E),
        # Two plants: no single plant for the Anlagenschluessel.
        row("EEG900000000001,EEG900000000008", key=KEY_D),
        row("EEG900000000004", key=KEY_C),
        # Found by Anlagenschluessel, not by number.
        row("EEG999999999999", key=KEY_A),
        row("", key=KEY_B),
        row("", key=KEY_F),
    ]
    ingest(archive(data_dir, {"a.csv": csv(HEADER, *lines_2025)}), data_dir)
    # Another TSO and year names another plant for KEY_C.
    lines_2024 = [row("EEG900000000005", key=KEY_C, uenb="tennet")]
    ingest(archive(data_dir, {"a.csv": csv(HEADER, *lines_2024)}, "tennet", 2024), data_dir)
    keys_2019 = [KEY_B, KEY_SHARED, KEY_E, KEY_C, KEY_D, KEY_A, KEY_F]
    lines_2019 = [f"{key};10009001;Netz GmbH;SoK81a------01;2718;1383,46;" for key in keys_2019]
    members = {"a.csv": cp1252_csv(HEADER_2017, *lines_2019)}
    ingest(archive(data_dir, members, "transnetbw", 2019), data_dir)
    return data_dir


def test_load_bridge_takes_the_keys_of_rows_matched_by_number(years_dir: Path, db: Path) -> None:
    bridge = load_bridge(years_dir, load_register(db))

    assert bridge.source == {
        "years": [2024, 2025],
        "keys": 3,
        "keys_with_several_plants": 1,
        # 2019 adds no key, but it is read all the same.
        "clean_files": [
            {
                "jahr": jahr,
                "uenb": uenb,
                "sha256": sha256_of(output_paths(years_dir, jahr, uenb).clean),
            }
            for jahr, uenb in [(2019, "transnetbw"), (2024, "tennet"), (2025, "amprion")]
        ],
    }
    assert bridge.keys.sort("anlagenschluessel").rows() == [
        (KEY_SHARED, 1, "EEG900000000008", "wind"),
        (KEY_B, 1, "EEG900000000004", "solar"),
        # Of plants 4 and 5.
        (KEY_C, 2, ANY, ANY),
        (KEY_E, 1, "EEG900000000002", "solar"),
    ]


def test_link_file_matches_rows_by_the_bridge(years_dir: Path, db: Path) -> None:
    register = load_register(db)
    bridge = load_bridge(years_dir, register)

    report = link_file(years_dir, 2019, "transnetbw", register, bridge)

    linked = pl.read_parquet(output_paths(years_dir, 2019, "transnetbw").mastr)
    assert linked.select("treffer", "eeg_mastr_nr_abgeglichen", "energietraeger").rows() == [
        ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000004", "solar"),
        # Of plants 2 and 8 in the MaStR; the rows matched by number name plant 8.
        ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000008", "wind"),
        ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000002", "solar"),
        ("NOT_FOUND", None, None),
        ("NOT_FOUND", None, None),
        ("ANLAGENSCHLUESSEL", "EEG900000000001", "solar"),
        ("NOT_FOUND", None, None),
    ]
    assert report["treffer"]["ANLAGENSCHLUESSEL_BRIDGE"]["rows"] == 3
    assert report["match_rate"]["rows"] == round(4 / 7, 6)
    assert report["bridge"] == bridge.source


def test_link_file_uses_the_bridge_in_years_with_mastr_numbers(years_dir: Path, db: Path) -> None:
    register = load_register(db)

    link_file(years_dir, 2025, "amprion", register, load_bridge(years_dir, register))

    linked = pl.read_parquet(output_paths(years_dir, 2025, "amprion").mastr)
    assert linked["treffer"].to_list() == [
        "EEG_NR",
        "UNIT_NR",
        "MULTIPLE",
        "MULTIPLE",
        "EEG_NR",
        "ANLAGENSCHLUESSEL",
        "ANLAGENSCHLUESSEL_BRIDGE",
        "NOT_FOUND",
    ]
    assert linked.select("eeg_mastr_nr_abgeglichen", "energietraeger").row(6) == (
        "EEG900000000004",
        "solar",
    )


def test_link_leaves_deleted_and_conflicting_rows_to_the_bridge(db: Path) -> None:
    register = load_register(db)
    keys = pl.DataFrame(
        {
            "anlagenschluessel": [KEY_B, KEY_SHARED],
            "plants": [1, 1],
            "eeg_nr": ["EEG900000000004", "EEG900000000008"],
            "energietraeger": ["solar", "wind"],
        }
    )
    # EEG_Mastr_Nr, EEG_Anlagenschluessel -> treffer, eeg_mastr_nr_abgeglichen
    cases = [
        (("SEE900000000041", KEY_B), ("DELETED", None)),
        # Number 4 is given with the Anlagenschluessel of plant 1 too.
        (("EEG900000000004", KEY_A), ("ANLAGENSCHLUESSEL", "EEG900000000001")),
        (("EEG900000000004", KEY_B), ("EEG_NR_CONFLICT", None)),
        # A number found comes before the bridge.
        (("EEG900000000001", KEY_B), ("EEG_NR", "EEG900000000001")),
        (("SEE900000000041", KEY_SHARED), ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000008")),
        (("KWK900000000032", KEY_B), ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000004")),
        (("Grund_MASTRNR99", ""), ("NO_ID", None)),
    ]
    clean = pl.LazyFrame(
        {
            "eeg_mastr_nr": [nr for (nr, _), _ in cases],
            "anlagenschluessel": [key or None for (_, key), _ in cases],
            "hinweise": [[] if nr[0] in "ES" else ["MASTR_NR_INVALID"] for (nr, _), _ in cases],
        },
        schema={
            "eeg_mastr_nr": pl.String(),
            "anlagenschluessel": pl.String(),
            "hinweise": pl.List(pl.String()),
        },
    )

    linked = link(clean, register, Bridge(keys, {})).collect()

    assert linked.select("treffer", "eeg_mastr_nr_abgeglichen").rows() == [
        expected for _, expected in cases
    ]


def test_link_file_writes_a_report_with_hit_rates(data_dir: Path, db: Path) -> None:
    register = load_register(db)

    report = link_file(data_dir, 2025, "amprion", register)

    assert report["rows"] == len(CASES)
    assert {code: n["rows"] for code, n in report["treffer"].items()} == {
        "EEG_NR": 1,
        "UNIT_NR": 1,
        "MULTIPLE": 3,
        "ANLAGENSCHLUESSEL": 6,
        "KWK_NR": 2,
        "ANLAGENSCHLUESSEL_BRIDGE": 0,
        "EEG_NR_CONFLICT": 0,
        "AMBIGUOUS": 2,
        "DELETED": 2,
        "NOT_FOUND": 3,
        "NO_ID": 4,
    }
    assert list(report["treffer"]) == list(MATCH_CODES)
    assert report["treffer"]["NO_ID"]["eeg_zahlung"] == pytest.approx(3 * 1128.2)
    # 13 of 24 rows; the last row (NO_ID) has no payment, so 13 of 23 payments.
    assert report["match_rate"] == {
        "rows": round(13 / 24, 6),
        "eeg_zahlung": round(13 / 23, 6),
    }
    assert report["mastr"] == register.source
    assert report["bridge"] is None
    report_path = output_paths(data_dir, 2025, "amprion").mastr_report
    assert report_path.name == "amprion.mastr.json"
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_link_file_replaces_old_output_and_leaves_no_temporary_files(
    data_dir: Path, db: Path
) -> None:
    register = load_register(db)
    link_file(data_dir, 2025, "amprion", register)
    first = pl.read_parquet(output_paths(data_dir, 2025, "amprion").mastr)

    link_file(data_dir, 2025, "amprion", register)

    assert pl.read_parquet(output_paths(data_dir, 2025, "amprion").mastr).equals(first)
    assert not list(data_dir.rglob("*.tmp"))


def test_link_file_without_any_match_has_a_hit_rate_of_zero(data_dir: Path, tmp_path: Path) -> None:
    db = make_mastr_db(tmp_path / "empty.db", {"AnlagenEegSolar": plants()})

    report = link_file(data_dir, 2025, "amprion", load_register(db))

    assert report["match_rate"]["rows"] == 0
    assert report["treffer"]["NOT_FOUND"]["rows"] + report["treffer"]["NO_ID"]["rows"] == len(CASES)


def test_clean_files_lists_year_and_tso(data_dir: Path) -> None:
    (data_dir / "clean" / "notes").mkdir()
    (data_dir / "clean" / "notes" / "x.parquet").write_bytes(b"")
    assert clean_files(data_dir) == [(2025, "amprion")]


def test_register_and_bridge_report_progress(data_dir: Path, db: Path) -> None:
    calls: list[tuple[int, int | None]] = []

    register = load_register(db, progress=lambda done, total: calls.append((done, total)))
    assert calls[0] == (0, calls[-1][1])
    assert calls[-1][0] == calls[-1][1]

    calls.clear()
    load_bridge(data_dir, register, progress=lambda done, total: calls.append((done, total)))
    files = len(clean_files(data_dir))
    assert calls[0] == (0, files)
    assert calls[-1] == (files, files)


def test_cli_mastr_prints_hit_rates(
    data_dir: Path, db: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    assert cli.main(["--data-dir", str(data_dir), "-v", "mastr", "--db", str(db)]) == 0

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("✓ MaStR register ")
    assert lines[1].startswith("✓ Bridge ")
    assert lines[2] == "Matching 1 clean files (24 rows) with the Marktstammdatenregister"
    assert lines[3].split()[:8] == [
        "✓",
        "2025",
        "amprion",
        "24",
        "rows",
        "54.2%",
        "matched",
        "56.5%",
    ]
    codes = lines[4]
    assert "EEG_NR 1, UNIT_NR 1, MULTIPLE 3, ANLAGENSCHLUESSEL 6, KWK_NR 2" in codes
    assert "DELETED 2" in codes
    assert "Rows     24 · 54.2% matched · 56.5% of payments" in lines
    assert output_paths(data_dir, 2025, "amprion").mastr.exists()
    assert "Missing MaStR tables" in caplog.text
    assert "AnlagenEegWasser" in caplog.text
    assert "Empty MaStR tables" not in caplog.text


def test_cli_mastr_builds_the_bridge_from_all_years(
    years_dir: Path, db: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    args = ["--data-dir", str(years_dir), "-v", "mastr", "--db", str(db), "--year", "2019"]
    assert cli.main(args) == 0

    lines = capsys.readouterr().err.splitlines()
    bridge = lines[1]
    assert "3 Anlagenschluessel of one EEG plant  1 of several, not used" in bridge
    assert "from the rows matched by number of 2024 to 2025" in bridge
    row = lines[3]
    assert row.split()[:5] == ["✓", "2019", "transnetbw", "7", "rows"]
    assert "+3 bridge 42.9%" in row
    assert "ANLAGENSCHLUESSEL 1, ANLAGENSCHLUESSEL_BRIDGE 3, NOT_FOUND 3" in lines[4]
    assert "Bridge   +3 rows (42.9%) · 0.0 m € · 2019 +42.9%" in lines
    assert not output_paths(years_dir, 2025, "amprion").mastr.exists()


def test_cli_mastr_warns_about_empty_tables(
    data_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = make_mastr_db(
        tmp_path / "x.db",
        {"AnlagenEegSolar": plants(("EEG900000000001", KEY_A)), "EinheitenSolar": units()},
    )
    assert cli.main(["--data-dir", str(data_dir), "mastr", "--db", str(db)]) == 0
    assert "Empty MaStR tables, check the open-MaStR log: EinheitenSolar" in caplog.text


def test_cli_mastr_uses_the_open_mastr_location(
    data_dir: Path, db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(db))
    assert cli.main(["--data-dir", str(data_dir), "mastr", "--year", "2025"]) == 0
    assert output_paths(data_dir, 2025, "amprion").mastr_report.exists()


def test_cli_mastr_without_database_fails(
    data_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    args = ["--data-dir", str(data_dir), "mastr", "--db", str(tmp_path / "missing.db")]
    assert cli.main(args) == 1
    assert "open-MaStR database not found" in caplog.text
    assert not output_paths(data_dir, 2025, "amprion").mastr.exists()


def test_cli_mastr_without_matching_clean_files_fails(
    data_dir: Path, db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    args = ["--data-dir", str(data_dir), "mastr", "--db", str(db), "--uenb", "tennet"]
    assert cli.main(args) == 1
    assert "run 'ingest' first" in caplog.text


def test_check_match_rejects_a_changed_clean_file(data_dir: Path, db: Path) -> None:
    link_file(data_dir, 2025, "amprion", load_register(db))
    paths = output_paths(data_dir, 2025, "amprion")
    assert check_match(data_dir, 2025, "amprion") == paths.mastr

    rows = pl.read_parquet(paths.clean).with_columns(eeg_mastr_nr=pl.lit("EEG900000000099"))
    rows.write_parquet(paths.clean)

    with pytest.raises(MastrError, match="was made from another version of the clean file"):
        check_match(data_dir, 2025, "amprion")


@pytest.fixture
def bridge_db(tmp_path: Path) -> Path:
    """Two EEG plants without Anlagenschluessel: rows without a number match only by the bridge."""
    return make_mastr_db(
        tmp_path / "bridge.db",
        {"AnlagenEegSolar": plants(("EEG900000000001", None), ("EEG900000000002", None))},
    )


def match_all(data_dir: Path, db: Path) -> None:
    """Build the bridge and match every clean file with it, as `mastr` does."""
    register = load_register(db)
    bridge = load_bridge(data_dir, register)
    for jahr, uenb in clean_files(data_dir):
        link_file(data_dir, jahr, uenb, register, bridge)


def matched(data_dir: Path, jahr: int) -> tuple[str, str | None]:
    """`treffer` and `eeg_mastr_nr_abgeglichen` of the single row of Amprion in `jahr`."""
    linked = pl.read_parquet(output_paths(data_dir, jahr, "amprion").mastr)
    return linked.select("treffer", "eeg_mastr_nr_abgeglichen").row(0)


@pytest.fixture
def bridged_dir(tmp_path: Path, bridge_db: Path) -> Path:
    """2019 without a MaStR number, matched by the bridge from the same key in 2025."""
    data_dir = tmp_path / "data"
    for year, number in [(2019, ""), (2025, "EEG900000000001")]:
        ingest(archive(data_dir, {"a.csv": csv(HEADER, row(number))}, year=year), data_dir)
    match_all(data_dir, bridge_db)
    assert matched(data_dir, 2019) == ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000001")
    return data_dir


STALE_BRIDGE = (
    "the bridge of the match does not fit the current clean files; run 'mastr' for all years again"
)


def test_check_match_rejects_a_bridge_match_after_another_clean_file_changed(
    bridged_dir: Path, bridge_db: Path
) -> None:
    path = output_paths(bridged_dir, 2025, "amprion").clean
    rows = pl.read_parquet(path).with_columns(eeg_mastr_nr=pl.lit("EEG900000000002"))
    rows.write_parquet(path)

    # The clean file of 2019 is unchanged, but its match is stale.
    with pytest.raises(MastrError, match=STALE_BRIDGE):
        check_match(bridged_dir, 2019, "amprion")
    match_all(bridged_dir, bridge_db)
    assert matched(bridged_dir, 2019) == ("ANLAGENSCHLUESSEL_BRIDGE", "EEG900000000002")
    check_match(bridged_dir, 2019, "amprion")


def test_check_match_rejects_a_bridge_match_after_a_clean_file_was_added(
    bridged_dir: Path, bridge_db: Path
) -> None:
    # Another TSO and year name another plant for the same Anlagenschluessel.
    members = {"a.csv": csv(HEADER, row("EEG900000000002", uenb="tennet"))}
    ingest(archive(bridged_dir, members, "tennet", 2024), bridged_dir)

    with pytest.raises(MastrError, match=STALE_BRIDGE):
        check_match(bridged_dir, 2019, "amprion")
    match_all(bridged_dir, bridge_db)
    assert matched(bridged_dir, 2019) == ("NOT_FOUND", None)


def test_check_match_rejects_a_bridge_match_after_a_clean_file_was_removed(
    bridged_dir: Path, bridge_db: Path
) -> None:
    output_paths(bridged_dir, 2025, "amprion").clean.unlink()

    with pytest.raises(MastrError, match=STALE_BRIDGE):
        check_match(bridged_dir, 2019, "amprion")
    match_all(bridged_dir, bridge_db)
    assert matched(bridged_dir, 2019) == ("NOT_FOUND", None)


def test_check_match_rejects_a_bridge_match_if_a_clean_file_disappears_meanwhile(
    bridged_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = clean_files(bridged_dir)
    output_paths(bridged_dir, 2025, "amprion").clean.unlink()
    # As if the file was removed after `clean_files` listed it.
    monkeypatch.setattr(mastr, "clean_files", lambda data_dir: listed)

    with pytest.raises(MastrError, match=STALE_BRIDGE):
        check_match(bridged_dir, 2019, "amprion")


def test_check_match_rejects_a_bridge_match_of_an_older_version(bridged_dir: Path) -> None:
    path = output_paths(bridged_dir, 2019, "amprion").mastr_report
    report = json.loads(path.read_text(encoding="utf-8"))
    del report["bridge"]["clean_files"]
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(MastrError, match=STALE_BRIDGE):
        check_match(bridged_dir, 2019, "amprion")


def test_check_match_accepts_unchanged_clean_files_and_hashes_each_once(
    bridged_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hashed: list[Path] = []

    def counted(path: Path) -> str:
        hashed.append(path)
        return sha256_of(path)

    monkeypatch.setattr(mastr, "sha256_of", counted)
    hashes: dict[Path, str] = {}
    for jahr in (2019, 2025):
        paths = output_paths(bridged_dir, jahr, "amprion")
        assert check_match(bridged_dir, jahr, "amprion", hashes=hashes) == paths.mastr
    assert sorted(hashed) == [output_paths(bridged_dir, j, "amprion").clean for j in (2019, 2025)]


def test_check_match_without_the_bridge_ignores_other_clean_files(
    bridged_dir: Path, bridge_db: Path
) -> None:
    link_file(bridged_dir, 2019, "amprion", load_register(bridge_db))
    output_paths(bridged_dir, 2025, "amprion").clean.unlink()

    paths = output_paths(bridged_dir, 2019, "amprion")
    assert check_match(bridged_dir, 2019, "amprion") == paths.mastr
