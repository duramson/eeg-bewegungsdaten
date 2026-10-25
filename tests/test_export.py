from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import polars as pl
import pytest
from oemetadata.v2.v20.schema import OEMETADATA_V20_SCHEMA

from eeg_bewegungsdaten import cli
from eeg_bewegungsdaten.columns import AS_PUBLISHED, COLUMNS
from eeg_bewegungsdaten.datacard import DATACARD, datacard
from eeg_bewegungsdaten.datapackage import DATAPACKAGE_JSON
from eeg_bewegungsdaten.export import (
    ANLAGE_ID_SCHEMA,
    DL_DE_BY,
    EXPORT_SCHEMA,
    ID_LIMIT,
    MASTR_COLUMNS,
    ExportError,
    _new_ids,
    export,
)
from eeg_bewegungsdaten.ingest import ingest
from eeg_bewegungsdaten.links import PAGE_URL, UENB
from eeg_bewegungsdaten.mastr import (
    MASTR_SCHEMA,
    default_db_path,
    link_file,
    load_bridge,
    load_register,
)
from eeg_bewegungsdaten.paths import anlage_id_path, default_data_dir, output_paths
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA
from tests.builders import HEADER, archive, csv, make_mastr_db, plants, row

KEY_A = "E91000000000000000000000000000001"
KEY_B = "E91000000000000000000000000000002"
KEY_C = "E91000000000000000000000000000003"

# (year, TSO) -> lines of its CSV file
FILES = {
    (2024, "amprion"): [row("EEG900000000001", key=KEY_A), row("", key=KEY_B)],
    # 50Hertz gives no Anlagenschluessel from 2022 on.
    (2025, "50hertz"): [row("EEG900000000001", key="", uenb="50hertz")],
    (2025, "amprion"): [row("EEG900000000001", key=KEY_A), row("", key="")],
    # The same Anlagenschluessel at another TSO is another plant.
    (2025, "transnetbw"): [row("", key=KEY_A, uenb="transnetbw")],
}


def add_files(data_dir: Path, db: Path, files: dict[tuple[int, str], list[str]]) -> None:
    """Ingest and match hand-made files."""
    register = load_register(db)
    for (year, uenb), lines in files.items():
        members = {"Bewegungsdaten.csv": csv(HEADER, *lines)}
        ingest(archive(data_dir, members, uenb, year), data_dir)
        link_file(data_dir, year, uenb, register)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return make_mastr_db(
        tmp_path / "open-mastr.db", {"AnlagenEegSolar": plants(("EEG900000000001", KEY_A))}
    )


@pytest.fixture
def data_dir(tmp_path: Path, db: Path) -> Path:
    data_dir = tmp_path / "data"
    add_files(data_dir, db, FILES)
    return data_dir


def read(data_dir: Path, year: int) -> pl.DataFrame:
    return pl.read_parquet(data_dir / "export" / f"eeg-bewegungsdaten-{year}.parquet")


def test_export_columns_are_those_of_clean_without_anlagenschluessel() -> None:
    clean = {name: dtype for name, dtype in CLEAN_SCHEMA.items() if name != "anlagenschluessel"}
    mastr = {name: MASTR_SCHEMA[name] for name in MASTR_COLUMNS}
    assert dict(EXPORT_SCHEMA) == {**clean, **mastr, "anlage_id": pl.Int64()}


def test_export_writes_one_file_per_year_and_the_sources(data_dir: Path) -> None:
    export(data_dir)

    names = sorted(path.name for path in (data_dir / "export").iterdir())
    assert names == [
        "README.md",
        "datapackage.json",
        "eeg-bewegungsdaten-2024.parquet",
        "eeg-bewegungsdaten-2025.parquet",
        "sources.json",
        "sources.txt",
    ]
    rows = read(data_dir, 2025)
    assert rows.schema == EXPORT_SCHEMA
    assert rows.select("uenb", "zeile", *MASTR_COLUMNS).rows() == [
        ("50hertz", 2, "EEG_NR", "EEG900000000001", "solar"),
        ("amprion", 2, "EEG_NR", "EEG900000000001", "solar"),
        ("amprion", 3, "NO_ID", None, None),
        ("transnetbw", 2, "ANLAGENSCHLUESSEL", "EEG900000000001", "solar"),
    ]


