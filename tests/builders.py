"""Small hand-made ZIP files that copy the quirks of the real TSO files, small open-MaStR
databases, and helpers shared by the tests.

All IDs are made up; they only follow the shape of the real ones. The only real numbers are the
MaStR numbers of the four TSOs in `UENB_NR`, which the checks compare against.
"""

from __future__ import annotations

import datetime as dt
import io
import sqlite3
import threading
import zipfile
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

import httpx
import polars as pl

from eeg_bewegungsdaten._version import CHECKOUT_PATH
from eeg_bewegungsdaten.download import ArchivedFile, download
from eeg_bewegungsdaten.links import DownloadLink, classify
from eeg_bewegungsdaten.quality import check
from eeg_bewegungsdaten.reader import iter_chunks, to_text

PAGE = (Path(__file__).parent / "fixtures" / "page.html").read_text(encoding="utf-8")

HEADER = (
    "EEG_Mastr_Nr;EEG_Anlagenschluessel;NB_Mastr_Nr;UENB_Mastr_Nr;Veraeusserungsform;"
    "Verguetungskategorie;Strommenge;EEG_Zahlung;EEG_Einnahmen;Monat"
)
# Amprion "ausgefoerdert": two empty extra columns in header and rows.
HEADER_AMPRION_EXTRA = HEADER + ";;"
# TenneT: BDEW number and name of the grid operator.
HEADER_TENNET = (
    "EEG_Mastr_Nr;EEG_Anlagenschluessel;NB_BNR;NB_Mastr_Nr;Netzbetreiber;UENB_Mastr_Nr;"
    "Veraeusserungsform;Verguetungskategorie;Strommenge;EEG_Zahlung;EEG_Einnahmen;Monat"
)
# TransnetBW: hyphen in part 1, "(Code)" in part 2.
HEADER_TRANSNETBW_1 = HEADER.replace("EEG_Anlagenschluessel", "EEG-Anlagenschluessel")
HEADER_TRANSNETBW_2 = HEADER_TRANSNETBW_1.replace("Veraeusserungsform", "Veraeusserungsform (Code)")

UENB_NR = {
    "50hertz": "SNB982046657236",
    "amprion": "SNB976890256486",
    "tennet": "SNB970033313272",
    "transnetbw": "SNB984944755380",
}
NB = "SNB900000000001"
KEY = "E90000000000000000000000000000001"
FILE_ID = "0687a707-cf7c-45c1-29cf-08defc51206b"
COMMIT = "9" * 40
# TenneT 2024 came in parts without a CDN id; the folder on the server holds the year.
TENNET_PART = (
    "https://www.netztransparenz.de/xspproxy/api/staticfiles/ntp-relaunch/dokumente/"
    "{folder}/tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%20{part}.zip"
)


def row(
    mastr_nr: str = "EEG900000000001",
    *,
    key: str = KEY,
    nb: str = NB,
    uenb: str = "amprion",
    form: str = "1",
    category: str = "SgK4820--Feb24",
    energy: str = "8586",
    payment: str = "1128,2",
    income: str = "0",
    month: str = "0",
) -> str:
    """One line in the 10-column layout shared by 50Hertz, Amprion and TransnetBW."""
    return ";".join(
        [mastr_nr, key, nb, UENB_NR[uenb], form, category, energy, payment, income, month]
    )


def csv(header: str, *lines: str, newline: str = "\r\n", bom: bool = False) -> bytes:
    text = newline.join([header, *lines]) + newline
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.write_bytes(zip_bytes(members))
    return path


def classified(url: str, published: dt.date | None = None) -> DownloadLink:
    link = classify(url, published)
    assert link is not None, url
    return link


class Clock:
    """Time that passes only while someone sleeps, for `HostLimits`; records the pauses."""

    def __init__(self) -> None:
        self.now = 0.0
        self.pauses: list[float] = []
        self._lock = threading.Lock()

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.pauses.append(seconds)
            self.now += seconds


