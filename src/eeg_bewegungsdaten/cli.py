"""Command line interface: ``eeg-bewegungsdaten <command>``."""

from __future__ import annotations

import argparse
import errno
import logging
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from rich.text import Text

from eeg_bewegungsdaten import display
from eeg_bewegungsdaten._version import __version__
from eeg_bewegungsdaten.datapackage import Contributor
from eeg_bewegungsdaten.download import (
    ArchivedFile,
    ChecksumError,
    DownloadError,
    HostLimits,
    archived_files,
    download_all,
    is_archived,
    verify,
)
from eeg_bewegungsdaten.export import ExportError, export
from eeg_bewegungsdaten.http import new_client
from eeg_bewegungsdaten.ingest import ingest, latest_uploads
from eeg_bewegungsdaten.links import PAGE_URL, UENB, DownloadLink, find_links, older_links
from eeg_bewegungsdaten.mastr import (
    MastrError,
    clean_files,
    default_db_path,
    link_file,
    load_bridge,
    load_register,
)
from eeg_bewegungsdaten.paths import ENV_VAR, default_data_dir, export_dir, output_paths
from eeg_bewegungsdaten.progress import Progress
from eeg_bewegungsdaten.reader import data_size
from eeg_bewegungsdaten.schema import FormatError

log = logging.getLogger("eeg_bewegungsdaten")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.console = display.console()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        handlers=[display.log_handler(args.console)],
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result: int = args.func(args)
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eeg-bewegungsdaten",
        description="Download, archive and clean the EEG-Bewegungsdaten of the four German TSOs "
        "and match them with the Marktstammdatenregister.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"archive and output folder (default: ${ENV_VAR} or ~/.eeg-bewegungsdaten)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(required=True, metavar="command")

    links = commands.add_parser(
        "links", help="list the files on netztransparenz.de and the known files of earlier years"
    )
    links.set_defaults(func=_links)

    fetch = commands.add_parser("download", help="download data files and legend into the archive")
    _add_filters(fetch)
    fetch.add_argument(
        "--jobs",
        type=_jobs,
        metavar="N",
        help="download N files at a time from each host, at most 6 (3 from the Wayback "
        "Machine); 1 downloads one file after the other (default: start with 2 per host and "
        "adapt to the servers)",
    )
    fetch.set_defaults(func=_download)

    build = commands.add_parser(
        "ingest", help="read archived ZIPs into Parquet (text, clean rows, quarantine, report)"
    )
    _add_filters(build)
    build.set_defaults(func=_ingest)

    match = commands.add_parser(
        "mastr", help="match clean rows to the Marktstammdatenregister (open-MaStR database)"
    )
    _add_filters(match)
    match.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite database written by open-MaStR (default: where open-MaStR puts it)",
    )
    match.set_defaults(func=_mastr)

    share = commands.add_parser(
        "export",
        help="write a dataset to share: clean rows with MaStR match, a random plant id instead "
        "of the Anlagenschluessel",
    )
    share.add_argument("--year", type=int, action="append", help="only this year (repeatable)")
    share.add_argument(
        "--out", type=Path, default=None, help="output folder (default: export in the data folder)"
    )
    share.add_argument(
        "--contributor",
        metavar="NAME",
        help="the person who publishes the export, for datapackage.json (default: none)",
    )
    share.add_argument(
        "--contributor-url",
        metavar="URL",
        help="ORCID or profile of the contributor, e.g. https://orcid.org/0000-0000-0000-0000",
    )
    share.set_defaults(func=_export)

    check = commands.add_parser("verify", help="recompute SHA-256 of all archived files")
    check.set_defaults(func=_verify)
    return parser


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year", type=int, action="append", help="only this year (repeatable)")
    parser.add_argument("--uenb", choices=UENB, action="append", help="only this TSO (repeatable)")


