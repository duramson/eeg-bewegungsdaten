# Output

The commands write their output into the data folder, `~/.eeg-bewegungsdaten` unless you choose
another one (see [Usage](usage.md#data-folder)):

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
├── export/README.md
├── export/sources.txt
├── export/sources.json
├── export/datapackage.json
└── anlage_id.parquet
```

`.meta.json` holds URL, SHA-256, size, publication date (from the page, for older years the
file date), part number and download time. `source_url` is where the bytes came from: the
Wayback copy when it replaced a file gone from netztransparenz.de. For files from the Wayback
Machine, `orig_last_modified` holds the `Last-Modified` date of the original server. The report
holds the source files, line counts, all codes with counts, the sums of the number columns and,
per CSV file, its header, its blank lines and its empty amounts.

The `.meta.json` files, the reports and `sources.json` of the export record the version of the
tool as `tool_version`. Before a release it is the same for every commit. So when the tool runs
from a git checkout, they also record the commit as `tool_commit`, and as `tool_dirty` whether
tracked files differed from it. For an installed package both are null.

The Parquet files form three layers:

- `text`: every line of every CSV file, split into the columns of the common schema. All
  values stay text, exactly as published. Use it to look at the raw values.
- `clean`: the lines of `text` that can be read, trimmed, typed and marked with `hinweise`.
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

## Columns of `text`

| Column | Type | Content |
| --- | --- | --- |
| `jahr`, `uenb`, `quelldatei`, `zeile` | | As in `clean` |
| `kodierung` | String | `utf-8`, `cp1252` or `undecodable` |
| `felder` | Int32 | Number of fields in the line |
| `felder_kopf` | Int32 | Number of fields in the header |
| `eeg_mastr_nr` … `monat` | String | The fields as published, not trimmed. Empty fields are empty strings; columns the file does not have, and fields missing at the end of a short line, are null |
| `zusatzfelder` | List[String] | Fields under an empty header name, such as the two extra columns of Amprion's `ausgefoerdert.csv`; null if the file has none |
| `rohzeile` | String | The whole line, only if `felder` differs from `felder_kopf` or the file has a column that is not kept (row numbers; the TSO's BDEW number in Amprion 2018 and 2019; the Bemessungsleistung in Amprion's vNNE file of 2016). Otherwise the fields hold the whole line |

## Columns of `clean`

| Column | Type | Content |
| --- | --- | --- |
| `jahr` | Int16 | Year of the data, from the file name |
| `uenb` | String | `50hertz`, `amprion`, `tennet` or `transnetbw` |
| `eeg_mastr_nr` | String | `EEG_Mastr_Nr` as published; see `hinweise`. The EEG number found in the MaStR is `eeg_mastr_nr_abgeglichen` of `mastr` |
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
| `hinweise` | List[String] | Quality codes, see [Quality checks](#quality-checks) |
| `quelldatei` | String | CSV file inside the ZIP (TenneT 2023: TXT file) |
| `zeile` | Int32 | Line in that CSV file; the header is line 1 |

The MaStR columns are null before 2020. Empty amounts are null.

The legend states no units. The values are kWh and euros: the sums match the EEG annual account,
which states its units (see
[Checks against the EEG annual account](findings.md#checks-against-the-eeg-annual-account)).
For 2025, `eeg_zahlung` sums to 17.8 billion euros.

## Columns of `quarantine`

| Column | Type | Content |
| --- | --- | --- |
| `jahr`, `uenb`, `quelldatei`, `zeile` | | As in `clean` |
| `fehlercode` | String | Why the line is in quarantine, see [Quality checks](#quality-checks) |
| `detail` | String | The value that failed the check, e.g. `strommenge='12.5'` |
| `rohzeile` | String | The decoded line |

## Summing energy

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
[Checks against the EEG annual account](findings.md#checks-against-the-eeg-annual-account)).
The rules hold for 2016 to 2025; a category of a later year may need a new one. What each kind
of row holds, with its energy per year, is in [Special rows](findings.md#special-rows).

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
| `BAD_NUMBER` | An amount is not a decimal number with comma, e.g. `12.5` or `9,59E+11` (Excel cut off digits), or too large for a float |

All other problems keep the row and add a code to `hinweise`, so sums stay complete:

| `hinweise` | Meaning |
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
[Reading the files](findings.md#reading-the-files).