def test_export_keeps_the_clean_rows(data_dir: Path) -> None:
    export(data_dir)

    clean = pl.concat(
        pl.read_parquet(output_paths(data_dir, 2025, uenb).clean)
        for uenb in ("50hertz", "amprion", "transnetbw")
    ).drop("anlagenschluessel")
    assert read(data_dir, 2025).select(clean.columns).equals(clean)


def test_export_holds_no_anlagenschluessel(data_dir: Path) -> None:
    export(data_dir)

    for path in (data_dir / "export").iterdir():
        if path.suffix == ".parquet":
            rows = pl.read_parquet(path)
            assert "anlagenschluessel" not in rows.columns
            for column in rows.select(pl.col(pl.String())).columns:
                for key in (KEY_A, KEY_B):
                    assert not rows[column].str.contains(key, literal=True).any(), (path, column)
        else:
            text = path.read_text(encoding="utf-8")
            assert KEY_A not in text
            assert KEY_B not in text


def test_anlage_id_mapping_stays_in_the_data_folder(data_dir: Path) -> None:
    export(data_dir)

    ids = pl.read_parquet(anlage_id_path(data_dir))
    assert ids.schema == ANLAGE_ID_SCHEMA
    assert ids.select("uenb", "anlagenschluessel").rows() == [
        ("amprion", KEY_A),
        ("amprion", KEY_B),
        ("transnetbw", KEY_A),
    ]
    assert ids["anlage_id"].n_unique() == 3
    assert ids["anlage_id"].is_between(1, ID_LIMIT - 1).all()
    assert not list((data_dir / "export").glob("anlage_id*"))


def test_anlage_id_is_one_per_tso_and_anlagenschluessel(data_dir: Path) -> None:
    export(data_dir)

    by_line = {
        (jahr, uenb, zeile): anlage_id
        for year in (2024, 2025)
        for jahr, uenb, zeile, anlage_id in read(data_dir, year)
        .select("jahr", "uenb", "zeile", "anlage_id")
        .iter_rows()
    }
    mapping = pl.read_parquet(anlage_id_path(data_dir))
    id_of = {(u, k): i for u, k, i in mapping.iter_rows()}
    assert by_line == {
        (2024, "amprion", 2): id_of[("amprion", KEY_A)],
        (2024, "amprion", 3): id_of[("amprion", KEY_B)],
        (2025, "50hertz", 2): None,
        (2025, "amprion", 2): id_of[("amprion", KEY_A)],
        (2025, "amprion", 3): None,
        (2025, "transnetbw", 2): id_of[("transnetbw", KEY_A)],
    }
    assert id_of[("transnetbw", KEY_A)] != id_of[("amprion", KEY_A)]


def test_anlage_id_stays_the_same_in_the_next_run(data_dir: Path) -> None:
    export(data_dir)
    first = read(data_dir, 2025)
    mapping = anlage_id_path(data_dir).read_bytes()

    export(data_dir)

    assert read(data_dir, 2025).equals(first)
    assert anlage_id_path(data_dir).read_bytes() == mapping


def test_new_anlagenschluessel_get_new_ids_and_old_ones_keep_theirs(
    data_dir: Path, db: Path
) -> None:
    export(data_dir)
    before = pl.read_parquet(anlage_id_path(data_dir))

    lines = [row("", key=key, uenb="tennet") for key in (KEY_C, KEY_A)]
    add_files(data_dir, db, {(2023, "tennet"): lines})
    export(data_dir)

    after = pl.read_parquet(anlage_id_path(data_dir))
    assert after.head(before.height).equals(before)
    assert after.tail(2).select("uenb", "anlagenschluessel").rows() == [
        ("tennet", KEY_A),
        ("tennet", KEY_C),
    ]
    assert after["anlage_id"].n_unique() == after.height


def test_anlage_id_is_random_not_derived_from_the_key(data_dir: Path, tmp_path: Path) -> None:
    for name in ("a", "b"):
        export(data_dir, tmp_path / name, ids_path=tmp_path / f"{name}.parquet")

    a, b = (pl.read_parquet(tmp_path / f"{name}.parquet") for name in ("a", "b"))
    assert a.drop("anlage_id").equals(b.drop("anlage_id"))
    assert not (a["anlage_id"] == b["anlage_id"]).any()


def test_new_ids_are_distinct_and_skip_ids_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    # With ids from 1 to 4 only, most draws collide and must be drawn again.
    monkeypatch.setattr("eeg_bewegungsdaten.export.ID_LIMIT", 5)

    assert _new_ids(1, pl.Series([1, 2, 3], dtype=pl.Int64())).to_list() == [4]
    assert sorted(_new_ids(4, pl.Series([], dtype=pl.Int64()))) == [1, 2, 3, 4]


