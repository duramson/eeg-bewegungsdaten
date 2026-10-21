# eeg-bewegungsdaten

Download, archive and clean the EEG-Bewegungsdaten: the per-plant payments under the German
Renewable Energy Sources Act (EEG) that the four transmission system operators (TSOs) publish
every year. The result is one Parquet table per year and TSO in a common schema, plus a
quarantine for lines that cannot be read and a text layer with every line as published.
Optionally, `mastr` finds the plant of every clean row in the Marktstammdatenregister (MaStR).

> This project is unofficial and not affiliated with netztransparenz.de or the TSOs.

Status: early development, not on PyPI. Tested with the files for 2016 to 2025.

The files hold personal data and have no settled license. Read [Data license](#data-license)
before you download older years or share anything derived from them.

This README describes the tool. What the files hold, and how well the tool reads them and matches
them with the MaStR, is in [docs/findings.md](docs/findings.md). [analysis/](analysis/README.md)
holds analyses that use the output, such as the full-load hours of wind per district.

## The data

For every plant paid under the EEG, the files list the energy fed into the grid, the EEG
payment and the EEG receipts (`EEG_Einnahmen`, from 2024; until 2023 the avoided grid fees,
`Vermiedene_Netzentgelte`), split by sales form (`Veraeusserungsform`) and payment category.
Market premium rows are mostly monthly from 2021 on; most other rows are annual values.

- 50Hertz, Amprion, TenneT and TransnetBW publish the files on
  [netztransparenz.de](https://www.netztransparenz.de/de-de/Erneuerbare-Energien-und-Umlagen/EEG/EEG-Abrechnungen/EEG-Jahresabrechnungen/EEG-Bewegungsdaten).
  The official legend (XLSX) is on the same page.
- They publish the data for the previous year by 15 September (§ 51 (1) no. 2 EnFG).
- They only have to keep the data online until the end of the following calendar year
  (§ 51 (5) EnFG). Older years disappear. On 18 October 2026 the page listed only the files for
  2025. This is why the tool keeps every download in a local archive.
- Each TSO publishes one ZIP per year with 1 to 14 CSV files (TenneT 2024: four ZIPs), 9.3
  million lines in total for 2025. The TSOs differ in encoding, line endings, column names and
  number of columns, and the layout changed several times, see
  [Formats of 2016 to 2025](docs/findings.md#formats-of-2016-to-2025).

### Older years

The page only lists the latest year. The tool knows where the earlier files are (`links.py`,
`OLDER_FILES`) and downloads them like the current ones:

- **2021 to 2024** are still on netztransparenz.de, but no page links them any more
  (`/xspproxy/api/staticfiles/ntp-relaunch/dokumente/…`). They can disappear at any time. For
  all 24 files the Wayback Machine has a copy with the same bytes; when netztransparenz.de
  answers 404, `download` takes that copy instead.
- **2016 to 2020** are gone from netztransparenz.de. The Wayback Machine has one copy of each
  file; the tool fetches the original bytes from `web.archive.org`.
- **Before 2016** the data were not on netztransparenz.de. The Wayback Machine only has TenneT
  2011 and 2012; the tool does not read them.

`OLDER_FILES` also lists the files of 2025, the year on the page in October 2026. The tool takes
them from the page as long as it lists them, and from `OLDER_FILES` once the page moves on to
2026 (by 15 September 2027). Whether their URLs keep working then is unknown. Like the files of
2021 to 2024, they and the legend have a copy with the same bytes in the Wayback Machine.

All files of 2016 to 2024 are 899 MB. Two years were uploaded twice; the tool archives both
uploads and `ingest` reads the one published last:

- **TenneT 2024:** four parts of December 2025 replace three parts of September 2025 (66 MB
  more). The first upload has no `Monat`; the second has the same plants and the same sums, but
  splits 847,335 rows into months, mostly Marktprämie.
- **TransnetBW 2020:** the upload of August 2021 replaces the one of July 2021. Only the row
  order and 102,031 categories differ: `…K33b1eMPM<month>` became `…K33b1-MPM<month>`.

## Install

Requires Python 3.10 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/duramson/eeg-bewegungsdaten
cd eeg-bewegungsdaten
uv sync
```

## Usage

```bash
uv run eeg-bewegungsdaten links
uv run eeg-bewegungsdaten download
uv run eeg-bewegungsdaten ingest
uv run eeg-bewegungsdaten mastr
uv run eeg-bewegungsdaten export
uv run eeg-bewegungsdaten verify
```

- `links` lists the files on the page and the known files of earlier years. The download URLs
  on the page contain an id that changes with every upload, so the tool reads them from the
  page and takes TSO and year from the file name. If the page lists a year that this version of
  the tool does not know yet, `links` and `download` warn: its files may come in a new format.
- `download` archives every data file and the legends: about 170 MB for 2025, 1 GB for all
  years. Files already in the archive are skipped; if one file fails, the others still load.
- `ingest` reads the newest upload per year and TSO and writes the Parquet files. It takes
  about 15 seconds for 2025 and about two minutes for all years on a current laptop, with
  1.3 GB of memory at most. After an update of the tool, run it again for all years, so that every
  Parquet file has the same columns.
- `mastr` matches the clean rows with the Marktstammdatenregister; see
  [Matching with the Marktstammdatenregister](#matching-with-the-marktstammdatenregister).
- `export` writes a dataset without the plant keys; see
  [Export a dataset to share](#export-a-dataset-to-share).
- `verify` recomputes the SHA-256 of every archived file and compares it with the metadata. It
  reports missing and truncated files as errors too.

`--year 2025` and `--uenb amprion` limit `download`, `ingest` and `mastr`; both can be
repeated. `--year` also limits `export`.

The data folder is `~/.eeg-bewegungsdaten`. Change it with the environment variable
`EEG_BEWEGUNGSDATEN_DIR`, or with `--data-dir` before the command:
`uv run eeg-bewegungsdaten --data-dir /path/to/data ingest`.

### How downloads are kept safe

- Each upload goes into its own folder named after its id, so a new upload does not overwrite
  an old one. Files without an id (older years) get a folder named after a hash of their URL.
  The parts of one upload share a folder.
- Data arrive in a `.part` file. The tool checks the size against `Content-Length` and tests
  the ZIP structure, then renames the file. An HTML error page fails this test.
- A file only counts as downloaded once its `.meta.json` exists. An aborted download is
  fetched again on the next run.
- A copy from the Wayback Machine that replaces a file gone from netztransparenz.de must have
  the SHA-256 of the file downloaded in October 2026, or it is rejected.
- Multipart downloads record the expected part numbers. `ingest` refuses an incomplete upload
  before it replaces any output, even if only its last part is missing.
- If a file of the newest upload is missing or truncated, `ingest` stops with an error for that
  year and TSO and keeps the existing output. It never falls back to an older upload; run
  `download` again.
- The ZIPs are read as streams and never unpacked to disk.

## Output

```
~/.eeg-bewegungsdaten/
├── raw/2025/amprion/<file id>/amprion gmbh eeg-zahlungen bewegungsdaten 2025.zip
├── raw/2025/amprion/<file id>/amprion gmbh eeg-zahlungen bewegungsdaten 2025.zip.meta.json
├── raw/legende/<file id>/bewegungsdaten_legende.xlsx
├── text/2025/amprion.parquet
├── clean/2025/amprion.parquet
├── quarantine/2025/amprion.parquet
├── mastr/2025/amprion.parquet
├── reports/2025/amprion.json
├── reports/2025/amprion.mastr.json
├── export/eeg-bewegungsdaten-2025.parquet
├── export/sources.txt
├── export/sources.json
└── anlage_id.parquet
```

`.meta.json` holds URL, SHA-256, size, publication date (from the page, for older years the
file date), part number and download time. `source_url` is where the bytes came from: the
Wayback copy when it replaced a file gone from netztransparenz.de. For files from the Wayback
Machine, `orig_last_modified` holds the `Last-Modified` date of the original server. The report
holds the source files, line counts, all codes with counts, the sums of the number columns and,
per CSV file, its header, its blank lines and its empty amounts.

The Parquet files form three layers:

- `text`: every line of every CSV file, split into the columns of the common schema. All
  values stay text, exactly as published. Use it to look at the raw values.
- `clean`: the lines of `text` that can be read, trimmed, typed and marked with `qa_flags`.
- `quarantine`: the lines of `text` that cannot be read, with an error code.

`ingest` writes all three in one pass. For 2025, `text` takes 185 MB, about as much as
`clean`; for all years both take about 1 GB. Files with a column that is not kept (row numbers
and the like, see `rohzeile` below) keep the whole line and are up to twice as large.

With polars:

```python
import polars as pl

import eeg_bewegungsdaten

payments = (
    eeg_bewegungsdaten.scan()  # all years and TSOs; layer="text" or "quarantine" for the others
    .filter(pl.col("jahr") == 2025)
    .group_by("uenb", "veraeusserungsform")
    .agg(pl.col("eeg_zahlung").sum())
    .collect()
)
```

With DuckDB:

```sql
SELECT uenb, sum(eeg_zahlung)
FROM '~/.eeg-bewegungsdaten/clean/*/*.parquet'
GROUP BY uenb;

-- lines encoded in cp1252, with the plant key as published
SELECT uenb, quelldatei, zeile, anlagenschluessel
FROM '~/.eeg-bewegungsdaten/text/*/*.parquet'
WHERE kodierung = 'cp1252';
```

### Columns of `text`

| Column | Type | Content |
| --- | --- | --- |
| `jahr`, `uenb`, `quelldatei`, `zeile` | | As in `clean` |
| `kodierung` | String | `utf-8`, `cp1252` or `undecodable` |
| `felder` | Int32 | Number of fields in the line |
| `felder_kopf` | Int32 | Number of fields in the header |
| `eeg_mastr_nr` … `monat` | String | The fields as published, not trimmed. Empty fields are empty strings; columns the file does not have, and fields missing at the end of a short line, are null |
| `zusatzfelder` | List[String] | Fields under an empty header name, such as the two extra columns of Amprion's `ausgefoerdert.csv`; null if the file has none |
| `rohzeile` | String | The whole line, only if `felder` differs from `felder_kopf` or the file has a column that is not kept (row numbers; the TSO's BDEW number in Amprion 2018 and 2019; the Bemessungsleistung in Amprion's vNNE file of 2016). Otherwise the fields hold the whole line |

### Columns of `clean`

| Column | Type | Content |
| --- | --- | --- |
| `jahr` | Int16 | Year of the data, from the file name |
| `uenb` | String | `50hertz`, `amprion`, `tennet` or `transnetbw` |
| `eeg_mastr_nr` | String | `EEG_Mastr_Nr` as published; see `qa_flags` |
| `anlagenschluessel` | String | `EEG_Anlagenschluessel`, free text |
| `nb_mastr_nr` | String | MaStR number of the grid operator |
| `nb_bnr` | String | BDEW number of the grid operator: most files up to 2023, TenneT 2025 |
| `netzbetreiber` | String | Name of the grid operator: the same files, except Amprion 2018 and 2019 |
| `uenb_mastr_nr` | String | MaStR number of the TSO |
| `veraeusserungsform` | Int8 | 1 Einspeisevergütung (feed-in tariff), 2 Marktprämie (market premium), 3 Mieterstromzuschlag (tenant electricity supplement), 4 Ausfallvergütung (fallback payment), 5 Sonstiges (other); null before 2020, except Amprion 2018 (column `TYP`) |
| `verguetungskategorie` | String | Payment category |
| `sonderzeile` | String | Kind of row whose amounts need care when you add them up, null for ordinary rows; see [Summing energy](#summing-energy) |
| `strommenge` | Float64 | Energy |
| `eeg_zahlung` | Float64 | EEG payment |
| `eeg_einnahmen` | Float64 | EEG receipts, from 2024 |
| `vermiedene_netzentgelte` | Float64 | Avoided grid fees (vNNE), until 2023 |
| `monat` | Int8 | 1–12, or 0 for the annual value; always 0 before 2021. Other values, or null, with `MONAT_INVALID` |
| `qa_flags` | List[String] | Quality codes, see below |
| `quelldatei` | String | CSV file inside the ZIP (TenneT 2023: TXT file) |
| `zeile` | Int32 | Line in that CSV file; the header is line 1 |

The MaStR columns are null before 2020. Empty amounts are null.

The legend states no units. The values are kWh and euros: the sums match the EEG annual account,
which states its units (see
[Checks against the EEG annual account](docs/findings.md#checks-against-the-eeg-annual-account)).
For 2025, `eeg_zahlung` sums to 17.8 billion euros.

`quarantine` has the columns `jahr`, `uenb`, `quelldatei`, `zeile`, `fehlercode`, `detail` and
`rohzeile` (the decoded line).

### Summing energy

Do not add up `strommenge` over all rows. For 2025 that gives 251 TWh, while the EEG
annual account reports 241.5 TWh of grid feed-in. The column `sonderzeile` marks the rows that
need care. `ingest` sets it from the start or end of `verguetungskategorie`:

| `sonderzeile` | `verguetungskategorie` | Content | Grid feed-in | Generation |
| --- | --- | --- | --- | --- |
| `REPEATED_ENERGY` | ends in `FLP` | energy that other rows of the same biomass plant hold already | leave out | leave out |
| `SELF_CONSUMPTION` | ends in `SV` | self-consumed energy, without payment | leave out | keep |
| `PAID_SELF_CONSUMPTION` | starts with `SgK3341` | self-consumed energy, paid at the full tariff (EEG 2009) | leave out | keep |
| `SELF_CONSUMPTION_DEDUCTION` | starts with `SgK3342` or `SgK3343` | the same energy again, negative, for the deduction from the tariff | leave out | leave out |
| `ENDED_SUPPORT` | ends in `aMW` | value paid for plants whose EEG support has ended | keep | keep |
| `OTHER_DIRECT_MARKETING` | ends in `SO-DV` | other direct marketing, without EEG payment | keep | keep |

`eeg_bewegungsdaten.grid_feed_in()` and `eeg_bewegungsdaten.generation()` are filters for the
last two columns:

```python
import polars as pl
import eeg_bewegungsdaten

energy = (
    eeg_bewegungsdaten.scan()
    .filter(eeg_bewegungsdaten.grid_feed_in())
    .group_by("jahr", "uenb")
    .agg(pl.col("strommenge").sum())
    .collect()
)
```

With `grid_feed_in()`, the energy of 2023 to 2025 matches the annual account to 0.02 % (see
[Checks against the EEG annual account](docs/findings.md#checks-against-the-eeg-annual-account)).
The rules hold for 2016 to 2025; a category of a later year may need a new one. What each kind
of row holds, with its energy per year, is in [Special rows](docs/findings.md#special-rows).

## Quality checks

Every line is in `text` once and ends up exactly once in either `clean` or `quarantine`.
`ingest` stops with an error if the counts do not add up. Blank lines, also lines with nothing
but `;`, are not data; the report counts them per file.

A line goes to quarantine only if its values cannot be read:

| `fehlercode` | Meaning |
| --- | --- |
| `UNDECODABLE` | Line is neither UTF-8 nor cp1252 |
| `FIELD_COUNT` | Number of fields differs from the header |
| `BAD_VERAEUSSERUNGSFORM` | `Veraeusserungsform` is empty, or neither 1 to 5 nor one of the texts of 2020 to 2022 and Amprion 2018 |
| `BAD_NUMBER` | An amount is not a decimal number with comma, e.g. `12.5` or `9,59E+11` (Excel cut off digits) |

All other problems keep the row and add a code to `qa_flags`, so sums stay complete:

| `qa_flags` | Meaning |
| --- | --- |
| `MASTR_NR_MISSING` | `EEG_Mastr_Nr` is empty |
| `MASTR_NR_IS_UNIT` | `EEG_Mastr_Nr` holds a unit number (`SEE…`) instead of an EEG number |
| `MASTR_NR_MULTIPLE` | `EEG_Mastr_Nr` holds several numbers, separated by `,` or `/` |
| `MASTR_NR_INVALID` | `EEG_Mastr_Nr` is not `EEG` or `SEE` followed by 12 digits, e.g. `Grund_MASTRNR99`, `KWK…`, `Noch ausstehend` |
| `NO_PLANT_ID` | Both `EEG_Mastr_Nr` and `EEG_Anlagenschluessel` are empty |
| `NB_MASTR_NR_INVALID` | `NB_Mastr_Nr` is not `SNB` followed by 12 digits |
| `UENB_MASTR_NR_MISMATCH` | `UENB_Mastr_Nr` does not belong to the TSO that published the file |
| `MONAT_EMPTY` | `Monat` was empty and is read as 0 |
| `ENCODING_CP1252` | Line was encoded in cp1252 instead of UTF-8 |
| `EXTRA_VALUE` | Value in a column without a header name; `clean` leaves it out, `text` keeps it |
| `MONAT_INVALID` | `Monat` is not 0 to 12 (`09` is read as 9); a whole number is kept, e.g. `13`, anything else is null, e.g. `1-12` |

The checks of a column only run if the file has that column, so files before 2020 get no MaStR
flags.

Without a code, the tool also trims spaces (including no-break spaces) and quotes, drops empty
columns without a header name and row numbers, reads thousands separators, maps the column
names of all years and the texts of `Veraeusserungsform` (`Direktvermarktung`,
`Selbstverbrauch`, `VNE`, …) to the codes of the legend. A header that matches none of the known
layouts stops the file.

How many lines of each year and TSO get which code is in
[Reading the files](docs/findings.md#reading-the-files).

## Matching with the Marktstammdatenregister

`mastr` finds the EEG plant in the Marktstammdatenregister (MaStR) for every clean row. It
writes one row per clean row with the EEG number from the MaStR and how it was found. Join
this table with MaStR data to get capacity, location or commissioning date of the plants.

### Get the MaStR data with open-MaStR

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

### Run the match

```bash
uv run eeg-bewegungsdaten mastr
```

- `mastr` matches all clean files, or those chosen with `--year` and `--uenb`. For the bridge
  (step 4 below) it reads all clean files in any case, so run `ingest` for all years first.
- It looks for the database where open-MaStR puts it: `$SQLITE_DATABASE_PATH`, else
  `data/sqlite/open-mastr.db` in `$MASTR_PROJECT_HOME_DIR` or `~/.open-MaStR`. Give another
  file with `--db`.
- The database is opened read-only. Databases written with `english=True` are not supported.
- It prints the share of rows and of EEG payments that were matched, per year and TSO.
- For 2025 alone it takes about 2 minutes and 4.2 GB of memory, for 2016 to 2025 about 4
  minutes.

### How rows are matched

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
| `MULTIPLE` | Several numbers; at least one found. All EEG numbers found are in `mastr_eeg_nrs` |
| `ANLAGENSCHLUESSEL` | No number found, or the `EEG…` number is given for other plants too (see step 1); the Anlagenschluessel belongs to exactly one EEG plant |
| `KWK_NR` | No number or Anlagenschluessel found; the KWK number belongs to exactly one EEG plant |
| `ANLAGENSCHLUESSEL_BRUECKE` | No number, Anlagenschluessel or KWK number leads to one plant; the rows of all years with this Anlagenschluessel that were matched by number name exactly one EEG plant (step 4) |
| `EEG_NR_CONFLICT` | The `EEG…` number is given for other plants too (see step 1); the Anlagenschluessel belongs to no single EEG plant |
| `AMBIGUOUS` | No number found; the Anlagenschluessel or the KWK number belongs to several EEG plants |
| `DELETED` | No number found; the unit number belongs to a deleted or deactivated unit |
| `NOT_FOUND` | Neither the number nor the Anlagenschluessel is in the MaStR |
| `NO_ID` | Neither a valid number nor an Anlagenschluessel |

The first six count as matched. The match rates of 2016 to 2025 are in
[Matching with the MaStR](docs/findings.md#matching-with-the-mastr).

### The bridge

The files of 2016 to 2019 have no MaStR numbers, so their rows can only be matched by
Anlagenschluessel. For many plants the MaStR holds no `AnlagenschluesselEeg`, or another one.
Without the bridge, 6 to 11 % of the rows of 2016 to 2019 stay without a plant, and the share
differs a lot between grid operators. From 2020 on, the rows hold both the Anlagenschluessel and
the EEG number. The bridge uses these pairs for the rows that the MaStR alone cannot match.

An Anlagenschluessel counts only if the rows of all years and TSOs name exactly one EEG plant.
The tool does not compare the grid operator, as some operators changed their BDEW number or took
over grid areas of others. How many rows the bridge matches, and how it was checked, is in
[The bridge](docs/findings.md#the-bridge) in the findings.

### Columns of `mastr`

| Column | Type | Content |
| --- | --- | --- |
| `jahr`, `uenb`, `quelldatei`, `zeile` | | Same as in `clean`; together they identify the row |
| `treffer` | String | How the row was matched, see above |
| `mastr_eeg_nr` | String | `EegMastrNummer` of the plant; empty unless exactly one plant was found |
| `mastr_eeg_nrs` | List[String] | All EEG numbers found, only for `MULTIPLE` |
| `energietraeger` | String | `solar`, `wind`, `biomasse`, `wasser`, `gsgk` or `speicher`, from the MaStR table |

`reports/<year>/<tso>.mastr.json` holds rows and EEG payments per `treffer`, the shares matched,
the database file and tables used, with open-MaStR's export date (`DatumDownload`), and under
`bridge` the years and the number of Anlagenschluessel of the bridge.

```python
import polars as pl

import eeg_bewegungsdaten

key = ["jahr", "uenb", "quelldatei", "zeile"]
payments = eeg_bewegungsdaten.scan().join(
    pl.scan_parquet("~/.eeg-bewegungsdaten/mastr/*/*.parquet"), on=key, how="left"
)
```

## Export a dataset to share

`export` writes the clean rows of every year without the plant keys (`anlagenschluessel`).
Each plant gets a random number instead. The export is pseudonymous, not anonymous: read
[Pseudonymous, not anonymous](#pseudonymous-not-anonymous) and [Personal data](#personal-data)
before you share it. Whoever needs the plant keys, for example to retrace the match with the
MaStR, can build the data from the source files with `download`, `ingest` and `mastr`.

```bash
uv run eeg-bewegungsdaten export
```

- Run `ingest` and `mastr` first. `export` reads only `clean` and `mastr`. It never reads
  `text` or `quarantine`, whose raw lines hold the plant keys.
- Each MaStR report records the SHA-256 of its clean input. Export and the analysis scripts
  refuse matches whose input has changed, even if file names and line numbers still agree.
- It writes one file per year with the rows of all TSOs:
  `~/.eeg-bewegungsdaten/export/eeg-bewegungsdaten-<year>.parquet`.
- `sources.txt` and `sources.json` hold the source notes for the EEG data and for the MaStR,
  with the date of the MaStR export. They also list the source files with their SHA-256.
- `--year 2025` exports only that year. `--out <folder>` writes to another folder. A folder
  with the export of other years is refused, so that the source notes always fit the files.
- For all years it takes about 20 seconds and up to 4 GB of memory.

### Columns of the export

All columns of `clean` except `anlagenschluessel`, plus these:

| Column | Type | Content |
| --- | --- | --- |
| `anlage_id` | Int64 | Random number per TSO and plant key; null if the row has no plant key (944,842 rows of 50Hertz, 2022 to 2025) |
| `treffer`, `mastr_eeg_nr`, `energietraeger` | String | From `mastr`, see [Columns of `mastr`](#columns-of-mastr) |

`quelldatei` and `zeile` stay, so that every row can be traced to its line in the source file.

### How `anlage_id` is assigned

- The tool draws every `anlage_id` at random. It is not a hash, so nobody can compute it from a
  plant key.
- The tool keeps the assignment in `~/.eeg-bewegungsdaten/anlage_id.parquet`, outside
  `export/`. A plant keeps its number across years and runs.
- Never share `anlage_id.parquet`: it leads from `anlage_id` back to the plant key.
- If you delete it, the next export draws new numbers for all plants.
- The same plant key at two TSOs gets two numbers.

### Pseudonymous, not anonymous

- `quelldatei` and `zeile` point to the line in the source file. Anyone who has that file
  finds the original line again, plant key included.
- `eeg_mastr_nr` and `mastr_eeg_nr` lead to the plant in the public MaStR.
- The export keeps every row, also those of PV plants up to 30 kW. It does not show them only
  in aggregates, as [Personal data](#personal-data) advises.
- Apart from `anlagenschluessel`, no column of `clean` holds free text about people (checked
  for 2016 to 2025). `netzbetreiber` holds the names of grid operators; a few are sole traders
  named after their owner. In a few thousand rows, `eeg_mastr_nr` holds a code such as
  `Grund_MASTRNR99` or a note such as `Noch ausstehend` instead of a number.

## Development

```bash
uv sync
uv run pre-commit install
uv run pytest
```

`uv run pytest --live` also checks the page on netztransparenz.de. `uv run pytest --realdata`
also reads the downloaded files of 2016 to 2025 and compares the line counts and codes with
reference counts, which are the counts in [docs/findings.md](docs/findings.md). It also checks
the earlier uploads of TenneT 2024 and TransnetBW 2020, the sums of 2023 to 2025 against the EEG
annual account, and an export of 2025 into a temporary folder, rebuilding its matches from the
local open-MaStR database. Files that are not downloaded, or that changed upstream, are skipped.

[`analysis/`](analysis/README.md) holds scripts that use the output and compare it with other
sources. They are not part of the package and need the data folder.

Pull request titles follow Conventional Commits; see [CONTRIBUTING.md](CONTRIBUTING.md).

Four GitHub Actions workflows are in `.github/workflows`:

- `ci.yml` runs the pre-commit hooks, mypy and pytest on Python 3.10 to 3.14, and pytest with
  the lowest supported versions (Python 3.10, polars 1.20.0, httpx 0.27.0). It runs on every
  push to `main` and on every pull request.
- `live.yml` runs `pytest --live -m live` every Monday. It loads only the HTML page, never the
  data files. If the weekly check fails, it opens an issue, or comments on the one already open.
- `data-issues.yml` opens the issues for the yearly upkeep, with the label `data <year>`. It
  compares the page with `OLDER_FILES`, daily from August to October and on Mondays in the
  other months. For a new year, it opens one issue per TSO to read its files, and one each to
  keep the files, to compare with the annual account, to match with the MaStR and to release.
  For a new upload of a known year, it opens one issue. From 16 September on, it opens one if
  the previous year is missing. It never opens a title twice.
  `uv run python .github/scripts/data_issues.py --dry-run` shows what it would open today.
- `pr-title.yml` checks that the title of every pull request follows Conventional Commits.

The workflows pin their actions by commit. Dependabot proposes updates once a month.

## Data license

The license of the EEG-Bewegungsdaten is not settled. This project does not redistribute any
data: it downloads the files to your machine, and you decide what you do with them. This section
is not legal advice.

### What the law allows

§ 51 (8) EnFG ([wording](https://www.gesetze-im-internet.de/enfg/__51.html)):

> Die nach den Absätzen 1 bis 3 veröffentlichten Angaben dürfen zu kommerziellen und
> nichtkommerziellen Zwecken verwendet werden.

The data published under paragraphs 1 to 3 may be used for commercial and non-commercial
purposes.

- The rule came in 2016 as § 77 (5) EEG 2017. Its explanatory memorandum says "Eine Nutzung ist
  grundsätzlich unbeschränkt möglich": use is in principle unrestricted
  ([BT-Drs. 18/10209](https://dserver.bundestag.de/btd/18/102/1810209.pdf), p. 121). The EnFG
  took the rule over unchanged in 2022 (BT-Drs. 20/1630, p. 233).
- netztransparenz.de names no license for the files.
- Open: neither the law nor the memorandum mentions redistributing whole files. The TSOs may
  also hold a database right (§ 87a UrhG).

### Years no longer on the page

- § 51 (5) EnFG sets a minimum period for keeping the data online, not an order to delete them.
  By its wording, § 51 (8) covers older years too.
- The tool downloads these years from paths on netztransparenz.de that no page links any more,
  or from the Wayback Machine (see [Older years](#older-years)).
- In their [FAQ on the EEG](https://www.netztransparenz.de/de-de/FAQ/FAQErneuerbareEnergienGesetz-EEG),
  the TSOs write that delivering data beyond the legal requirement is "aus rechtlichen Gründen
  nicht möglich" (not possible for legal reasons). They do not say which reasons.
- The tool fetches each file once and keeps it in the local archive. The
  [terms of use](https://archive.org/about/terms) of the Internet Archive apply to the copies
  from the Wayback Machine.

Decide yourself whether you download these years. `download --year 2025` loads the files of
2025, the year on the page in October 2026, and every legend, including the older ones from
paths that no page links any more.

### Personal data

- The TSOs must not publish data on operators who are natural persons, nor the street and house
  number of plants up to 30 kW (§ 51 (3) nos. 1 and 2 EnFG).
- Still, some plant keys (`EEG_Anlagenschluessel`, `anlagenschluessel` in `clean`) contain
  names of people or streets.
- Many rows belong to small PV plants of private persons. With the MaStR number, anyone can look
  up the town, capacity and start of operation of such a plant. The EEG payment is a financial
  figure.
- If you share data derived from the files, you process personal data and are responsible for
  it under the GDPR.

Before you share data:

- Leave out `anlagenschluessel`. A hash does not help: anyone with the raw file can compute it.
  `export` replaces it with a random number, see
  [Export a dataset to share](#export-a-dataset-to-share).
- Show plants up to 30 kW only in aggregates, never row by row.
- Hide every aggregate with fewer than 10 plants, and every aggregate in which one plant makes
  up almost the whole sum.

### MaStR

The MaStR data that `mastr` reads are under the
[Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0)
(DL-DE-BY-2.0). The register says so on its
[download page](https://www.marktstammdatenregister.de/MaStR/Datendownload) and in its
[legal notice](https://www.marktstammdatenregister.de/MaStR/Startseite/Impressum). The license
asks you to name the Bundesnetzagentur, link the license, point to the dataset and mark your
changes. If you share results that contain MaStR data, a source note can read:

> Datenquelle: Marktstammdatenregister der Bundesnetzagentur, Gesamtdatenexport vom TT.MM.JJJJ,
> https://www.marktstammdatenregister.de/MaStR/Datendownload, Datenlizenz Deutschland –
> Namensnennung – Version 2.0 (www.govdata.de/dl-de/by-2-0); Daten verändert (bereinigt,
> verknüpft, aggregiert).

Replace `TT.MM.JJJJ` with the date of the MaStR export you used.

## License

Copyright (C) 2026 Cedrik Theesen. The code is under the
[GNU Affero General Public License, version 3 or later](LICENSE). For the data, see
[Data license](#data-license).
