"""Measured full-load hours of onshore wind turbines per district (Kreis): energy fed in as listed
in the EEG-Bewegungsdaten, over the gross capacity in the MaStR.

Method:
- Turbines on land in the MaStR that ran the whole year: commissioned before it, not shut down for
  good before its end.
- Energy of a turbine: the sum of the rows that `mastr` matched to exactly one plant.
- Turbines are grouped by their site (Lokation) in the MaStR, because some grid operators book the
  energy of several turbines on one of them. A site counts if all its turbines ran the whole year
  and have rows, each for all 12 months or for the year. Turbines without a site are a site of
  their own.
- Sites above 5,000 h are dropped: far above what onshore sites reach, so energy of other turbines
  is booked on them. Sites below 100 h are dropped too, as their energy is probably booked
  elsewhere. The script prints how much capacity this drops.
- Full-load hours of a district: sum of energy over sum of capacity of its turbines. For several
  years, add up `kwh` and `kw` over the years before dividing.

Needs `eeg-bewegungsdaten ingest` and `eeg-bewegungsdaten mastr` for the years, and the database of
open-MaStR. Writes one row per district and year to the given Parquet file.

    uv run python analysis/wind_full_load_hours.py 2020 2025 wind.parquet
"""

from __future__ import annotations

import sys

import polars as pl
from _common import matched_rows, query_mastr

MAX_HOURS = 5000
MIN_HOURS = 100
# Districts merged into others, still the key of a few turbines in the MaStR
MERGED = {"03152": "03159", "16056": "16063"}
STATUS = ["used", "part of year", "no rows", "some without rows", "months missing"]


def energy_per_turbine(jahr: int) -> pl.DataFrame:
    return (
        matched_rows(jahr, "wind")
        .group_by(pl.col("eeg_mastr_nr_abgeglichen").alias("eeg_nr"))
        .agg(
            kwh=pl.col("strommenge").sum(),
            months=pl.col("monat").filter(pl.col("monat") > 0).n_unique(),
            annual=(pl.col("monat") == 0).any(),
        )
        .collect()
    )


def onshore_turbines() -> pl.DataFrame:
    rows = query_mastr(
        """select EegMastrNummer, LokationMastrNummer, cast(Bruttoleistung as real),
                  substr(Inbetriebnahmedatum, 1, 10), substr(DatumEndgueltigeStilllegung, 1, 10),
                  Gemeindeschluessel, Landkreis, Bundesland
           from EinheitenWind where WindAnLandOderAufSee = 'Windenergie an Land'"""
    )
    names = ["eeg_nr", "site", "kw", "ibn", "still", "kreis", "landkreis", "bundesland"]
    schema = {name: pl.Float64 if name == "kw" else pl.String for name in names}
    key = pl.col("kreis")
    return pl.DataFrame(rows, schema=schema, orient="row").with_columns(
        pl.col("ibn", "still").str.to_date(strict=False),
        pl.col("site").fill_null(pl.col("eeg_nr")),
        # A few keys lost their leading zero, some of them are only the district: "5334"
        kreis=pl.when(key.str.len_chars() <= 5)
        .then(key.str.zfill(5))
        .otherwise(key.str.zfill(8).str.slice(0, 5))
        .replace(MERGED),
    )


def turbines_of_year(turbines: pl.DataFrame, jahr: int) -> pl.DataFrame:
    """Turbines of the sites that pass the checks, with their energy, and a status per site."""
    start, end = pl.date(jahr, 1, 1), pl.date(jahr, 12, 31)
    ran = (pl.col("ibn") <= end) & (pl.col("still").is_null() | (pl.col("still") >= start))
    whole_year = (pl.col("ibn") < start) & (pl.col("still").is_null() | (pl.col("still") > end))
    complete = (pl.col("months") == 12) | pl.col("annual")
    rows = turbines.filter(ran).join(energy_per_turbine(jahr), on="eeg_nr", how="left")
    hours = pl.col("kwh").sum().over("site") / pl.col("kw").sum().over("site")
    status = (
        pl.when(~(whole_year & (pl.col("kw") > 0)).all().over("site"))
        .then(pl.lit("part of year"))
        .when(pl.col("kwh").is_null().all().over("site"))
        .then(pl.lit("no rows"))
        .when(pl.col("kwh").is_null().any().over("site"))
        .then(pl.lit("some without rows"))
        .when(~(complete | pl.col("kwh").is_null()).all().over("site"))
        .then(pl.lit("months missing"))
        .when(hours > MAX_HOURS)
        .then(pl.lit(f"above {MAX_HOURS} h"))
        .when(hours < MIN_HOURS)
        .then(pl.lit(f"below {MIN_HOURS} h"))
        .otherwise(pl.lit("used"))
    )
    return rows.with_columns(jahr=pl.lit(jahr, pl.Int16), status=status)


def status_summary(years: pl.DataFrame) -> pl.DataFrame:
    """Capacity in GW per year and site status, with a column for every status in a fixed order.

    A status that no site of a year has shows 0.0. Without this, a status missing in all the years
    is also missing as a column: before 2021 the data has only annual values, so no site is
    "months missing", and selecting that column failed.
    """
    columns = [*STATUS, f"above {MAX_HOURS} h", f"below {MIN_HOURS} h"]
    summary = (
        years.group_by("jahr", "status")
        .agg(gw=(pl.col("kw").sum() / 1e6).round(2))
        .pivot("status", index="jahr", values="gw")
    )
    missing = [pl.lit(0.0).alias(column) for column in columns if column not in summary.columns]
    return summary.with_columns(missing).select("jahr", pl.col(columns).fill_null(0.0)).sort("jahr")


def per_district(turbines: pl.DataFrame) -> pl.DataFrame:
    return (
        turbines.filter(pl.col("status") == "used")
        .group_by("jahr", "kreis")
        .agg(
            pl.col("landkreis", "bundesland").mode().first(),
            turbines=pl.len(),
            kw=pl.col("kw").sum(),
            kwh=pl.col("kwh").sum(),
        )
        .with_columns(hours=pl.col("kwh") / pl.col("kw"))
        .sort("jahr", "kreis")
    )


if __name__ == "__main__":
    first, last, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    pl.Config.set_tbl_rows(30)
    pl.Config.set_tbl_hide_dataframe_shape(True)
    turbines = onshore_turbines()
    years = pl.concat([turbines_of_year(turbines, jahr) for jahr in range(first, last + 1)])
    print(status_summary(years))
    districts = per_district(years)
    districts.write_parquet(out)
    used = years.filter(pl.col("status") == "used")
    print(
        used.group_by("jahr")
        .agg(
            districts=pl.col("kreis").n_unique(),
            gw=(pl.col("kw").sum() / 1e6).round(1),
            twh=(pl.col("kwh").sum() / 1e9).round(1),
            hours=(pl.col("kwh").sum() / pl.col("kw").sum()).round(0),
        )
        .sort("jahr")
    )