def test_sources_name_the_tsos_and_the_mastr_export(data_dir: Path) -> None:
    content = export(data_dir)

    out = data_dir / "export"
    assert json.loads((out / "sources.json").read_text(encoding="utf-8")) == content
    assert content["years"]["2025"] == {
        "file": "eeg-bewegungsdaten-2025.parquet",
        "rows": 4,
        "anlage_ids": 2,
        "rows_without_anlage_id": 2,
        "match_rate": 0.75,
    }
    notes = content["source_notes"]
    assert notes["eeg"].startswith(
        "Datenquelle: EEG-Bewegungsdaten der Übertragungsnetzbetreiber 50Hertz Transmission GmbH, "
        f"Amprion GmbH und TransnetBW GmbH, {PAGE_URL}; Daten verändert"
    )
    dash = chr(0x2013)  # en dash
    assert f"Datenlizenz Deutschland {dash} Namensnennung {dash} Version 2.0" == DL_DE_BY
    assert notes["mastr"] == (
        "Datenquelle: Marktstammdatenregister der Bundesnetzagentur, Gesamtdatenexport vom "
        "17.10.2026, https://www.marktstammdatenregister.de/MaStR/Datendownload, "
        f"{DL_DE_BY} (www.govdata.de/dl-de/by-2-0); Daten verändert (bereinigt, verknüpft)."
    )
    assert content["mastr_export_dates"] == ["2026-10-17"]

    reports = [
        json.loads(output_paths(data_dir, jahr, uenb).report.read_text(encoding="utf-8"))
        for jahr, uenb in FILES
    ]
    sha256 = [source["sha256"] for report in reports for source in report["sources"]]
    assert [source["sha256"] for source in content["files"]] == sha256
    assert content["files"][0]["url"].startswith("https://www.netztransparenz.de/")

    text = (out / "sources.txt").read_text(encoding="utf-8")
    assert notes["eeg"] in text
    assert notes["mastr"] in text
    assert all(digest in text for digest in sha256)


def read_datapackage(data_dir: Path) -> dict[str, Any]:
    text = (data_dir / "export" / DATAPACKAGE_JSON).read_text(encoding="utf-8")
    package: dict[str, Any] = json.loads(text)
    return package


def test_datapackage_is_valid_oemetadata_v2(data_dir: Path) -> None:
    export(data_dir)

    package = read_datapackage(data_dir)
    jsonschema.validate(package, OEMETADATA_V20_SCHEMA)
    assert package["metaMetadata"]["metadataVersion"] == "OEMetadata-2.0.4"


def test_datapackage_has_one_resource_per_year_with_the_dictionary(data_dir: Path) -> None:
    content = export(data_dir)

    resources = read_datapackage(data_dir)["resources"]
    assert [r["path"] for r in resources] == [
        "eeg-bewegungsdaten-2024.parquet",
        "eeg-bewegungsdaten-2025.parquet",
    ]
    fields = {field["name"]: field for field in resources[1]["schema"]["fields"]}
    assert list(fields) == list(EXPORT_SCHEMA)
    assert resources[1]["schema"]["primaryKey"] == ["jahr", "uenb", "quelldatei", "zeile"]
    assert fields["strommenge"] | {"description": ""} == {
        "name": "strommenge",
        "description": "",
        "type": "double precision",
        "nullable": True,
        "unit": "kWh",
    }
    assert fields["strommenge"]["description"] == COLUMNS["strommenge"].description
    assert fields["hinweise"]["type"] == "text[]"
    assert fields["jahr"]["nullable"] is False
    codes = [ref["value"] for ref in fields["treffer"]["valueReference"]]
    assert codes == list(COLUMNS["treffer"].codes)
    # Each year names its own source files, with SHA-256, and the MaStR export.
    files = [f for f in content["files"] if f["jahr"] == 2025]
    sources = resources[1]["sources"]
    assert [s["path"] for s in sources[:-1]] == [f["url"] for f in files]
    assert all(f["sha256"] in s["description"] for s, f in zip(sources, files, strict=False))
    assert sources[-1]["path"] == "https://www.marktstammdatenregister.de/MaStR/Datendownload"
    assert resources[1]["contributors"] == []


