"""Find the download links on the netztransparenz.de page, plus the files of earlier years.

The file URLs contain a CDN id that changes with every upload, so they are read from the page
instead of being hard-coded. TSO and year come from the file name.

The page only lists the latest year. Older files are in `OLDER_FILES`: 2021 to 2024 are still
on the server without a link, 2016 to 2020 only survive in the Wayback Machine. Where the Wayback
Machine also has a copy of a 2021 to 2024 file, `download` falls back to it once the server
answers 404. `OLDER_FILES` lists the latest year too, for the time after the page moves on.

Before 2016 each TSO published on its own site. `EARLIER_FILES` holds Wayback copies and
members of the Zenodo Payment Register, with explicit TSO, year and integrity checks.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from eeg_bewegungsdaten.http import new_client

log = logging.getLogger(__name__)

PAGE_URL = (
    "https://www.netztransparenz.de/de-de/Erneuerbare-Energien-und-Umlagen/EEG/"
    "EEG-Abrechnungen/EEG-Jahresabrechnungen/EEG-Bewegungsdaten"
)

UENB = ("50hertz", "amprion", "tennet", "transnetbw")

Kind = Literal["bewegungsdaten", "legende"]

_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
_FILE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_PART = re.compile(r"teil\s*(\d+)")


@dataclass(frozen=True)
class DownloadLink:
    """One data file or legend, from the page or from `OLDER_FILES`."""

    url: str
    filename: str
    kind: Kind
    uenb: str | None
    year: int | None
    file_id: str | None
    published: dt.date | None = None
    part: int | None = None
    """Number of the part when a TSO splits one year into several ZIPs."""
    listed: bool = True
    """False for the older files in `OLDER_FILES`, which the page no longer links."""
    fallback_url: str | None = None
    """Copy in the Wayback Machine, downloaded instead when `url` answers 404."""
    sha256: str | None = None
    """SHA-256 of the file as archived before. The copy at `fallback_url` must match it, or
    `url` itself when that is the only copy (`EARLIER_FILES`)."""
    expected_parts: tuple[int, ...] = ()
    """All part numbers of this upload, from the page or the known file catalog."""
    part_urls: tuple[str, ...] = ()
    """URLs of all parts of this upload."""
    zip_member: str | None = None
    """Member to extract from a remote ZIP using HTTP ranges."""
    size: int | None = None
    """Expected size of the extracted member in bytes."""
    doi: str | None = None
    """DOI of the dataset that supplies this member."""
    upload_id: str | None = None
    """Explicit group for complementary files from different source URLs."""


def classify(url: str, published: dt.date | None = None) -> DownloadLink | None:
    """Turn a URL into a `DownloadLink`, or return None if it is not one of ours."""
    path = urlsplit(url).path
    filename = unquote(path.rsplit("/", 1)[-1])
    name = filename.lower()
    match = _FILE_ID.search(path)
    file_id = match.group(0).lower() if match else None

    if "legende" in name and name.endswith(".xlsx"):
        return DownloadLink(url, filename, "legende", None, None, file_id, published)
    if "bewegungsdaten" not in name or not name.endswith(".zip"):
        return None

    compact = re.sub(r"[^a-z0-9]", "", name)
    uenb = [u for u in UENB if u in compact]
    years = _YEAR.findall(name)
    if not years:
        # TenneT 2024: "... bewegungsdaten_teil 1.zip" in a folder named "2024".
        years = [s for s in unquote(path).split("/")[:-1] if re.fullmatch(r"20\d\d", s)]
    if len(uenb) != 1 or len(set(years)) != 1:
        log.warning("Skipping %s: cannot tell TSO and year from the file name", url)
        return None
    part = _PART.search(name)
    return DownloadLink(
        url,
        filename,
        "bewegungsdaten",
        uenb[0],
        int(years[0]),
        file_id,
        published,
        part=int(part.group(1)) if part else None,
    )


class _LinkParser(HTMLParser):
    """Collect hrefs and the publication date shown in the same list entry."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, dt.date | None]] = []
        self._date: dt.date | None = None
        self._in_date = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "li":
            self._date = None
        if attributes.get("data-fc-editor") == "file-item-date":
            self._in_date = True
        if tag == "a" and attributes.get("href"):
            self.found.append((attributes["href"] or "", self._date))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("strong", "span", "div"):
            self._in_date = False

    def handle_data(self, data: str) -> None:
        if not self._in_date:
            return
        match = _DATE.search(data)
        if match:
            day, month, year = (int(g) for g in match.groups())
            try:
                self._date = dt.date(year, month, day)
            except ValueError:
                self._date = None


