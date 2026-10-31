# Development

```bash
uv sync
uv run pre-commit install
uv run pytest
```

`uv run pytest --live` also checks the page on netztransparenz.de. `uv run pytest --realdata`
also reads the downloaded files of 2016 to 2025 and compares the line counts and codes with
reference counts, which are the counts in [docs/findings.md](findings.md). It also checks
the earlier uploads of TenneT 2024 and TransnetBW 2020, the sums of 2023 to 2025 against the EEG
annual account, and an export of 2025 into a temporary folder, rebuilding its matches from the
local open-MaStR database. Files that are not downloaded, or that changed upstream, are skipped.

[`analysis/`](../analysis/README.md) holds scripts that use the output and compare it with other
sources. They are not part of the package and need the data folder.

Pull request titles follow Conventional Commits; see [CONTRIBUTING.md](../CONTRIBUTING.md).

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
