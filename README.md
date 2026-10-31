# eeg-bewegungsdaten

Download, archive and clean the EEG-Bewegungsdaten: the per-plant payments under the German
Renewable Energy Sources Act (EEG) that the four transmission system operators (TSOs) publish
every year. The tool reads the files of 2016 to 2025 ([which files exist](docs/data-status.md))
and writes one Parquet table per year and TSO in a common schema. `mastr` finds the plant of every
clean row in the Marktstammdatenregister (MaStR); `export` writes a dataset to share without the plant
keys.

> This project is unofficial and not affiliated with netztransparenz.de or the TSOs.

Status: early development, not on PyPI.

The files hold personal data and have no settled license. Read the
[data license](docs/data-license.md) before you download older years or share anything derived
from them.

## Prerequisites

- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/).
- Disk space: about 5 GB in the data folder for all years (1 GB of downloaded files), and about
  10 GB for the MaStR database that `mastr` needs.
- Memory: up to 4.2 GB for `mastr` and 4 GB for `export`; `ingest` needs 1.3 GB at most.
- No account: neither netztransparenz.de nor the MaStR asks for one.

## Install

```bash
git clone https://github.com/duramson/eeg-bewegungsdaten
cd eeg-bewegungsdaten
uv sync
```

## Quick start

Run the steps in this order. The times are for all years on a current laptop, the sizes those of
the outputs of 2016 to 2025 in October 2026. `--year 2025` limits a step to one year; `mastr` still
reads every year that `ingest` wrote, for its [bridge](docs/mastr.md#the-bridge).

1. **Download** the files into the local archive in `~/.eeg-bewegungsdaten`. Needs nothing.

   ```bash
   uv run eeg-bewegungsdaten download
   ```

   About 1 GB (170 MB for 2025). The time depends on the servers: one file after the other, all
   years took about ten minutes in October 2026; it now downloads several files at a time. It
   prints one line per file with size, time, speed and source.

2. **Read** the files into Parquet. Needs step 1.

   ```bash
   uv run eeg-bewegungsdaten ingest
   ```

   About two minutes (15 seconds for 2025); writes about 2.3 GB. It prints rows, clean rows and
   quarantined lines per upload, with the error codes of the quarantine.

3. **Build the MaStR database** with [open-MaStR](https://github.com/OpenEnergyPlatform/open-MaStR),
   once. Needs nothing from the steps before.

   ```bash
   uvx --from "git+https://github.com/OpenEnergyPlatform/open-MaStR@develop" python -c "from open_mastr import Mastr; Mastr().download(data=['solar', 'wind', 'biomass', 'hydro', 'gsgk', 'storage', 'deleted_units'])"
   ```

   Downloads the MaStR export of 2.4 GB, keeps it and writes a database of 7.2 GB, both in
   `~/.open-MaStR`. The time depends on the line; it was not measured. open-MaStR prints its own
   progress. Why `develop` and what to check is in
   [Get the MaStR data](docs/mastr.md#get-the-mastr-data-with-open-mastr).

4. **Match** the rows with the MaStR. Needs steps 2 and 3.

   ```bash
   uv run eeg-bewegungsdaten mastr
   ```

   About four minutes (two for 2025); writes about 450 MB. It prints the share of rows and of EEG
   payments matched, per year and TSO.

5. **Export** a dataset to share. Needs step 4.

   ```bash
   uv run eeg-bewegungsdaten export
   ```

   About 20 seconds; writes 1.35 GB (226 MB for 2025). It prints rows, plants, the share matched
   and the file size per year. Read [Pseudonymous, not anonymous](docs/export.md#pseudonymous-not-anonymous)
   before you share it.

`uv run eeg-bewegungsdaten verify` checks the archive against the recorded SHA-256 at any time.

## Documentation

- [The data](docs/data.md): what the files hold and how the TSOs publish them
- [Data status](docs/data-status.md): which files exist for which year and TSO, and where
- [Usage](docs/usage.md): the commands and their options, how downloads and outputs are kept safe
- [Output](docs/output.md): the files, their columns, summing energy, quality checks
- [Matching with the MaStR](docs/mastr.md): open-MaStR, how rows are matched, the bridge
- [Export](docs/export.md): the dataset to share, its columns and metadata, `anlage_id`
- [Data license](docs/data-license.md): what the law allows, personal data, the MaStR license
- [Development](docs/development.md): tests and workflows; see also
  [CONTRIBUTING.md](CONTRIBUTING.md)
- [Findings](docs/findings.md): what the files hold, and how well the tool reads and matches them
- [Analyses](analysis/README.md): scripts that use the output, such as the full-load hours of wind
  per district

## Moved sections

Earlier versions of this README held the whole manual. Its sections are now in `docs/`. The
datacards of earlier exports and other sites link to these:

- <a id="summing-energy"></a>Summing energy: [docs/output.md](docs/output.md#summing-energy)
- <a id="add-mastr-data"></a>Add MaStR data: [docs/mastr.md](docs/mastr.md#add-mastr-data)
- <a id="personal-data"></a>Personal data: [docs/data-license.md](docs/data-license.md#personal-data)
- <a id="data-license"></a>Data license: [docs/data-license.md](docs/data-license.md)
- <a id="columns-of-the-export"></a>Columns of the export:
  [docs/export.md](docs/export.md#columns-of-the-export)

## License

Copyright (C) 2026 Cedrik Theesen. The code is under the
[GNU Affero General Public License, version 3 or later](LICENSE). For the data, see
[Data license](docs/data-license.md).