def parse_links(html: str, base_url: str = PAGE_URL) -> list[DownloadLink]:
    """Return the data and legend files linked on the page, in page order, without duplicates."""
    parser = _LinkParser()
    parser.feed(html)
    parser.close()

    links: list[DownloadLink] = []
    seen: set[str] = set()
    for href, published in parser.found:
        url = urljoin(base_url, href)
        link = classify(url, published)
        if link is None or url in seen:
            continue
        seen.add(url)
        links.append(link)
    return links


_STATIC = (
    "https://www.netztransparenz.de/xspproxy/api/staticfiles/ntp-relaunch/dokumente/"
    "erneuerbare%20energien%20und%20umlagen/"
)
_STATIC_2023 = _STATIC + "eeg/eeg-abrechnungen/eeg-jahresabrechnungen/eeg-jahresabrechnungen/"
_CDN = "https://www.netztransparenz.de/cdn/files/"
_WAYBACK_ID = "https://web.archive.org/web/{}id_/"
_WAYBACK = _WAYBACK_ID + "https://www.netztransparenz.de/portals/1/"
_WAYBACK_2016 = _WAYBACK + "Content/Erneuerbare-Energien-Gesetz/Jahresabrechnungen/"

# Entries: (URL, Last-Modified date of the file[, Wayback timestamp, SHA-256]), checked on 18 and
# 19 October 2026. The Wayback capture of that timestamp has the same bytes as the file, whose
# SHA-256 follows ("id_" returns the original bytes). Superseded uploads are kept for comparison
# (TransnetBW 2020 of July 2021, TenneT 2024 in three parts); `ingest` reads the newest one.
OLDER_FILES: tuple[tuple[str, str] | tuple[str, str, str, str], ...] = (
    (_WAYBACK_2016.format("20190719111339") + "50Hertz_Bewegungsdaten_2016.zip", "2017-11-30"),
    (_WAYBACK_2016.format("20190719111528") + "Amprion_Bewegungsdaten_2016.zip", "2017-10-27"),
    (_WAYBACK_2016.format("20190719111731") + "TenneT_Bewegungsdaten_2016.zip", "2017-10-27"),
    (_WAYBACK_2016.format("20190719111938") + "TransnetBW_Bewegungsdaten_2016.zip", "2017-10-27"),
    (_WAYBACK.format("20190719112929") + "50Hertz_Bewegungsdaten_2017.zip", "2018-09-14"),
    (_WAYBACK.format("20190719113026") + "Amprion_Bewegungsdaten_2017.zip", "2018-09-14"),
    (_WAYBACK.format("20190719113228") + "TenneT_Bewegungsdaten_2017.zip", "2018-09-14"),
    (_WAYBACK.format("20190719113444") + "TransnetBW_Bewegungsdaten_2017.zip", "2018-09-14"),
    (_WAYBACK.format("20220326042741") + "50Hertz_Bewegungsdaten_2018.zip", "2019-08-02"),
    (_WAYBACK.format("20220326042739") + "Amprion_Bewegungsdaten_2018.zip", "2019-08-02"),
    (_WAYBACK.format("20220326042739") + "TenneT_Bewegungsdaten_2018.zip", "2019-08-02"),
    (_WAYBACK.format("20220326042739") + "TransnetBW_Bewegungsdaten_2018.zip", "2019-08-02"),
    (_WAYBACK.format("20220326043008") + "50Hertz_Bewegungsdaten_2019.zip", "2020-07-31"),
    (_WAYBACK.format("20220326043008") + "Amprion_Bewegungsdaten_2019.zip", "2020-07-31"),
    (_WAYBACK.format("20220326043013") + "TenneT_Bewegungsdaten_2019.zip", "2020-07-31"),
    (_WAYBACK.format("20220326043009") + "TransnetBW_Bewegungsdaten_2019.zip", "2020-07-31"),
    (
        _WAYBACK.format("20220326051809")
        + "50Hertz%20Transmission%20GmbH%20EEG-Zahlungen%20Bewegungsdaten%202020.zip",
        "2021-07-30",
    ),
    (
        _WAYBACK.format("20220326051810")
        + "Amprion%20GmbH%20EEG-Zahlungen%20Bewegungsdaten%202020.zip",
        "2021-07-30",
    ),
    (
        _WAYBACK.format("20220326051810")
        + "TenneT%20TSO%20GmbH%20EEG-Zahlungen%20Bewegungsdaten%202020.zip",
        "2021-07-30",
    ),
    (
        _WAYBACK.format("20210803182320")
        + "TransnetBW%20GmbH%20EEG-Zahlungen%20Bewegungsdaten%202020.zip",
        "2021-07-30",
    ),
    (
        _WAYBACK.format("20220401055329")
        + "TransnetBW%20GmbH%20EEG-Zahlungen%20Bewegungsdaten%202020.zip",
        "2021-08-05",
    ),
    (
        _STATIC
        + "eeg-jahresabrechnungen/2021/50hertztransmissiongmbheeg-zahlungenbewegungsdaten2021.zip",
        "2023-09-13",
        "20261018122716",
        "b134b326062638b1abff3d7522ca54e45b78e9dfd6b0f5f81e1e06520b448f2a",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2021/ampriongmbheeg-zahlungenbewegungsdaten2021.zip",
        "2023-09-13",
        "20261018125820",
        "709efcbb19c8d18bb8f195256d89c8fe7298e1c0fe5674e89a56fdcf6424e217",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2021/tennettsogmbheeg-zahlungenbewegungsdaten2021.zip",
        "2023-09-13",
        "20261018130053",
        "7aad1ca45aa6271bc2d44b1c33788341091e0a89b43c490bb9de342583f0a840",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2021/transnetbwgmbheeg-zahlungenbewegungsdaten2021.zip",
        "2023-09-13",
        "20261018130335",
        "7ee9de0e8db734c604c28ccf3865a2344382c1c677c6c52d4b67dd91b6b1f45f",
    ),
    (
        _STATIC
        + "eeg-jahresabrechnungen/2022/50hertztransmissiongmbheeg-zahlungenbewegungsdaten2022.zip",
        "2023-09-13",
        "20240927075903",
        "8e9d78eafe3ab4e9007a7ae959fcab3d7bd54d3adf67185c7b756cd261873ab6",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2022/ampriongmbheeg-zahlungenbewegungsdaten2022.zip",
        "2023-09-13",
        "20250909082600",
        "6256d028e3d86dde388e21ba8624e66d8273c1d589d82d3ffe6e38873719d662",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2022/tennettsogmbheeg-zahlungenbewegungsdaten2022.zip",
        "2023-09-13",
        "20250909082537",
        "675e225a9e63ba3384b2361f8c6cd318605c1a25c12a50f39c080e5f13fcecf0",
    ),
    (
        _STATIC
        + "eeg-jahresabrechnungen/2022/transnetbwgmbheeg-zahlungenbewegungsdaten2022_v02.zip",
        "2023-09-27",
        "20240224203126",
        "b421ded0a918df33b5c5a579dce036342cf12c47dbbcead5e9967b17d71b0816",
    ),
    (
        _STATIC_2023 + "50hertz_transmission_gmbh_eeg-zahlungen_bewegungsdaten_2023.zip",
        "2024-09-17",
        "20261018130559",
        "b227f97048eadbdf00384b967ff1a10109dbe8b8b835ec284a25bdb97304c078",
    ),
    (
        _STATIC_2023 + "amprion_gmbh_eeg-zahlungen_bewegungsdaten_2023.zip",
        "2024-09-17",
        "20261018130927",
        "5f4d8cc585e015409295a79d58a43bdc97c31f50a674035ce1c41ae3017e5d10",
    ),
    (
        _STATIC_2023 + "tennet_tso_gmbh_eeg-zahlungen_bewegungsdaten_2023.zip",
        "2024-09-17",
        "20240920181702",
        "fedd4888227bd70307c42c48eba06d57b19a3027a4f8bc5cf7d3aac2877695e4",
    ),
    (
        _STATIC_2023 + "transnetbw_gmbh_eeg-zahlungen_bewegungsdaten_2023.zip",
        "2024-09-17",
        "20240920155501",
        "91253ac5c77177bf728c46e041c83fe7bacb0798e0eae4eee831d46a5c593a4f",
    ),
    (
        _STATIC + "eeg/eeg-abrechnungen/eeg-jahresabrechnungen/bewegungsdaten_legende.xlsx",
        "2024-09-16",
        "20250709182314",
        "d2fbcc7971b7edc41bd9c5887ac116eef88dd4fa2321ea091d04dc7cc0b8a4c3",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/"
        "50hertz%20transmission%20gmbh%20eeg-zahlungen%20bewegungsdaten%202024.zip",
        "2025-09-15",
        "20261018131218",
        "17ed5f4b8ee5ad25d7a9193aa439364b0091d4a8f88359b7576e863dac9232ac",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/"
        "amprion%20gmbh%20eeg-zahlungen%20bewegungsdaten%202024.zip",
        "2025-09-16",
        "20261019144659",
        "1c799d25d866680c04bce46b80ea86df5d72cb40664adc00fe88720dd1110bb2",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%201.zip",
        "2025-09-16",
        "20250916135902",
        "958eb3895eab1828a54376e6d2a95b093ecceb75d579b16415c2e0097c8823e1",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%202.zip",
        "2025-09-16",
        "20250916135952",
        "eceb5f86ecf5c7f8f2808a06b17541ba3b90633dc45476af2fd6be41a3f506e9",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%203.zip",
        "2025-09-16",
        "20250916140007",
        "ec5321762e0f40cc5b154e9d2182fb8ec44a37add6b8527a2a7124e446baa60f",
    ),
    (
        _STATIC_2023 + "eeg-bewegungsdaten/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%201.zip",
        "2025-12-08",
        "20261018133658",
        "3570cec5c8f00d9ed5527e70f06bccdf6568739916ded39e7ee59aaa27c21465",
    ),
    (
        _STATIC_2023 + "eeg-bewegungsdaten/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%202.zip",
        "2025-12-08",
        "20261018133922",
        "d537e7250eda8ae61d4ce34a19adb7a5b2cfd02257eeea38d2f80c46c5446c1b",
    ),
    (
        _STATIC_2023 + "eeg-bewegungsdaten/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%203.zip",
        "2025-12-08",
        "20261018134213",
        "23432f18928664380cb7ee7008c3cf773aad78a1ca94dbf8c37361e8db6e8837",
    ),
    (
        _STATIC_2023 + "eeg-bewegungsdaten/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%204.zip",
        "2025-12-08",
        "20261018134405",
        "b9bc4162d78c0b88e26bbc7a08c482d0a791fb18b05fac3903af44cb752a1702",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/transnetbw_2024_eegzahlungen_bewegungsdaten.zip",
        "2025-09-15",
        "20250915165654",
        "82a819937591ed5947d8914072597c5c3d2fe91bd4c5ab767fac6eccce0c7537",
    ),
    (
        _STATIC + "eeg-jahresabrechnungen/2024/bewegungsdaten_legende.xlsx",
        "2025-09-15",
        "20250916091713",
        "0a166d5207801d52bfbd3c16594e58956854a41e78045fbf13bf2e3f2fd9ca2c",
    ),
    (
        _CDN + "0687a707-cf7c-45c1-29cf-08defc51206b/"
        "50hertz%20transmission%20gmbh%20eeg-zahlungen%20bewegungsdaten%202025.zip",
        "2026-09-04",
        "20260905131904",
        "9fa4da2375e1eb2581708ca9e88f0ecfe840fb8edc965560fc695f53f935abdd",
    ),
    (
        _CDN + "56b98c32-4c16-44ed-29d0-08defc51206b/"
        "amprion%20gmbh%20eeg-zahlungen%20bewegungsdaten%202025.zip",
        "2026-09-04",
        "20261019120641",
        "124ddd279f69430b9fd10360caab7a65187bb01fe2ac6b8db92f27b6b853ff44",
    ),
    (
        _CDN + "ff4ee575-7857-431e-f919-08def218fc48/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten%202025.zip",
        "2026-09-04",
        "20261019121000",
        "00f8324c5f44c1a45b890329fdc062d1aa7e4483895df6b8047342a56fa8724f",
    ),
    (
        _CDN + "a43d4021-c55e-420b-29d2-08defc51206b/"
        "transnetbw%20gmbh%20eeg-zahlungen%20bewegungsdaten%202025.zip",
        "2026-09-04",
        "20261019121430",
        "82540f1a3fc0cea75ca806824163d538048f4e383a63ed92e28b01b3779c49f6",
    ),
    (
        _CDN + "9d2cf962-999e-4810-29d3-08defc51206b/bewegungsdaten_legende.xlsx",
        "2026-09-04",
        "20261019121650",
        "db6dfc6aa97a84db265523219b43c2749ec2ec547e077fbd2f080562528e3ce7",
    ),
)

