"""Recompute the full-load hours of PV plants in the AGEE-Stat report on PV, column * (without
estimated self-consumption): Table 16 for 2023 and Table 17 for 2022.

Report: Umweltbundesamt, Climate Change 61/2025, "Fachbericht Photovoltaik" (Fraunhofer ISE),
https://www.umweltbundesamt.de/system/files/medien/11850/publikationen/61_2025_cc.pdf

Method as in the report: plants commissioned up to the end of the previous year, with their
energy of the year divided by their gross capacity (kWp). Energy is the sum of all rows of the
plant, including self-consumption, over the rows that `mastr` matched to exactly one plant
(`generation()`: without repeated energy and without the deduction rows of the self-consumption
paid under section 33(2) EEG 2009). Full-load hours of a class are the sum of energy over the sum
of capacity of the plants with rows in the year.

Needs `eeg-bewegungsdaten ingest` and `eeg-bewegungsdaten mastr` for the year, and the database
of open-MaStR.

    uv run python analysis/agee_pv_full_load_hours.py 2023
"""

from __future__ import annotations

import sys

import polars as pl
from _common import matched_rows, query_mastr

from eeg_bewegungsdaten import generation

# Column * of the report, in h/a. Table 17 gives one value for the classes where * and ** agree.
AGEE = {
    2023: {
        "Steckerfertig x <= 2": 432,
        "Gebäude x < 10": 701,
        "Gebäude 10 <= x < 20": 816,
        "Gebäude 20 <= x < 30": 858,
        "Gebäude 30 <= x < 100": 833,
        "Gebäude 100 <= x < 500": 786,
        "Gebäude 500 <= x <= 750": 766,
        "Gebäude 750 < x <= 1000": 799,
        "Gebäude x > 1000": 764,
        "Freifläche x <= 750": 910,
        "Freifläche 750 < x <= 1000": 931,
        "Freifläche x > 1000": 917,
        "Gesamt": 828,
    },
    2022: {
        "Gebäude x < 10": 814,
        "Gebäude 10 <= x < 20": 953,
        "Gebäude 20 <= x < 30": 966,
        "Gebäude 30 <= x < 100": 953,
        "Gebäude 100 <= x < 500": 905,
        "Gebäude 500 <= x <= 750": 879,
        "Gebäude 750 < x <= 1000": 909,
        "Gebäude x > 1000": 901,
        "Freifläche x <= 750": 1029,
        "Freifläche 750 < x <= 1000": 1042,
        "Freifläche x > 1000": 1035,
        "Gesamt": 946,
    },
}


def energy_per_plant(jahr: int) -> pl.DataFrame:
    return (
        matched_rows(jahr, "solar")
        .filter(generation())
        .group_by(pl.col("eeg_mastr_nr_abgeglichen").alias("eeg_nr"))
        .agg(kwh=pl.col("strommenge").sum())
        .collect()
    )


def solar_units() -> pl.DataFrame:
    rows = query_mastr(
        """select EegMastrNummer, cast(Bruttoleistung as real), ArtDerSolaranlage,
                  substr(Inbetriebnahmedatum, 1, 10), substr(DatumEndgueltigeStilllegung, 1, 10)
           from EinheitenSolar"""
    )
    schema = {"eeg_nr": pl.String, "kw": pl.Float64, "art": pl.String, "ibn": pl.String}
    return pl.DataFrame(rows, schema={**schema, "still": pl.String}, orient="row").with_columns(
        pl.col("ibn", "still").str.to_date(strict=False)
    )


def size_class() -> pl.Expr:
    kw = pl.col("kw")
    building = (
        pl.when(kw < 10)
        .then(pl.lit("Gebäude x < 10"))
        .when(kw < 20)
        .then(pl.lit("Gebäude 10 <= x < 20"))
        .when(kw < 30)
        .then(pl.lit("Gebäude 20 <= x < 30"))
        .when(kw < 100)
        .then(pl.lit("Gebäude 30 <= x < 100"))
        .when(kw < 500)
        .then(pl.lit("Gebäude 100 <= x < 500"))
        .when(kw <= 750)
        .then(pl.lit("Gebäude 500 <= x <= 750"))
        .when(kw <= 1000)
        .then(pl.lit("Gebäude 750 < x <= 1000"))
        .otherwise(pl.lit("Gebäude x > 1000"))
    )
    ground = (
        pl.when(kw <= 750)
        .then(pl.lit("Freifläche x <= 750"))
        .when(kw <= 1000)
        .then(pl.lit("Freifläche 750 < x <= 1000"))
        .otherwise(pl.lit("Freifläche x > 1000"))
    )
    # Report, Table 3: plug-in plants and plants up to 2 kW commissioned from 2019 on
    plug_in = pl.col("art").str.starts_with("Steckerfertige") | (
        (kw <= 2) & (pl.col("ibn").dt.year() >= 2019)
    )
    return (
        pl.when(plug_in)
        .then(pl.lit("Steckerfertig x <= 2"))
        .when(pl.col("art") == "Freiflächensolaranlage")
        .then(ground)
        .otherwise(building)
    )


def full_load_hours(jahr: int) -> pl.DataFrame:
    plants = (
        solar_units()
        .filter(
            (pl.col("ibn") < pl.date(jahr, 1, 1))
            & (pl.col("still").is_null() | (pl.col("still") > pl.date(jahr, 12, 31)))
            & (pl.col("kw") > 0)
        )
        .join(energy_per_plant(jahr), on="eeg_nr")
        .with_columns(klasse=size_class())
    )
    agg = [
        pl.len().alias("plants"),
        (pl.col("kw").sum() / 1000).round(0).alias("mw"),
        (pl.col("kwh").sum() / pl.col("kw").sum()).round(0).alias("ours"),
    ]
    total = plants.select(pl.lit("Gesamt").alias("klasse"), *agg)
    ours = pl.concat([plants.group_by("klasse").agg(agg), total])
    agee = pl.DataFrame({"klasse": list(AGEE[jahr]), "agee": list(AGEE[jahr].values())})
    return agee.join(ours, on="klasse", how="left").with_columns(
        diff_pct=((pl.col("ours") / pl.col("agee") - 1) * 100).round(1)
    )


if __name__ == "__main__":
    pl.Config.set_tbl_rows(20)
    pl.Config.set_tbl_hide_dataframe_shape(True)
    print(full_load_hours(int(sys.argv[1]) if len(sys.argv) > 1 else 2023))
