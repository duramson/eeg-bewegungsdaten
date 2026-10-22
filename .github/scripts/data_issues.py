"""Open the GitHub issues for the yearly upkeep of eeg-bewegungsdaten.

Run by .github/workflows/data-issues.yml, daily from August to October and weekly otherwise.
Compares the netztransparenz.de page with `OLDER_FILES` and opens:

- for a year after the newest in `OLDER_FILES`, one issue per task: reading the files of each
  TSO, keeping the files, the annual account, the MaStR match and the release;
- for a file of a known year that `OLDER_FILES` lacks (a TSO uploaded a new version), one issue;
- from 16 September on, if the page still lacks the previous year, one issue.

An issue whose title exists already, open or closed, is not opened again. Each issue gets the
label "data <year>". With --dry-run, the script prints the issues instead of opening them.

    uv run python .github/scripts/data_issues.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from eeg_bewegungsdaten.links import (
    OLDER_FILES,
    PAGE_URL,
    UENB,
    DownloadLink,
    find_links,
    newest_known_year,
)

NAMES = {"50hertz": "50Hertz", "amprion": "Amprion", "tennet": "TenneT", "transnetbw": "TransnetBW"}
# § 51 (1) no. 2 EnFG: the data of a year by 15 September of the next year
DEADLINE = (9, 15)


@dataclass(frozen=True)
class Issue:
    title: str
    body: str
    year: int

    @property
    def label(self) -> str:
        return f"data {self.year}"


def plan(page: Sequence[DownloadLink], today: dt.date) -> list[Issue]:
    """The issues that the links on the page call for on `today`."""
    known = newest_known_year()
    known_urls = {url for url, *_ in OLDER_FILES}
    data = [link for link in page if link.kind == "bewegungsdaten" and link.year]
    new_years = sorted({link.year for link in data if link.year and link.year > known})

    issues: list[Issue] = []
    for year in new_years:
        issues += new_year(year, [link for link in data if link.year == year])
    for link in page:
        if link.url in known_urls or (link.year and link.year in new_years):
            continue
        if link.kind == "legende" and new_years:
            continue  # the issue to keep the files of the new year covers the legend
        issues.append(new_upload(link))

    due = today.year - 1 if (today.month, today.day) > DEADLINE else today.year - 2
    if max((link.year for link in data if link.year), default=0) < due:
        issues.append(overdue(due, today))
    return issues


def new_year(year: int, links: Sequence[DownloadLink]) -> list[Issue]:
    issues = []
    for uenb in UENB:
        urls = "\n".join(f"- {link.url}" for link in links if link.uenb == uenb)
        issues.append(
            Issue(
                f"Data {year}: read the files of {NAMES[uenb]}",
                f"""The page lists the EEG-Bewegungsdaten {year} of {NAMES[uenb]}:

{urls or "- none yet"}

- [ ] `uv run eeg-bewegungsdaten download --year {year} --uenb {uenb}`
- [ ] `uv run eeg-bewegungsdaten ingest --year {year} --uenb {uenb}` runs without a \
`FormatError`. If not, add the new layout to `schema.py` (`SOURCE_COLUMNS`, `LAYOUTS`) and its \
header to `tests/old_formats.py`.
- [ ] Compare the quarantine codes and `hinweise` with {year - 1}. New codes or large jumps \
need a look.
- [ ] Look for new files in the ZIP, parts, encodings and changes in the number of lines.
- [ ] Add the SHA-256, the line count and the codes to `EXPECTED` in \
`tests/test_realdata.py` and run `uv run pytest --realdata`.
- [ ] Update the result tables in `docs/findings.md`.
""",
                year,
            )
        )
    issues += [
        Issue(
            f"Data {year}: keep the files for after they leave the page",
            f"""The page lists {year} until the data of {year + 1} come out, by 15 September \
{year + 2}. After that, the tool finds the files of {year} only through `OLDER_FILES`.