def _jobs(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number from 1 on, not {value!r}")
    return int(value)


def _data_dir(args: argparse.Namespace) -> Path:
    data_dir: Path = args.data_dir.expanduser() if args.data_dir else default_data_dir()
    return data_dir


def _wanted(args: argparse.Namespace, jahr: int | None, uenb: str | None) -> bool:
    """Whether `--year` and `--uenb` let this year and TSO through."""
    return (not args.year or jahr in args.year) and (not args.uenb or uenb in args.uenb)


def _links(args: argparse.Namespace) -> int:
    links, page_read = _known_links()
    for link in links:
        published = link.published.isoformat() if link.published else "-"
        print(f"{link.year or '-'}\t{link.uenb or link.kind}\t{published}\t{link.url}")
    return 0 if page_read else 1


def _known_links(client: httpx.Client | None = None) -> tuple[list[DownloadLink], bool]:
    """The links of the page and the older files, and whether the page could be read. Without
    the page, only the files in `OLDER_FILES` remain."""
    try:
        return find_links(client), True
    except (httpx.HTTPError, LookupError) as e:
        log.error(
            "Cannot read %s (%s); going on with the files this version knows",
            PAGE_URL,
            display.reason(e),
        )
        return older_links(), False


# Errors of the file system that every later file would hit as well
_DISK_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}