def archive(
    data_dir: Path,
    members: dict[str, bytes],
    uenb: str = "amprion",
    year: int = 2025,
    file_id: str = FILE_ID,
) -> ArchivedFile:
    """Put a hand-made ZIP into the archive through the real download code."""
    body = zip_bytes(members)
    link = classified(
        f"https://www.netztransparenz.de/cdn/files/{file_id}/"
        f"{uenb}%20eeg-zahlungen%20bewegungsdaten%20{year}.zip"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    with httpx.Client(transport=transport) as client:
        return download(link, data_dir, client)


def read_zip(
    path: Path, uenb: str = "amprion", block_size: int = 1 << 20
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Run reader and checks over a whole ZIP and return (clean, quarantine)."""
    clean, quarantine = [], []
    for chunk in iter_chunks(path, block_size):
        c, q = check(to_text(chunk, jahr=2025, uenb=uenb))
        clean.append(c)
        quarantine.append(q)
    return pl.concat(clean), pl.concat(quarantine)


def read_lines(
    tmp_path: Path, *lines: str, uenb: str = "amprion"
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """`read_zip` of a ZIP with one CSV file in the 10-column layout."""
    path = make_zip(tmp_path / "t.zip", {"Bewegungsdaten.csv": csv(HEADER, *lines)})
    return read_zip(path, uenb)


# Columns of open-MaStR's tables, reduced to the ones used and one more each.
PLANT_COLUMNS = ("EegMastrNummer", "AnlagenschluesselEeg", "InstallierteLeistung", "DatumDownload")
UNIT_COLUMNS = ("EinheitMastrNummer", "EegMastrNummer", "Bruttoleistung", "DatumDownload")
DOWNLOAD_DATE = "20261017"


def plants(
    *rows: tuple[str | None, str | None],
) -> tuple[Sequence[str], list[tuple[str | None, ...]]]:
    """Rows of an `AnlagenEeg*` table from (EegMastrNummer, AnlagenschluesselEeg)."""
    return PLANT_COLUMNS, [(nr, key, "9.9", DOWNLOAD_DATE) for nr, key in rows]


def units(*rows: tuple[str, str | None]) -> tuple[Sequence[str], list[tuple[str | None, ...]]]:
    """Rows of an `Einheiten*` table from (EinheitMastrNummer, EegMastrNummer)."""
    return UNIT_COLUMNS, [(unit, eeg, "9.9", DOWNLOAD_DATE) for unit, eeg in rows]


def kwk_units(
    *rows: tuple[str, str | None, str | None],
) -> tuple[Sequence[str], list[tuple[str | None, ...]]]:
    """Rows of `EinheitenBiomasse` or `EinheitenGeothermie...`, which also have a KWK number, from
    (EinheitMastrNummer, EegMastrNummer, KwkMastrNummer)."""
    columns = (*UNIT_COLUMNS[:2], "KwkMastrNummer", *UNIT_COLUMNS[2:])
    return columns, [(unit, eeg, kwk, "9.9", DOWNLOAD_DATE) for unit, eeg, kwk in rows]


def deleted_units(*numbers: str) -> tuple[Sequence[str], list[tuple[str | None, ...]]]:
    """Rows of `GeloeschteUndDeaktivierteEinheiten` from EinheitMastrNummer."""
    columns = ("EinheitMastrNummer", "EinheitSystemstatus", "DatumDownload")
    return columns, [(nr, "Gelöscht", DOWNLOAD_DATE) for nr in numbers]


def make_mastr_db(
    path: Path, tables: dict[str, tuple[Sequence[str], list[tuple[str | None, ...]]]]
) -> Path:
    """SQLite file shaped like open-MaStR's: one table per MaStR file, text columns."""
    with closing(sqlite3.connect(path)) as con, con:
        for table, (columns, rows) in tables.items():
            definitions = ", ".join(f'"{c}" VARCHAR' for c in columns)
            con.execute(f'CREATE TABLE "{table}" ({definitions})')
            placeholders = ", ".join("?" * len(columns))
            con.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)
    return path


def fake_git(prefix: str = CHECKOUT_PATH, status: str | None = "") -> Callable[..., str | None]:
    """A stand-in for `_version._git`: a checkout at `COMMIT` with the package at `prefix`.
    `status` is the output of git status, None if it fails."""

    def git(cwd: Path, *args: str) -> str | None:
        return f"{prefix}\n{COMMIT}\n" if "rev-parse" in args else status

    return git