- [ ] Add the files of {year} and the legend to `OLDER_FILES` in `links.py`, with URL and \
publication date. This also ends the warning about an unknown year.
- [ ] Save each URL in the Wayback Machine: https://web.archive.org/save
- [ ] Once the Wayback index lists the captures, compare their SHA-1 with the downloaded files \
and add timestamp and SHA-256 to `OLDER_FILES`.
- [ ] Check that the files of {year - 1} still download now that the page has moved on: \
`uv run eeg-bewegungsdaten --data-dir <empty folder> download --year {year - 1}`
""",
            year,
        ),
        Issue(
            f"Data {year}: compare with the EEG annual account",
            f"""- [ ] Find the EEG-Jahresabrechnung {year} on netztransparenz.de.
- [ ] Add its sums per TSO to `OFFICIAL` in `tests/test_annual_account.py` and run \
`uv run pytest --realdata tests/test_annual_account.py`.
- [ ] Compare the energy of `grid_feed_in()` with the account. A gap above 0.02 % can mean a \
new kind of special row.
- [ ] List the categories of {year} that {year - 1} did not have, and check whether \
`SPECIAL_ROWS` in `special_rows.py` needs a new rule.
- [ ] Update "Special rows" and "Checks against the EEG annual account" in `docs/findings.md`.
""",
            year,
        ),
        Issue(
            f"Data {year}: match with the MaStR",
            f"""- [ ] Update the open-MaStR database.
- [ ] `uv run eeg-bewegungsdaten mastr --year {year}` and compare the hit rates with \
{year - 1}.
- [ ] Update "Match rates" and "Match results" in `docs/findings.md`.
""",
            year,
        ),
        Issue(
            f"Data {year}: release a version that reads {year}",
            f"""Once the other issues with the label "data {year}" are closed:

- [ ] Raise the version and release it.
""",
            year,
        ),
    ]
    return issues


def new_upload(link: DownloadLink) -> Issue:
    what = f"{NAMES.get(str(link.uenb), str(link.uenb))} {link.year}" if link.year else "legend"
    when = link.published.isoformat() if link.published else str(link.file_id)
    # A legend has no year; it belongs to the newest year on the page.
    year = link.year or newest_known_year()
    return Issue(
        f"Data {year}: new upload of the {what} file ({when})",
        f"""The page lists a file that `OLDER_FILES` does not have:

{link.url}

- [ ] Download and ingest it. Compare it with the upload before: rows, sums, categories (see \
"Older years" in the README).
- [ ] Add it to `OLDER_FILES` and keep the upload before.
- [ ] Save it in the Wayback Machine and add the copy to `OLDER_FILES`.
""",
        year,
    )


def overdue(year: int, today: dt.date) -> Issue:
    return Issue(
        f"Data {year}: not on the page after 15 September {year + 1}",
        f"""The TSOs publish the data of a year by 15 September of the next year (§ 51 (1) no. 2 \
EnFG). On {today.isoformat()}, the page does not list {year} yet.

- [ ] Check the page: {PAGE_URL}
- [ ] Look for an announcement of a delay or a new place for the files.
""",
        year,
    )


Runner = Callable[[Sequence[str], str | None], str]


def gh(args: Sequence[str], stdin: str | None = None) -> str:
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True)
    return result.stdout


def open_issues(issues: Sequence[Issue], run: Runner = gh) -> list[Issue]:
    """Open the issues whose title does not exist yet; return those opened."""
    listed = run(["issue", "list", "--state", "all", "--limit", "1000", "--json", "title"], None)
    existing = {issue["title"] for issue in json.loads(listed)}
    opened = []
    for issue in issues:
        if issue.title in existing:
            continue
        run(["label", "create", issue.label, "--color", "1d76db", "--force"], None)
        run(
            ["issue", "create", "--title", issue.title, "--label", issue.label, "--body-file", "-"],
            issue.body,
        )
        existing.add(issue.title)
        opened.append(issue)
    return opened


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the issues, open none")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)

    issues = plan(find_links(older=False), args.today)
    if args.dry_run:
        for issue in issues:
            print(f"# {issue.title}  [{issue.label}]\n\n{issue.body}")
        return 0
    for issue in open_issues(issues):
        print(f"Opened: {issue.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
