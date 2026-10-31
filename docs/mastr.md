# Matching with the Marktstammdatenregister

`mastr` finds the EEG plant in the Marktstammdatenregister (MaStR) for every clean row. It
writes one row per clean row with the EEG number from the MaStR and how it was found.

`mastr` adds only what holds for the payment year: the EEG number and the energy source. It
adds no capacity, location, operator or status. The MaStR export shows these on the day of the
export, not in the payment year: a plant extended in 2025 would show its new capacity in the
rows of 2024 too. Join them yourself where you need them, see
[Add MaStR data](#add-mastr-data).

## Get the MaStR data with open-MaStR

The tool reads the SQLite database that [open-MaStR](https://github.com/OpenEnergyPlatform/open-MaStR)
builds from the MaStR bulk export. open-MaStR is not a dependency of this package, as it needs
Python 3.12 or newer. Run it once in its own environment:

```bash
uvx --from "git+https://github.com/OpenEnergyPlatform/open-MaStR@develop" python -c "from open_mastr import Mastr; Mastr().download(data=['solar', 'wind', 'biomass', 'hydro', 'gsgk', 'storage', 'deleted_units'])"
```

- Use the `develop` branch until a release after 1.0.0 is out. open-MaStR 1.0.0 cannot import
  `EinheitenSolar` from the export of 17 October 2026: every file fails with
  `invalid literal for int()`, and the run still ends with "Bulk download was successful".
  The fixes ([#819](https://github.com/OpenEnergyPlatform/open-MaStR/pull/819),
  [#820](https://github.com/OpenEnergyPlatform/open-MaStR/pull/820)) are on `develop`.
- Tested on 18 October 2026: open-MaStR 1.0.0 for wind, biomass, hydro, GSGK and storage, and
  `develop` at commit `85f91c7` for solar.
- The download is 2.4 GB of the 3.2 GB bulk export. The database takes 7.2 GB in
  `~/.open-MaStR/data/sqlite/open-mastr.db`.
- open-MaStR can leave tables empty or incomplete without an error. `mastr` warns about empty
  tables; the report lists the rows of every table.
- The MaStR shows the current state. Plants that were deleted from the register since the
  payment year cannot be found. `deleted_units` lists the numbers of deleted and deactivated
  units (6.7 MB download), but not their EEG numbers. Load it together with the other tables,
  so that all come from the same export.

## Run the match

```bash
uv run eeg-bewegungsdaten mastr
```

- `mastr` matches all clean files, or those chosen with `--year` and `--uenb`. For the bridge
  (step 4 below) it reads all clean files in any case, so run `ingest` for all years first.
  If `ingest` later changes, adds or removes a clean file, run `mastr` for all years again.
- It looks for the database where open-MaStR puts it: `$SQLITE_DATABASE_PATH`, else
  `data/sqlite/open-mastr.db` in `$MASTR_PROJECT_HOME_DIR` or `~/.open-MaStR`. Give another
  file with `--db`.
- The database is opened read-only. Databases written with `english=True` are not supported.
- It prints the share of rows and of EEG payments that were matched, the rows that only the
  bridge matched and the rows not found, per year and TSO. The summary adds them up and lists
  the years in which the bridge matched at least 0.5 % of the rows.
- For 2025 alone it takes about 2 minutes and 4.2 GB of memory, for 2016 to 2025 about 4
  minutes.

## How rows are matched

The tool uses the EEG plants (`AnlagenEeg…`) and units (`Einheiten…`) of solar, wind, biomass,
hydro, GSGK (geothermal, mine gas, pressure release) and storage. It tries these steps in
order and stops at the first match:

1. By number: an `EEG…` number is looked up as `EegMastrNummer` of the EEG plants. A unit
   number `SEE…` is looked up as `EinheitMastrNummer` of the units; the unit's
   `EegMastrNummer` is used. Rows with several numbers are looked up number by number.
   Some grid operators give the EEG number of one plant in the rows of several plants, for
   example of all turbines of a wind farm, each with the turbine's own Anlagenschluessel. If the
   rows of an `EEG…` number in one file hold several Anlagenschluessel and one of them belongs
   to exactly one other EEG plant, each of these rows is matched by its Anlagenschluessel:
   rows of the plant itself keep `EEG_NR`, rows of another plant get `ANLAGENSCHLUESSEL`, and
   rows whose Anlagenschluessel leads to no single plant get `EEG_NR_CONFLICT`.
2. By Anlagenschluessel: if no number matched, because it is missing, invalid or not in
   the MaStR, the `anlagenschluessel` is looked up as `AnlagenschluesselEeg` of the EEG plants.
3. By KWK number: some rows hold the number of a CHP plant (`KWK…`) instead of an EEG
   number. If the Anlagenschluessel leads to no single plant, the KWK number is looked up as
   `KwkMastrNummer` of the biomass and GSGK units; the units' `EegMastrNummer` is used. This
   step comes after the Anlagenschluessel because a CHP plant can span several EEG plants, and
   some rows name a neighbouring CHP plant.
4. By the Anlagenschluessel of other years (the bridge): if the Anlagenschluessel is not in the
   MaStR or belongs to several plants, the tool looks at the rows of all years and TSOs with
   this Anlagenschluessel that were matched by number (`EEG_NR`, `UNIT_NR`, or `MULTIPLE` with
   one plant). If they all name the same EEG plant, the row gets this plant. See
   [The bridge](#the-bridge).

Rows that are still not matched get `DELETED` if one of their unit numbers is in
`GeloeschteUndDeaktivierteEinheiten`. The MaStR export lists only active units in `Einheiten…`.
The list of deleted units has no EEG numbers, so these rows stay without a plant. Without that
table in the database, they are `NOT_FOUND`. Step 4 does not change rows with `DELETED` or
`EEG_NR_CONFLICT`.

| `treffer` | Meaning |
| --- | --- |
| `EEG_NR` | `EEG…` number found |
| `UNIT_NR` | `SEE…` number found as a unit with an EEG number |
| `MULTIPLE` | Several numbers; at least one found. All EEG numbers found are in `eeg_mastr_nrs_abgeglichen` |
| `ANLAGENSCHLUESSEL` | No number found, or the `EEG…` number is given for other plants too (see step 1); the Anlagenschluessel belongs to exactly one EEG plant |
| `KWK_NR` | No number or Anlagenschluessel found; the KWK number belongs to exactly one EEG plant |
| `ANLAGENSCHLUESSEL_BRIDGE` | No number, Anlagenschluessel or KWK number leads to one plant; the rows of all years with this Anlagenschluessel that were matched by number name exactly one EEG plant (step 4) |
| `EEG_NR_CONFLICT` | The `EEG…` number is given for other plants too (see step 1); the Anlagenschluessel belongs to no single EEG plant |
| `AMBIGUOUS` | No number found; the Anlagenschluessel or the KWK number belongs to several EEG plants |
| `DELETED` | No number found; the unit number belongs to a deleted or deactivated unit |
| `NOT_FOUND` | Neither the number nor the Anlagenschluessel is in the MaStR |
| `NO_ID` | Neither a valid number nor an Anlagenschluessel |

The first six count as matched. The match rates of 2016 to 2025 are in
[Matching with the MaStR](findings.md#matching-with-the-mastr).

## The bridge

The files of 2016 to 2019 have no MaStR numbers, so their rows can only be matched by
Anlagenschluessel. For many plants the MaStR holds no `AnlagenschluesselEeg`, or another one.
Without the bridge, 6 to 11 % of the rows of 2016 to 2019 stay without a plant, and the share
differs a lot between grid operators. From 2020 on, the rows hold both the Anlagenschluessel and
the EEG number. The bridge uses these pairs for the rows that the MaStR alone cannot match.

An Anlagenschluessel counts only if the rows of all years and TSOs name exactly one EEG plant.
The tool does not compare the grid operator, as some operators changed their BDEW number or took
over grid areas of others. How many rows the bridge matches, and how it was checked, is in
[The bridge](findings.md#the-bridge) in the findings.

## Columns of `mastr`

| Column | Type | Content |
| --- | --- | --- |
| `jahr`, `uenb`, `quelldatei`, `zeile` | | Same as in `clean`; together they identify the row |
| `treffer` | String | How the row was matched, see above |
| `eeg_mastr_nr_abgeglichen` | String | `EegMastrNummer` of the plant found in the MaStR; empty unless exactly one plant was found. `eeg_mastr_nr` of `clean` is the number as published |
| `eeg_mastr_nrs_abgeglichen` | List[String] | All EEG numbers found, only for `MULTIPLE` |
| `energietraeger` | String | `solar`, `wind`, `biomass`, `hydro`, `gsgk` or `storage`, from the MaStR table |

`reports/<year>/<tso>.mastr.json` holds rows and EEG payments per `treffer`, the shares matched,
the database file and tables used, with open-MaStR's export date (`DatumDownload`), and under
`bridge` the years and the number of Anlagenschluessel of the bridge, and the clean files it
was built from with their SHA-256.

```python
import polars as pl

import eeg_bewegungsdaten

key = ["jahr", "uenb", "quelldatei", "zeile"]
payments = eeg_bewegungsdaten.scan().join(
    pl.scan_parquet("~/.eeg-bewegungsdaten/mastr/*/*.parquet"), on=key, how="left"
)
```

## Add MaStR data

Capacity, commissioning date, location and the other MaStR data come from the open-MaStR
database by `eeg_mastr_nr_abgeglichen`. For example, the capacity and the EEG commissioning
date of PV plants:

```python
import sqlite3
from contextlib import closing
from pathlib import Path

import polars as pl

import eeg_bewegungsdaten

db = Path.home() / ".open-MaStR/data/sqlite/open-mastr.db"
with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
    solar = pl.read_database(
        "SELECT EegMastrNummer, InstallierteLeistung, EegInbetriebnahmedatum FROM AnlagenEegSolar",
        con,
    )

key = ["jahr", "uenb", "quelldatei", "zeile"]
payments = (
    eeg_bewegungsdaten.scan()
    .join(pl.scan_parquet("~/.eeg-bewegungsdaten/mastr/*/*.parquet"), on=key, how="left")
    .join(solar.lazy(), left_on="eeg_mastr_nr_abgeglichen", right_on="EegMastrNummer", how="left")
)
```

- The values are those of the day of the MaStR export (`DatumDownload`, also in the `mastr`
  reports), for every year. Some changes have a date in the MaStR: `EegInbetriebnahmedatum`,
  `Inbetriebnahmedatum` and `DatumEndgueltigeStilllegung` of the units,
  `DatumDesBetreiberwechsels`, and for biogas `BiogasDatumLeistungserhoehung`.
- The wind script in [analysis/](../analysis/README.md) uses these dates to leave out turbines that
  started or stopped during the year.
- Since 1 April 2023 the [download page](https://www.marktstammdatenregister.de/MaStR/Datendownload)
  also offers the export of the first day of every quarter. open-MaStR loads one with
  `Mastr().download(select_date_interactively=True)`.
- Location and capacity make small plants easier to identify. Read
  [Personal data](data-license.md#personal-data) before you share the result.