# Files before 2016: original Wayback copies plus the missing members held by Zenodo.
# SHA-256 and sizes refer to the archived file, after extraction for Zenodo members.
ZENODO_URL = "https://zenodo.org/api/records/8010410/files/PaymentRegister.zip/content"
ZENODO_DOI = "10.5281/zenodo.8010410"
EARLIER_FILES: tuple[DownloadLink, ...] = (
    DownloadLink(
        "https://web.archive.org/web/20150820204932id_/http://www.amprion.net/sites/default/files/zip/Bewegungsdaten_Internet.zip",
        "Bewegungsdaten_Internet.zip",
        "bewegungsdaten",
        "amprion",
        2011,
        None,
        published=dt.date.fromisoformat("2012-07-31"),
        listed=False,
        sha256="062ceb3a1c6d122d51aa4e113ddb2d74479aabc9e54d36cd6648d69730decbb7",
    ),
    DownloadLink(
        "https://web.archive.org/web/20130326100131id_/http://www.tennettso.de/site/binaries/content/assets/eeg-kwk-g/eeg/eeg-daten/csv-datei-bewegungsdaten-nach-eeg-kategorien-2011.zip",
        "csv-datei-bewegungsdaten-nach-eeg-kategorien-2011.zip",
        "bewegungsdaten",
        "tennet",
        2011,
        None,
        published=dt.date.fromisoformat("2012-09-06"),
        listed=False,
        sha256="2d136855331435cc066b9387881b5aab00ac7b11ea8903e0d68ec7a592fbf9d4",
    ),
    DownloadLink(
        "https://web.archive.org/web/20150423152946id_/http://www.tennet.eu/de/fileadmin/downloads/Kunden/CSV-Datei_Bewegungsdaten__EEG-Kategorien_aus_EEG-Jahresmeldung_2012_.csv.zip",
        "CSV-Datei_Bewegungsdaten__EEG-Kategorien_aus_EEG-Jahresmeldung_2012_.csv.zip",
        "bewegungsdaten",
        "tennet",
        2012,
        None,
        published=dt.date.fromisoformat("2014-09-19"),
        listed=False,
        sha256="edd993727c9fb808a43bca547961c3d364a3d5b638241f08a6436a07a782a183",
    ),
    DownloadLink(
        "https://web.archive.org/web/20160128161534id_/http://www.amprion.net/sites/default/files/Internet_Bewegungsdaten_VGT_DV_2013.zip",
        "Internet_Bewegungsdaten_VGT_DV_2013.zip",
        "bewegungsdaten",
        "amprion",
        2013,
        None,
        published=dt.date.fromisoformat("2014-07-25"),
        listed=False,
        sha256="ce69bac72aaddf8a0fae43b9e223a8b69b37e88bf5237cada52949954a3fadcb",
        part=1,
        upload_id="amprion-2013-zenodo-8010410",
    ),
    DownloadLink(
        "https://web.archive.org/web/20220319125625id_/https://www.tennet.eu/fileadmin/user_upload/The_Electricity_Market/German_Market/EEG-KWKG/assets_reports_balancing/TenneT_Bewegungsdaten_2013.zip",
        "TenneT_Bewegungsdaten_2013.zip",
        "bewegungsdaten",
        "tennet",
        2013,
        None,
        published=dt.date.fromisoformat("2020-02-06"),
        listed=False,
        sha256="5c304f8bba7c6587deb60910a2da8057e43b57335e9f47f3372691ba17fc0b55",
    ),
    DownloadLink(
        "https://web.archive.org/web/20180201020739id_/http://www.50hertz.com/Portals/3/Content/Dokumente/EEG/Veroeffentlichung-EEG-Daten/EEG-Jahresabrechnung/EEG_JEA_2014.csv",
        "EEG_JEA_2014.csv",
        "bewegungsdaten",
        "50hertz",
        2014,
        None,
        published=dt.date.fromisoformat("2016-02-18"),
        listed=False,
        sha256="d99f184455a6eedbcad186d63ac3361120cf3ea5dd79de22c15dabd5c9cde3fa",
    ),
    DownloadLink(
        "https://web.archive.org/web/20220319125638id_/https://www.tennet.eu/fileadmin/user_upload/The_Electricity_Market/German_Market/EEG-KWKG/assets_reports_balancing/TenneT_Bewegungsdaten_2014.zip",
        "TenneT_Bewegungsdaten_2014.zip",
        "bewegungsdaten",
        "tennet",
        2014,
        None,
        published=dt.date.fromisoformat("2020-02-06"),
        listed=False,
        sha256="defe974361c81c62788a79288866a9fc4d3680e78f34d335958fd3581aae0eba",
    ),
    DownloadLink(
        "https://web.archive.org/web/20171127035812id_/http://www.50hertz.com/Portals/3/Content/Dokumente/EEG/Veroeffentlichung-EEG-Daten/50Hertz_Bewegungsdaten_2015%5B1%5D.csv",
        "50Hertz_Bewegungsdaten_2015[1].csv",
        "bewegungsdaten",
        "50hertz",
        2015,
        None,
        published=dt.date.fromisoformat("2016-07-29"),
        listed=False,
        sha256="4ccfafa9c85c9b80d653a22e4a3e88aa6df19b3c7fe6756f212bfd870ab09561",
    ),
    DownloadLink(
        "https://web.archive.org/web/20220319125615id_/https://www.tennet.eu/fileadmin/user_upload/The_Electricity_Market/German_Market/EEG-KWKG/assets_reports_balancing/JM_2015_Bewegungsdaten.zip",
        "JM_2015_Bewegungsdaten.zip",
        "bewegungsdaten",
        "tennet",
        2015,
        None,
        published=dt.date.fromisoformat("2020-02-06"),
        listed=False,
        sha256="9cce08910cf6673d37a90adb046ec79db9da2dd6eac1cba690391b729fddac00",
    ),
    DownloadLink(
        ZENODO_URL,
        "2011.csv",
        "bewegungsdaten",
        "transnetbw",
        2011,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="a43ee0a682b5411fa9492b2996a8aa49145da0ea5e4ca5109f6802ae05a7d867",
        zip_member="PaymentRegister/Transnet/Original files/2011.csv",
        size=43_475_407,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2012.csv",
        "bewegungsdaten",
        "transnetbw",
        2012,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="74696b4246033c417a873de45657846017a7a382c084bf9e659ebca07b98de8c",
        zip_member="PaymentRegister/Transnet/Original files/2012.csv",
        size=53_595_778,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2013.csv",
        "bewegungsdaten",
        "transnetbw",
        2013,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="a5016c4734064dbdbe504e2d8024b0429d8c304b10b8866b25586750decb7a2a",
        zip_member="PaymentRegister/Transnet/Original files/2013.csv",
        size=56_393_728,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2014.csv",
        "bewegungsdaten",
        "transnetbw",
        2014,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="35887d88f7388ceb13ece8e4ebbfe4b33da57d384300ad1872a10db973d63b7d",
        zip_member="PaymentRegister/Transnet/Original files/2014.csv",
        size=47_012_059,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2015.csv",
        "bewegungsdaten",
        "transnetbw",
        2015,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="57168a6ecfbd3cde2be9620dd6a28053a16c690367110dac2c2bd74f989550f5",
        zip_member="PaymentRegister/Transnet/Original files/2015.csv",
        size=60_426_565,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2010.csv",
        "bewegungsdaten",
        "50hertz",
        2010,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="c1a1037450a1b50bed03639fb0aa4e4c185710e60fcde52656c6c4b94c11917b",
        zip_member="PaymentRegister/Hertz/Original files/2010.csv",
        size=10_032_046,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2011.csv",
        "bewegungsdaten",
        "50hertz",
        2011,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="a531528beb9921bdd1a1a6d8164c6c346456b776572db6190e0b9704ff652fdf",
        zip_member="PaymentRegister/Hertz/Original files/2011.csv",
        size=22_609_007,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2012.csv",
        "bewegungsdaten",
        "50hertz",
        2012,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="e3d5216ac07d3991d7d8a15c8d18114d9727125f8946866c0cc9aee8ef12aa37",
        zip_member="PaymentRegister/Hertz/Original files/2012.csv",
        size=25_623_062,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2013.csv",
        "bewegungsdaten",
        "50hertz",
        2013,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="92a68e1f00574d1341502aa40586e7f747be539b01a0f1399f095064cc6a4764",
        zip_member="PaymentRegister/Hertz/Original files/2013.csv",
        size=33_321_965,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2012_a.txt",
        "bewegungsdaten",
        "amprion",
        2012,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="b49271194abddac76d5e35d174ff411137605a31da660eac9b115521b7d570c6",
        zip_member=("PaymentRegister/Amprion/Original files/Bewegungsdaten_2012/2012_a.txt"),
        size=45_963_879,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "Internet_Bewegungsdaten_VNNE_2013.csv",
        "bewegungsdaten",
        "amprion",
        2013,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="7155e8e1723594653fc156e1711c8c4d409aeb63676d23874b82997fd2a44914",
        zip_member=(
            "PaymentRegister/Amprion/Original files/Bewegungsdaten_2013/"
            "Internet_Bewegungsdaten_VNNE_2013.csv"
        ),
        size=24_645_074,
        doi=ZENODO_DOI,
        part=2,
        upload_id="amprion-2013-zenodo-8010410",
    ),
    DownloadLink(
        ZENODO_URL,
        "Bewegungsdaten_VGT_DV_2014.csv",
        "bewegungsdaten",
        "amprion",
        2014,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="3d9685e67cee7b093298c640d777fe8c3026f3ab1d5b7303e5b2c8f005261b9d",
        zip_member=(
            "PaymentRegister/Amprion/Original files/Bewegungsdaten_2014/"
            "Bewegungsdaten_VGT_DV_2014.csv"
        ),
        size=38_367_998,
        doi=ZENODO_DOI,
        part=1,
        upload_id="amprion-2014-zenodo-8010410",
    ),
    DownloadLink(
        ZENODO_URL,
        "Bewegungsdaten_VNNE_2014.csv",
        "bewegungsdaten",
        "amprion",
        2014,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="72174b96c732d5b49bddeea89efbf00cb14c44b5dc06a6f3233ac6f9ca2eabed",
        zip_member=(
            "PaymentRegister/Amprion/Original files/Bewegungsdaten_2014/"
            "Bewegungsdaten_VNNE_2014.csv"
        ),
        size=17_884_824,
        doi=ZENODO_DOI,
        part=2,
        upload_id="amprion-2014-zenodo-8010410",
    ),
    DownloadLink(
        ZENODO_URL,
        "2007.csv",
        "bewegungsdaten",
        "transnetbw",
        2007,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="bfd3dd70dd6a8289f414ef8227213a66d4865c298cb5cf32bb404dc81c105154",
        zip_member="PaymentRegister/Transnet/Original files/2007.csv",
        size=4_828_843,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2008.csv",
        "bewegungsdaten",
        "transnetbw",
        2008,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="d49e337be012e4bc8014963ca45da4ce396031d89f7e8321a12f71036d2aa720",
        zip_member="PaymentRegister/Transnet/Original files/2008.csv",
        size=6_429_844,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2009.csv",
        "bewegungsdaten",
        "transnetbw",
        2009,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="1a81a4879dc587010937c3a4260fe9f08e55511379b107ff6478c038b081fe53",
        zip_member="PaymentRegister/Transnet/Original files/2009.csv",
        size=8_245_137,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2010.csv",
        "bewegungsdaten",
        "transnetbw",
        2010,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="9ffb71448d7f13f2d109d47930bf0dec3bd5ab129f596ed4b6103d12f65a862f",
        zip_member="PaymentRegister/Transnet/Original files/2010.csv",
        size=11_178_287,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2007.csv",
        "bewegungsdaten",
        "tennet",
        2007,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="dcee3ca548534714a2a0df9246ea8e30eae7ab0148d4bdda02164ed09aef93d1",
        zip_member="PaymentRegister/Tennet/Original files/2007.csv",
        size=21_065_709,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2008.csv",
        "bewegungsdaten",
        "tennet",
        2008,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="244a8bb00b7f71f4d517283ecbaf239031d7fd24c31481af025dbe7729060c6a",
        zip_member="PaymentRegister/Tennet/Original files/2008.csv",
        size=41_684_448,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2009.csv",
        "bewegungsdaten",
        "tennet",
        2009,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="35b3a02e8be2f79ae1bb0a0b70e2ac744dd173f8ac3f6f46d80bfea2ea6d92e0",
        zip_member="PaymentRegister/Tennet/Original files/2009.csv",
        size=58_849_033,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2010.csv",
        "bewegungsdaten",
        "tennet",
        2010,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="efbb2199c81d9564a53e397f0e7bea5ce97e6cb27f940158c3d6d2d5f99739aa",
        zip_member="PaymentRegister/Tennet/Original files/2010.csv",
        size=89_537_767,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2007.csv",
        "bewegungsdaten",
        "50hertz",
        2007,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="3c6a4e745a19c6cafa6d77dcbc09137b884b57fb9fcf191b3e0d5011701973e3",
        zip_member="PaymentRegister/Hertz/Original files/2007.csv",
        size=4_129_865,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2008.csv",
        "bewegungsdaten",
        "50hertz",
        2008,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="b522a6f7f884b26a7f13cd6d2b1b4288cefc2d5fc5577fcfe6a77f0edab48ee1",
        zip_member="PaymentRegister/Hertz/Original files/2008.csv",
        size=5_462_346,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2009.csv",
        "bewegungsdaten",
        "50hertz",
        2009,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="9accab59a5962ced0b575d0cfc6a014df317f408b86521afb00c06b19f273ec4",
        zip_member="PaymentRegister/Hertz/Original files/2009.csv",
        size=7_408_833,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2007_a.txt",
        "bewegungsdaten",
        "amprion",
        2007,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="08a1af5d49fba3185774c7d9591d038e3efe74443327a4256de5129142766b60",
        zip_member="PaymentRegister/Amprion/Original files/Bewegungsdaten_2007/2007_a.txt",
        size=15_195_083,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2008_a.txt",
        "bewegungsdaten",
        "amprion",
        2008,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="8e1ae702d97d087daa1fc4b71db063c43d4002c536f454e0cf3a337913bab69c",
        zip_member="PaymentRegister/Amprion/Original files/Bewegungsdaten_2008/2008_a.txt",
        size=17_856_398,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2009_a.txt",
        "bewegungsdaten",
        "amprion",
        2009,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="297505c8bd435a91f9a101c150d795f43cae59ade75da75df7226bd4ab4183b3",
        zip_member="PaymentRegister/Amprion/Original files/Bewegungsdaten_2009/2009_a.txt",
        size=25_449_952,
        doi=ZENODO_DOI,
    ),
    DownloadLink(
        ZENODO_URL,
        "2010_a.txt",
        "bewegungsdaten",
        "amprion",
        2010,
        None,
        published=dt.date(2023, 6, 6),
        listed=False,
        sha256="28af6619e97ac5c278783a52a3575e8abdc889245e6d73af635cef8a803d7aaf",
        zip_member="PaymentRegister/Amprion/Original files/Bewegungsdaten_2010/2010_a.txt",
        size=39_525_790,
        doi=ZENODO_DOI,
    ),
)


