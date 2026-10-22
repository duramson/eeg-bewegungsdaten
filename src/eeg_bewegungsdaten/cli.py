"""Command line interface: ``eeg-bewegungsdaten <command>``."""

from __future__ import annotations

import argparse
import logging
import zipfile
from collections.abc import Sequence
from pathlib import Path

import httpx

from eeg_bewegungsdaten import display
from eeg_bewegungsdaten._version import __version__
from eeg_bewegungsdaten.download import (
    ArchivedFile,
    ChecksumError,
    DownloadError,
    archive_path,
    archived_files,
    download,
    read_meta,
    verify,
)
from eeg_bewegungsdaten.export import ExportError, export
from eeg_bewegungsdaten.http import new_client
from eeg_bewegungsdaten.ingest import ingest, latest_uploads
from eeg_bewegungsdaten.links import UENB, DownloadLink, find_links
from eeg_bewegungsdaten.mastr import (
    MastrError,
    clean_files,
    default_db_path,
    link_file,
    load_bridge,
    load_register,
)
from eeg_bewegungsdaten.paths import ENV_VAR, default_data_dir, export_dir
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
    share.set_defaults(func=_export)

    check = commands.add_parser("verify", help="recompute SHA-256 of all archived files")
    check.set_defaults(func=_verify)
    return parser


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year", type=int, action="append", help="only this year (repeatable)")
    parser.add_argument("--uenb", choices=UENB, action="append", help="only this TSO (repeatable)")


def _data_dir(args: argparse.Namespace) -> Path:
    data_dir: Path = args.data_dir.expanduser() if args.data_dir else default_data_dir()
    return data_dir


def _wanted(args: argparse.Namespace, jahr: int | None, uenb: str | None) -> bool:
    """Whether `--year` and `--uenb` let this year and TSO through."""
    return (not args.year or jahr in args.year) and (not args.uenb or uenb in args.uenb)


def _links(args: argparse.Namespace) -> int:
    for link in find_links():
        published = link.published.isoformat() if link.published else "-"
        print(f"{link.year or '-'}\t{link.uenb or link.kind}\t{published}\t{link.url}")
    return 0


def _download(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    with new_client() as client:
        links = [
            link
            for link in find_links(client)
            if link.kind == "legende" or _wanted(args, link.year, link.uenb)
        ]
        if not any(link.kind == "bewegungsdaten" for link in links):
            log.error("No known data files match --year/--uenb")
            return 1
        archived = {link.url for link in links if _is_archived(data_dir, link)}
        with display.DownloadDisplay(
            args.console, links, archived, data_dir, verbose=args.verbose
        ) as shown:
            for link in links:
                log.debug("%s %s: %s", link.year or "-", link.uenb or link.kind, link.url)
                try:
                    result = download(link, data_dir, client, progress=shown.start(link.url))
                except (httpx.HTTPError, DownloadError) as e:
                    shown.failed(link, e)
                else:
                    shown.finished(link, result)
    return 1 if shown.failures else 0


def _is_archived(data_dir: Path, link: DownloadLink) -> bool:
    try:
        return read_meta(archive_path(data_dir, link)) is not None
    except DownloadError:
        return False


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
    try:
        register = load_register(args.db.expanduser() if args.db else default_db_path())
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
    bridge = load_bridge(data_dir, register)
    years = bridge.source["years"]
    log.info(
        "Bridge from the rows matched by number%s: %s Anlagenschluessel of one EEG plant, "
        "%s of several (not used)",
        f" of {years[0]} to {years[-1]}" if years else "",
        f"{bridge.source['keys']:,}",
        f"{bridge.source['keys_with_several_plants']:,}",
    )

    print(f"{'jahr':<6}{'uenb':<12}{'rows':>11}{'treffer':>9}{'zahlung':>9}  codes")
    for jahr, uenb in files:
        report = link_file(data_dir, jahr, uenb, register, bridge)
        rate = report["trefferquote"]
        codes = ", ".join(
            f"{code} {n['rows']:,}" for code, n in report["treffer"].items() if n["rows"]
        )
        print(
            f"{jahr:<6}{uenb:<12}{report['rows']:>11,}"
            f"{_percent(rate['rows']):>9}{_percent(rate['eeg_zahlung']):>9}  {codes}"
        )
    return 0


def _export(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    out_dir = args.out.expanduser() if args.out else export_dir(data_dir)
    try:
        sources = export(data_dir, out_dir, args.year)
    except ExportError as e:
        log.error("%s", e)
        return 1
    print(f"{'jahr':<6}{'rows':>12}{'anlage_id':>11}{'ohne_id':>10}{'treffer':>9}  file")
    for jahr, summary in sources["years"].items():
        print(
            f"{jahr:<6}{summary['rows']:>12,}{summary['anlage_ids']:>11,}"
            f"{summary['rows_without_anlage_id']:>10,}{_percent(summary['trefferquote']):>9}  "
            f"{out_dir / summary['file']}"
        )
    total = sum(summary["rows"] for summary in sources["years"].values())
    print(f"{'total':<6}{total:>12,}")
    return 0


def _percent(share: float | None) -> str:
    return "-" if share is None else f"{share:.1%}"


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