def test_datapackage_claims_no_license_for_the_eeg_data(data_dir: Path) -> None:
    content = export(data_dir)

    eeg, mastr = read_datapackage(data_dir)["resources"][0]["licenses"]
    assert eeg["name"] is None
    assert eeg["title"] == "EEG-Bewegungsdaten: no settled license"
    assert eeg["attribution"] == content["source_notes"]["eeg"]
    assert mastr["name"] == "DL-DE-BY-2.0"
    assert mastr["attribution"] == content["source_notes"]["mastr"]


def test_cli_export_names_the_contributor(data_dir: Path) -> None:
    orcid = "https://orcid.org/0000-0000-0000-0000"
    args = ["--data-dir", str(data_dir), "export", "--contributor", "A. Person"]
    assert cli.main([*args, "--contributor-url", orcid]) == 0

    for resource in read_datapackage(data_dir)["resources"]:
        (contributor,) = resource["contributors"]
        assert (contributor["title"], contributor["path"]) == ("A. Person", orcid)
        assert contributor["roles"] == ["DataCurator"]


def read_datacard(data_dir: Path) -> str:
    return (data_dir / "export" / DATACARD).read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n")[1].split("\n## ")[0]


def test_datacard_is_written_from_sources_json(data_dir: Path) -> None:
    export(data_dir)

    text = read_datacard(data_dir)
    content = json.loads((data_dir / "export" / "sources.json").read_text(encoding="utf-8"))
    assert text == datacard(content, EXPORT_SCHEMA)
    assert text.startswith("# EEG-Bewegungsdaten 2024 to 2025, cleaned and matched")
    assert (
        "- `eeg-bewegungsdaten-2025.parquet`: 2025, 4 rows of 50Hertz Transmission GmbH, Amprion "
        "GmbH and TransnetBW GmbH; 2 plants (`anlage_id`), 2 rows without one; 75.0% of the rows "
        "matched with the MaStR." in text
    )
    # The codes the filter of grid feed-in leaves out, the MaStR export and the pseudonym
    assert (
        "`REPEATED_ENERGY`, `SELF_CONSUMPTION`, `PAID_SELF_CONSUMPTION` or "
        "`SELF_CONSUMPTION_DEDUCTION`. The sum of the other rows matches" in text
    )
    assert "(MaStR), export of 2026-10-17." in text
    assert "- `anlage_id` replaces the plant key" in text
    # Markdown tables need pipes, and CKAN does not render them.
    assert "|" not in text
    # The card is read outside the repository.
    assert all(link.startswith("https://") for link in re.findall(r"\]\(([^)]*)\)", text))


def test_datacard_names_every_column_with_type_unit_and_meaning(data_dir: Path) -> None:
    export(data_dir)

    columns = section(read_datacard(data_dir), "Columns")
    items = {line.split("`")[1]: line for line in columns.splitlines() if line.startswith("- `")}
    assert list(items) == list(EXPORT_SCHEMA)
    for name, dtype in EXPORT_SCHEMA.items():
        column = COLUMNS[name]
        unit = f", {column.unit}" if column.unit else ""
        meaning = column.description.removesuffix(AS_PUBLISHED)
        assert items[name] == f"- `{name}`: {dtype}{unit}. {meaning}"
        for code, text in column.codes.items():
            assert f"\n    - `{code}`: {text}\n" in columns
    assert items["strommenge"].startswith("- `strommenge`: Float64, kWh. Energy of the row.")
    assert items["eeg_zahlung"].startswith("- `eeg_zahlung`: Float64, EUR. ")
    # `text` is not part of the export.
    assert "In `text`" not in columns


def test_datacard_claims_no_license_for_the_eeg_data(data_dir: Path) -> None:
    content = export(data_dir)

    text = read_datacard(data_dir)
    eeg, mastr = section(text, "Licenses").strip().splitlines()
    assert eeg.startswith(
        "- [EEG-Bewegungsdaten: no settled license]"
        "(https://github.com/duramson/eeg-bewegungsdaten#data-license)."
    )
    assert "Datenlizenz" not in eeg
    assert mastr.startswith(f"- [{DL_DE_BY}](https://www.govdata.de/dl-de/by-2-0) (DL-DE-BY-2.0)")
    mastr_columns = "`treffer`, `eeg_mastr_nr_abgeglichen` and `energietraeger`"
    assert f"for the MaStR data in {mastr_columns}." in mastr
    assert text.count("DL-DE-BY-2.0") == 1
    assert not any(name in text for name in ("CC0", "CC-BY", "CC BY", "ODbL", "dl-de/zero"))
    notes = section(text, "Source notes")
    assert f"- {content['source_notes']['eeg']}\n" in notes
    assert f"- {content['source_notes']['mastr']}\n" in notes


