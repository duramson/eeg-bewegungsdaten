# Export a dataset to share

`export` writes the clean rows of every year without the plant keys (`anlagenschluessel`).
Each plant gets a random number instead. The export is pseudonymous, not anonymous: read
[Pseudonymous, not anonymous](#pseudonymous-not-anonymous) and [Personal data](data-license.md#personal-data)
before you share it. Whoever needs the plant keys, for example to retrace the match with the
MaStR, can build the data from the source files with `download`, `ingest` and `mastr`.

```bash
uv run eeg-bewegungsdaten export
```

- Run `ingest` and `mastr` first. `export` reads only `clean` and `mastr`. It never reads
  `text` or `quarantine`, whose raw lines hold the plant keys.
- Each MaStR report records the SHA-256 of its clean input. Export and the analysis scripts
  refuse matches whose input has changed, even if file names and line numbers still agree.
  The bridge reads the clean files of all years. So a match made with the bridge is also
  refused if any clean file was changed, added or removed since, or if its report does not
  list them, as in reports of earlier versions. Then run `mastr` for all years again.
- It writes one file per year with the rows of all TSOs:
  `~/.eeg-bewegungsdaten/export/eeg-bewegungsdaten-<year>.parquet`.
- `sources.txt` and `sources.json` hold the source notes for the EEG data and for the MaStR,
  with the date of the MaStR export. They also list the source files with their SHA-256, and
  name the version of the tool and, for a build from a git checkout, its commit.
- `datapackage.json` describes the export in [OEMetadata v2](https://github.com/OpenEnergyPlatform/oemetadata),
  the metadata standard of the Open Energy Platform, which builds on the Frictionless Data
  Package: one resource per year with every column (type, unit, meaning, codes), its source
  files and the licenses. The EEG data have no settled license, and it says so. It names no
  person unless you give `--contributor "<name>"` and `--contributor-url <ORCID or profile>`,
  for whoever publishes the export.
- `README.md` is the datacard in English, for whoever gets only the export: what the data are,
  years, TSOs and rows, every column with type, unit and meaning, notes on summing energy, on the
  MaStR data and on personal data, the licenses and the source notes. It has no tables, as CKAN
  does not render them.
- `--year 2025` exports only that year. `--out <folder>` writes to another folder. A folder
  with the export of other years is refused, so that the source notes always fit the files. So
  is a folder with a `README.md` that `export` did not write.
- For all years it takes about 20 seconds and up to 4 GB of memory.

## Columns of the export

All columns of `clean` except `anlagenschluessel`, plus these:

| Column | Type | Content |
| --- | --- | --- |
| `anlage_id` | Int64 | Random number per TSO and plant key; null if the row has no plant key (944,842 rows of 50Hertz, 2022 to 2025) |
| `treffer`, `eeg_mastr_nr_abgeglichen`, `energietraeger` | String | From `mastr`, see [Columns of `mastr`](mastr.md#columns-of-mastr) |

`eeg_mastr_nr` is the EEG number as the TSO published it, `eeg_mastr_nr_abgeglichen` the one
found in the MaStR. Use the latter to join MaStR data. `quelldatei` and `zeile` stay, so that
every row can be traced to its line in the source file.

Exports of earlier versions of the tool, such as the one of 21 October 2026, use older names: `qa_flags` for `hinweise`,
`mastr_eeg_nr` for `eeg_mastr_nr_abgeglichen`, `ANLAGENSCHLUESSEL_BRUECKE` for
`ANLAGENSCHLUESSEL_BRIDGE`, `biomasse`, `wasser` and `speicher` for `biomass`, `hydro` and
`storage`, and `trefferquote` for `match_rate` in `sources.json`. Output files of `ingest` and
`mastr` with the old names are refused with a message which command to run again.

## How `anlage_id` is assigned

- The tool draws every `anlage_id` at random. It is not a hash, so nobody can compute it from a
  plant key.
- The tool keeps the assignment in `~/.eeg-bewegungsdaten/anlage_id.parquet`, outside
  `export/`. A plant keeps its number across years and runs.
- Never share `anlage_id.parquet`: it leads from `anlage_id` back to the plant key.
- If you delete it, the next export draws new numbers for all plants.
- The same plant key at two TSOs gets two numbers.

## Pseudonymous, not anonymous

- `quelldatei` and `zeile` point to the line in the source file. Anyone who has that file
  finds the original line again, plant key included.
- `eeg_mastr_nr` and `eeg_mastr_nr_abgeglichen` lead to the plant in the public MaStR.
- The export keeps every row, also those of PV plants up to 30 kW. It does not show them only
  in aggregates, as [Personal data](data-license.md#personal-data) advises.
- Apart from `anlagenschluessel`, no column of `clean` holds free text about people (checked
  for 2016 to 2025). `netzbetreiber` holds the names of grid operators; a few are sole traders
  named after their owner. In a few thousand rows, `eeg_mastr_nr` holds a code such as
  `Grund_MASTRNR99` or a note such as `Noch ausstehend` instead of a number.