def newest_known_year() -> int:
    """The newest year in `OLDER_FILES`. Files of later years may come in a format that this
    version cannot read yet."""
    return max(link.year for link in older_links() if link.year is not None)


def older_links(listed: Iterable[DownloadLink] = ()) -> list[DownloadLink]:
    """The files in `EARLIER_FILES` and `OLDER_FILES`, except those the page lists: data files
    of a (year, TSO) in `listed`, and any file with the URL of a link in `listed`."""
    listed = list(listed)
    on_page = {(link.year, link.uenb) for link in listed if link.kind == "bewegungsdaten"}
    urls = {link.url for link in listed}
    links = list(EARLIER_FILES)
    for url, published, *copy in OLDER_FILES:
        link = classify(url, dt.date.fromisoformat(published))
        assert link is not None, url
        if url in urls or (link.kind == "bewegungsdaten" and (link.year, link.uenb) in on_page):
            continue
        link = dataclasses.replace(link, listed=False)
        if copy:
            timestamp, sha256 = copy
            link = dataclasses.replace(
                link, fallback_url=_WAYBACK_ID.format(timestamp) + url, sha256=sha256
            )
        links.append(link)
    return _group_parts(links)


def _group_parts(links: list[DownloadLink]) -> list[DownloadLink]:
    """Give each part the numbers and URLs of all parts of its upload, before a failed download
    can leave only some of them."""
    groups: dict[tuple[int | None, str | None, str | None], tuple[set[int], set[str]]] = {}
    for link in links:
        if link.part is not None:
            parts, urls = groups.setdefault(_upload_key(link), (set(), set()))
            parts.update((*link.expected_parts, link.part))
            urls.update((*link.part_urls, link.url))
    return [
        dataclasses.replace(
            link,
            expected_parts=tuple(sorted(groups[_upload_key(link)][0])),
            part_urls=tuple(sorted(groups[_upload_key(link)][1])),
        )
        if link.part is not None
        else link
        for link in links
    ]