def test_sources_name_every_mastr_export_date(data_dir: Path) -> None:
    report_path = output_paths(data_dir, 2025, "transnetbw").mastr_report
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["mastr"]["tables"]["AnlagenEegSolar"]["datum_download"] = ["20261018"]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    content = export(data_dir)

    assert content["mastr_export_dates"] == ["2026-10-17", "2026-10-18"]
    assert "Gesamtdatenexporte vom 17.10.2026 und 18.10.2026," in content["source_notes"]["mastr"]


def test_export_warns_about_years_without_all_tsos(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    export(data_dir)
    assert "2024: no clean files of 50hertz, tennet, transnetbw" in caplog.text
    assert "2025: no clean files of tennet" in caplog.text


def test_export_of_one_year_writes_only_that_year(data_dir: Path, tmp_path: Path) -> None:
    content = export(data_dir, tmp_path / "share", [2025])

    assert list(content["years"]) == ["2025"]
    assert {source["jahr"] for source in content["files"]} == {2025}
    assert sorted(path.name for path in (tmp_path / "share").glob("*.parquet")) == [
        "eeg-bewegungsdaten-2025.parquet"
    ]


def test_export_refuses_a_folder_with_other_years(data_dir: Path) -> None:
    export(data_dir)

    with pytest.raises(ExportError, match=r"other years \(eeg-bewegungsdaten-2024\.parquet\)"):
        export(data_dir, years=[2025])


def test_export_without_mastr_file_fails(data_dir: Path) -> None:
    output_paths(data_dir, 2025, "transnetbw").mastr.unlink()

    with pytest.raises(ExportError, match="run 'mastr' first"):
        export(data_dir)
    assert not (data_dir / "export").exists()
    assert not anlage_id_path(data_dir).exists()


def test_export_with_a_mastr_file_that_does_not_fit_fails(data_dir: Path) -> None:
    path = output_paths(data_dir, 2025, "amprion").mastr
    pl.read_parquet(path).head(1).write_parquet(path)

    with pytest.raises(ExportError, match="2025 amprion: the mastr file does not fit"):
        export(data_dir)


def test_export_rejects_a_mapping_with_other_columns(data_dir: Path) -> None:
    pl.DataFrame({"anlagenschluessel": [KEY_A], "id": [1]}).write_parquet(anlage_id_path(data_dir))

    with pytest.raises(ExportError, match="expected the columns"):
        export(data_dir)


def test_export_refuses_a_folder_that_holds_the_mapping(data_dir: Path) -> None:
    with pytest.raises(ExportError, match=r"holds anlage_id\.parquet, which must not be shared"):
        export(data_dir, data_dir)
    assert not list(data_dir.glob("eeg-bewegungsdaten-*"))


def test_export_rejects_changed_clean_rows_at_the_same_source_positions(
    data_dir: Path, db: Path
) -> None:
    export(data_dir)
    before = {path: path.read_bytes() for path in (data_dir / "export").iterdir()}
    mapping = anlage_id_path(data_dir).read_bytes()
    clean_path = output_paths(data_dir, 2025, "amprion").clean
    old_keys = pl.read_parquet(clean_path).select("jahr", "uenb", "quelldatei", "zeile")
    updated = archive(
        data_dir,
        {"Bewegungsdaten.csv": csv(HEADER, row("EEG900000000002", key=KEY_C), row("", key=""))},
        file_id="aaaaaaaa-1111-2222-3333-444444444444",
    )
    ingest(updated, data_dir)
    assert pl.read_parquet(clean_path).select(old_keys.columns).equals(old_keys)

    with pytest.raises(ExportError, match="run 'mastr' again"):
        export(data_dir)
    assert {path: path.read_bytes() for path in (data_dir / "export").iterdir()} == before
    assert anlage_id_path(data_dir).read_bytes() == mapping

    link_file(data_dir, 2025, "amprion", load_register(db))
    export(data_dir)
    updated_rows = read(data_dir, 2025).filter(pl.col("uenb") == "amprion")
    assert updated_rows["eeg_mastr_nr"][0] == "EEG900000000002"
    assert updated_rows["eeg_mastr_nr_abgeglichen"][0] is None


@pytest.mark.parametrize(
    ("layer", "old", "new", "command"),
    [
        ("clean", "qa_flags", "hinweise", "ingest"),
        ("mastr", "mastr_eeg_nr", "eeg_mastr_nr_abgeglichen", "mastr"),
    ],
)
def test_export_refuses_files_with_the_columns_of_an_older_version(
    data_dir: Path, layer: str, old: str, new: str, command: str
) -> None:
    path = getattr(output_paths(data_dir, 2025, "amprion"), layer)
    pl.read_parquet(path).rename({new: old}).write_parquet(path)

    with pytest.raises(ExportError, match=rf"has no column {new}.*run '{command}' again"):
        export(data_dir)
    assert not (data_dir / "export").exists()


def test_export_refuses_a_match_report_without_clean_sha256(data_dir: Path) -> None:
    path = output_paths(data_dir, 2025, "amprion").mastr_report
    report = json.loads(path.read_text(encoding="utf-8"))
    report.pop("clean_sha256")
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ExportError, match="run 'mastr' again"):
        export(data_dir)
    assert not (data_dir / "export").exists()
    assert not anlage_id_path(data_dir).exists()


