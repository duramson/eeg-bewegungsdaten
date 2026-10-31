# Usage

The [quick start](../README.md#quick-start) shows the order of a first run.

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
  If the page cannot be read, both go on with the files this version knows and exit with 1.
- `download` archives every data file and the legends: about 170 MB for 2025, 1 GB for all
  years. Files already in the archive are skipped; if one file fails, the others still load.
  It downloads several files at a time, with a limit per host. netztransparenz.de and the
  Wayback Machine each have their own queue, so a slow Wayback copy never holds up the other
  files. The limit starts at 2 files per host and grows by one after each downloaded file, up
  to 6 for netztransparenz.de and 3 for the Wayback Machine. A 429, a 503 or a timeout halves
  it (down to 1), and a `Retry-After` holds back all requests to that host. `--jobs N` fixes
  the limit at N files per host, but not above these caps; `--jobs 1` downloads one file after
  the other.
  A broken connection, a timeout or a busy server (429, 5xx) is tried up to four times per
  file, after 1, 2 and 4 seconds or what `Retry-After` asks for (at most a minute). A transfer
  that breaks off, also by Ctrl+C, goes on from where it stopped on the next attempt or run, where
  the server allows it (the Wayback Machine and netztransparenz.de's files of earlier years; the
  CDN of the current year sends the whole file again). Ctrl+C stops all running downloads within
  5 seconds: none of them gets metadata, and only a `.part` that can go on stays. On Windows, a
  `.part` that is still open after these 5 seconds stays too; the next run replaces it. A full disk
  stops the run: the files already running end, no further file starts. A file whose bytes
  differ from the SHA-256 the tool records for it is archived as it is now, with a warning and
  `expected_sha256` in its metadata, and `download` exits with 1: the TSO may have replaced it.
  On a terminal it shows the running files with their progress, the next files in the queue
  and a summary that updates during the run, with the current limit per host; each finished
  file prints a line with size, time, average speed, source and the start of its SHA-256. `-v`
  adds the full SHA-256 and path.
  Without a terminal, only these lines and the summary are printed, on stderr.
- `ingest` reads the newest upload per year and TSO and writes the Parquet files. It takes
  about 15 seconds for 2025 and about two minutes for all years on a current laptop, with
  1.3 GB of memory at most. After an update of the tool, run it again for all years, so that every
  Parquet file has the same columns. It shows the same kind of display as `download`, with the
  bytes of CSV read per upload, and prints rows, clean rows, quarantine and codes per upload.
- `mastr` matches the clean rows with the Marktstammdatenregister; see
  [Matching with the Marktstammdatenregister](mastr.md).
- `export` writes a dataset without the plant keys; see
  [Export a dataset to share](export.md).
- `verify` recomputes the SHA-256 of every archived file and compares it with the metadata. It
  reports missing and truncated files as errors too.

## Options

`--year 2025` and `--uenb amprion` limit `download`, `ingest` and `mastr`; both can be
repeated. `--year` also limits `export`. `-v` before the command prints more detail.
`uv run eeg-bewegungsdaten <command> --help` lists the options of each command.

## Data folder

The data folder is `~/.eeg-bewegungsdaten`. Change it with the environment variable
`EEG_BEWEGUNGSDATEN_DIR`, or with `--data-dir` before the command:
`uv run eeg-bewegungsdaten --data-dir /path/to/data ingest`.

## How downloads are kept safe

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

## How outputs are replaced

- `ingest` writes the new `text`, `clean`, `quarantine` and report of a year and TSO next to the
  old ones first, as `.new` files, and then the list of the four files to
  `reports/<year>/<tso>.pending.json`. If that fails, the old files stay as they are.
- Then it renames the files and deletes the list.
- If a run stops between the renames, the list stays, and the files of that year and TSO are a
  mix of old and new. Until the next `ingest` of that year and TSO finishes the renames, `scan`
  and `mastr` refuse to run at all, as they read every year and TSO, and `export` stops if that
  year is among those it exports. `ingest` finishes the renames first, even if it cannot read
  the upload afterwards.
- Do not delete `.new` files while a `.pending.json` file exists: `ingest` takes a missing
  `.new` file as renamed already and would keep the mix.
- Tools that read the files directly, such as DuckDB, do not check the list. If you find a
  `.pending.json` file, run `ingest` again before you read the files.