def _upload_key(link: DownloadLink) -> tuple[int | None, str | None, str | None]:
    """Parts without a CDN id belong together if they share their server folder. On the CDN each
    part has its own id; as the page lists one upload per year and TSO, all parts of a year and
    TSO belong together."""
    folder = link.upload_id or (None if link.file_id else link.url.rsplit("/", 1)[0])
    return link.year, link.uenb, folder


def find_links(client: httpx.Client | None = None, *, older: bool = True) -> list[DownloadLink]:
    """Load the page and return its download links, followed by `older_links()` for the files
    the page does not list. Warns if the page lists a year after `newest_known_year()`."""
    http = client or new_client()
    try:
        response = http.get(PAGE_URL)
        response.raise_for_status()
    finally:
        if client is None:
            http.close()
    catalog = {link.url: link for link in older_links()}
    links = [
        dataclasses.replace(
            link,
            fallback_url=catalog[link.url].fallback_url,
            sha256=catalog[link.url].sha256,
            expected_parts=catalog[link.url].expected_parts,
        )
        if link.url in catalog
        else link
        for link in parse_links(response.text, str(response.url))
    ]
    links = _group_parts(links)
    if not any(link.kind == "bewegungsdaten" for link in links):
        raise LookupError(f"No EEG-Bewegungsdaten links found on {PAGE_URL}")
    known = newest_known_year()
    for year in sorted({link.year for link in links if link.year and link.year > known}):
        log.warning(
            "The page lists %d, which this version of eeg-bewegungsdaten does not know yet. "
            "If ingest stops with a FormatError, update eeg-bewegungsdaten.",
            year,
        )
    if older:
        links += older_links(links)
    return links