def test_export_reports_progress_and_each_year(data_dir: Path) -> None:
    calls: list[tuple[int, int | None]] = []
    years: list[int] = []

    content = export(
        data_dir,
        progress=lambda done, total: calls.append((done, total)),
        on_year=lambda jahr, summary: years.append(jahr),
    )

    assert calls[0] == (0, 4)
    assert calls[-1] == (4, 4)
    assert years == [2024, 2025]
    assert [str(year) for year in years] == list(content["years"])


def test_cli_export_prints_rows_per_year(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--data-dir", str(data_dir), "export"]) == 0

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("Exporting 2 years to ")
    first, second = lines[1:3]
    assert first.split()[:9] == ["✓", "2024", "1", "TSO", "2", "rows", "2", "plants", "0"]
    assert "50.0% matched" in first
    assert second.split()[:9] == ["✓", "2025", "3", "TSOs", "4", "rows", "2", "plants", "2"]
    assert "75.0% matched" in second
    assert second.endswith("eeg-bewegungsdaten-2025.parquet")
    assert "Rows     6 rows · 2 without plant key" in lines


def test_cli_export_to_another_folder(data_dir: Path, tmp_path: Path) -> None:
    args = ["--data-dir", str(data_dir), "export", "--year", "2025", "--out", str(tmp_path / "x")]
    assert cli.main(args) == 0
    assert (tmp_path / "x" / "eeg-bewegungsdaten-2025.parquet").exists()
    assert not (data_dir / "export").exists()


def test_cli_export_without_clean_files_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "export"]) == 1
    assert "run 'ingest' first" in caplog.text


@pytest.mark.realdata
def test_real_export_of_2025(tmp_path: Path) -> None:
    source = default_data_dir()
    db = default_db_path()
    if not db.is_file() or not all(
        output_paths(source, 2025, uenb).clean.is_file()
        and output_paths(source, 2025, uenb).report.is_file()
        for uenb in UENB
    ):
        pytest.skip("clean files of 2025 or the open-MaStR database missing")

    # Match again in a temporary folder: the matches in the data folder may be from another
    # version and must neither be trusted nor overwritten by the test.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "clean").symlink_to(source / "clean", target_is_directory=True)
    (data_dir / "reports/2025").mkdir(parents=True)
    register = load_register(db)
    bridge = load_bridge(data_dir, register)
    for uenb in UENB:
        shutil.copyfile(
            output_paths(source, 2025, uenb).report, output_paths(data_dir, 2025, uenb).report
        )
        link_file(data_dir, 2025, uenb, register, bridge)
    content = export(data_dir, tmp_path / "export", [2025], ids_path=tmp_path / "ids.parquet")

    rows = pl.scan_parquet(tmp_path / "export" / "eeg-bewegungsdaten-2025.parquet")
    assert rows.collect_schema() == EXPORT_SCHEMA
    clean = pl.scan_parquet([output_paths(data_dir, 2025, uenb).clean for uenb in UENB])
    keys = clean.select(
        rows=pl.len(),
        plants=pl.struct("uenb", "anlagenschluessel")
        .filter(pl.col("anlagenschluessel").is_not_null())
        .n_unique(),
        without_key=pl.col("anlagenschluessel").null_count(),
    ).collect()
    year = content["years"]["2025"]
    assert (year["rows"], year["anlage_ids"], year["rows_without_anlage_id"]) == keys.row(0)
    assert rows.filter(pl.col("treffer").is_null()).select(pl.len()).collect().item() == 0