def _download(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    with new_client() as client:
        known, page_read = _known_links(client)
        links = [
            link for link in known if link.kind == "legende" or _wanted(args, link.year, link.uenb)
        ]
        if not any(link.kind == "bewegungsdaten" for link in links):
            log.error("No known data files match --year/--uenb")
            return 1
        archived = {link.url for link in links if is_archived(data_dir, link)}
        limits = None if args.jobs == 1 else HostLimits(args.jobs)
        with display.DownloadDisplay(
            args.console, links, archived, data_dir, limits=limits, verbose=args.verbose
        ) as shown:
            disk_full = False

            def start(link: DownloadLink) -> Progress:
                log.debug("%s %s: %s", link.year or "-", link.uenb or link.kind, link.url)
                return shown.start(link.url)

            def done(link: DownloadLink, result: ArchivedFile | Exception) -> bool:
                nonlocal disk_full
                if isinstance(result, ArchivedFile):
                    shown.finished(link, result)
                    return True
                if not isinstance(result, (httpx.HTTPError, DownloadError, OSError)):
                    raise result
                shown.failed(link, result)
                if isinstance(result, OSError) and result.errno in _DISK_FULL:
                    # Files that were running already end, and may fail the same way.
                    if not disk_full:
                        log.error("The disk is full; the other files are not downloaded")
                    disk_full = True
                    return False
                return True

            download_all(links, data_dir, client, limits, start=start, done=done)
    return 1 if shown.failures or shown.changed or not page_read else 0


def _ingest(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    uploads = [
        files
        for files in latest_uploads(data_dir)
        if _wanted(args, files[0].meta["year"], files[0].meta["uenb"])
    ]
    if not uploads:
        log.error("No matching downloads in %s; run 'download' first", data_dir)
        return 1

    shown_uploads = [
        display.Upload(
            int(files[0].meta["year"]),
            str(files[0].meta["uenb"]),
            [f.path for f in files],
            _data_size(files),
        )
        for files in uploads
    ]
    with display.IngestDisplay(
        args.console, shown_uploads, data_dir, verbose=args.verbose
    ) as shown:
        for files, upload in zip(uploads, shown_uploads, strict=True):
            try:
                report = ingest(files, data_dir, progress=shown.start(upload.key))
            except FormatError as e:
                shown.fail(upload.key, e)
            else:
                shown.finish(upload.key, report, size=upload.size)
    return 1 if shown.failures else 0


def _data_size(files: Sequence[ArchivedFile]) -> int:
    """Bytes of the CSV files of an upload; 0 if a file cannot be read, which ingest reports."""
    try:
        return sum(data_size(f.path) for f in files)
    except (OSError, zipfile.BadZipFile):
        return 0


def _mastr(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    files = [(jahr, uenb) for jahr, uenb in clean_files(data_dir) if _wanted(args, jahr, uenb)]
    if not files:
        log.error("No matching clean files in %s; run 'ingest' first", data_dir)
        return 1
    db_path = args.db.expanduser() if args.db else default_db_path()
    try:
        with display.Step(args.console, "MaStR register", "tables") as step:
            register = load_register(db_path, progress=step.progress)
            tables = register.source["tables"]
            dates = sorted({d for t in tables.values() for d in t["datum_download"]})
            step.cells = [
                f"{len(tables)} tables",
                f"{sum(t['rows'] for t in tables.values()):,} rows",
                f"export of {', '.join(_iso_day(d) for d in dates) or 'unknown date'}",
                Text(str(db_path), "eeg.muted"),
            ]
    except MastrError as e:
        log.error("%s", e)
        return 1
    if register.source["missing_tables"]:
        log.warning(
            "Missing MaStR tables, their plants or units cannot be matched: %s",
            ", ".join(register.source["missing_tables"]),
        )
    empty = [name for name, table in register.source["tables"].items() if not table["rows"]]
    if empty:
        # open-MaStR can fail on single files and still report success.
        log.warning("Empty MaStR tables, check the open-MaStR log: %s", ", ".join(empty))
    # From all clean files, also those not selected.
    try:
        with display.Step(args.console, "Bridge", "files") as step:
            bridge = load_bridge(data_dir, register, progress=step.progress)
            years = bridge.source["years"]
            step.cells = [
                f"{bridge.source['keys']:,} Anlagenschluessel of one EEG plant",
                Text(
                    f"{bridge.source['keys_with_several_plants']:,} of several, not used",
                    "eeg.muted",
                ),
                _from_years(years),
            ]
    except MastrError as e:
        log.error("%s", e)
        return 1

    cleans = [
        display.Clean(jahr, uenb, _rows(output_paths(data_dir, jahr, uenb).clean))
        for jahr, uenb in files
    ]
    with display.MastrDisplay(args.console, cleans, data_dir, verbose=args.verbose) as shown:
        for clean in cleans:
            shown.start(clean.key)
            report = link_file(data_dir, clean.year, clean.uenb, register, bridge)
            shown.finish(clean.key, report, size=clean.rows)
            if args.verbose:
                codes = ", ".join(
                    f"{code} {n['rows']:,}" for code, n in report["treffer"].items() if n["rows"]
                )
                args.console.print(Text(f"  {codes}", "eeg.muted"))
    return 0


def _from_years(years: Sequence[int]) -> str:
    if not years:
        return ""
    span = str(years[0]) if years[0] == years[-1] else f"{years[0]} to {years[-1]}"
    return f"from the rows matched by number of {span}"


def _rows(path: Path) -> int:
    """Rows of a Parquet file, from its metadata."""
    return int(pl.scan_parquet(path).select(pl.len()).collect().item())


def _iso_day(value: str) -> str:
    """20261021 as 2026-10-21"""
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else value


def _export(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    out_dir = args.out.expanduser() if args.out else export_dir(data_dir)
    by_year: dict[int, list[str]] = {}
    for jahr, uenb in clean_files(data_dir):
        if not args.year or jahr in args.year:
            by_year.setdefault(jahr, []).append(uenb)
    years = [display.ExportYear(jahr, uenbs) for jahr, uenbs in by_year.items()]
    if not years:
        log.error("No matching clean files in %s; run 'ingest' first", data_dir)
        return 1
    offsets = [sum(len(y.uenbs) for y in years[:i]) for i in range(len(years))]
    try:
        with display.ExportDisplay(args.console, years, out_dir, verbose=args.verbose) as shown:
            current = [0]
            moves = [shown.start(years[0].key)]

            def progress(done: int, total: int | None) -> None:
                i = current[0]
                if i < len(years):
                    moves[0](min(done - offsets[i], len(years[i].uenbs)), len(years[i].uenbs))

            def on_year(jahr: int, summary: dict[str, Any]) -> None:
                i = current[0]
                shown.finish(years[i].key, summary, size=len(years[i].uenbs))
                current[0] = i + 1
                if i + 1 < len(years):
                    moves[0] = shown.start(years[i + 1].key)

            contributor = (
                Contributor(args.contributor, args.contributor_url) if args.contributor else None
            )
            export(
                data_dir,
                out_dir,
                args.year,
                progress=progress,
                on_year=on_year,
                contributor=contributor,
            )
    except ExportError as e:
        log.error("%s", e)
        return 1
    return 0


def _verify(args: argparse.Namespace) -> int:
    files = archived_files(_data_dir(args), include_incomplete=True)
    if not files:
        log.error("No archived files in %s", _data_dir(args))
        return 1
    failed = 0
    for archived in files:
        try:
            verify(archived)
        except ChecksumError as e:
            failed += 1
            log.error("%s", e)
        else:
            print(f"OK  {archived.path}")
    return 1 if failed else 0
