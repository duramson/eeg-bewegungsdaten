from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import httpx
import pytest

from eeg_bewegungsdaten.download import archive_path
from eeg_bewegungsdaten.links import (
    EARLIER_FILES,
    OLDER_FILES,
    PAGE_URL,
    UENB,
    DownloadLink,
    classify,
    find_links,
    newest_known_year,
    older_links,
    parse_links,
)
from tests.builders import PAGE


def links_on(page: str, *, older: bool = True) -> list[DownloadLink]:
    """`find_links` for a server that answers with `page`."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    with httpx.Client(transport=transport) as client:
        return find_links(client, older=older)


def test_parse_links_finds_all_four_tsos_and_the_legend() -> None:
    links = parse_links(PAGE)

    data = [link for link in links if link.kind == "bewegungsdaten"]
    assert [(link.uenb, link.year) for link in data] == [
        ("50hertz", 2025),
        ("amprion", 2025),
        ("tennet", 2025),
        ("transnetbw", 2025),
    ]
    legend = [link for link in links if link.kind == "legende"]
    assert len(legend) == 1
    assert legend[0].filename == "bewegungsdaten_legende.xlsx"
    assert legend[0].year is None


def test_parse_links_reads_file_id_name_and_date() -> None:
    first = parse_links(PAGE)[0]

    assert first.file_id == "0687a707-cf7c-45c1-29cf-08defc51206b"
    assert first.filename == "50hertz transmission gmbh eeg-zahlungen bewegungsdaten 2025.zip"
    assert first.published == dt.date(2026, 9, 4)


def test_parse_links_resolves_relative_urls() -> None:
    tennet = next(link for link in parse_links(PAGE) if link.uenb == "tennet")

    assert tennet.url.startswith("https://www.netztransparenz.de/cdn/files/ff4ee575-")
    assert tennet.published == dt.date(2026, 9, 5)


def test_parse_links_keeps_missing_date_empty() -> None:
    transnetbw = next(link for link in parse_links(PAGE) if link.uenb == "transnetbw")

    assert transnetbw.published is None


def test_parse_links_skips_duplicates_and_foreign_files(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        links = parse_links(PAGE)

    assert len(links) == 5
    assert not any(
        "stammdaten" in link.filename or link.filename.endswith(".pdf") for link in links
    )
    assert "ohne%20jahr" in caplog.text


@pytest.mark.parametrize(
    ("filename", "uenb", "year"),
    [
        ("50Hertz_Bewegungsdaten_2024.zip", "50hertz", 2024),
        ("TransnetBW GmbH EEG-Zahlungen Bewegungsdaten 2023.zip", "transnetbw", 2023),
        ("transnet-bw_bewegungsdaten_2026_20270910.zip", "transnetbw", 2026),
        ("TenneT TSO GmbH EEG-Zahlungen Bewegungsdaten 2025.ZIP", "tennet", 2025),
    ],
)
def test_classify_reads_tso_and_year_from_the_name(filename: str, uenb: str, year: int) -> None:
    link = classify(f"https://example.org/cdn/files/{filename}")

    assert link is not None
    assert (link.uenb, link.year, link.file_id) == (uenb, year, None)


def test_classify_rejects_names_with_two_tsos() -> None:
    assert classify("https://example.org/amprion_tennet_bewegungsdaten_2025.zip") is None


def test_classify_takes_the_year_from_the_folder_and_reads_the_part() -> None:
    link = classify(
        "https://www.netztransparenz.de/xspproxy/api/staticfiles/eeg-bewegungsdaten/2024/"
        "tennet%20tso%20gmbh%20eeg-zahlungen%20bewegungsdaten_teil%203.zip"
    )

    assert link is not None
    assert (link.uenb, link.year, link.part) == ("tennet", 2024, 3)


def test_classify_ignores_years_inside_longer_folder_names() -> None:
    url = "https://example.org/cdn/files/aaaa-2024-bbbb/archiv2023/tennet_bewegungsdaten_teil1.zip"

    assert classify(url) is None


def test_classify_without_part_has_none() -> None:
    link = classify("https://example.org/Amprion_Bewegungsdaten_2019.zip")

    assert link is not None
    assert link.part is None


def test_older_links_cover_every_tso_from_2016_on() -> None:
    links = older_links()
    data = [link for link in links if link.kind == "bewegungsdaten"]

    assert len(links) == len(EARLIER_FILES) + len(OLDER_FILES)
    assert {(link.year, link.uenb) for link in data if (link.year or 0) >= 2016} == {
        (year, uenb) for year in range(2016, newest_known_year() + 1) for uenb in UENB
    }
    assert any(link.kind == "legende" for link in links)
    assert not any(link.listed for link in links)
    assert all(link.published for link in links)
    assert len({(link.url, link.zip_member) for link in links}) == len(links)


def test_older_links_keep_the_superseded_uploads_apart() -> None:
    uploads: dict[tuple[int | None, str | None], dict[str, set[Path]]] = {}
    for link in older_links():
        if link.kind == "bewegungsdaten":
            folders = uploads.setdefault((link.year, link.uenb), {})
            folders.setdefault(link.upload_id or str(link.published), set()).add(
                archive_path(Path("data"), link).parent
            )

    tennet = [link for link in older_links() if (link.year, link.uenb) == (2024, "tennet")]
    assert [(str(link.published), link.part) for link in tennet] == [
        ("2025-09-16", 1),
        ("2025-09-16", 2),
        ("2025-09-16", 3),
        ("2025-12-08", 1),
        ("2025-12-08", 2),
        ("2025-12-08", 3),
        ("2025-12-08", 4),
    ]
    superseded = {key: dates for key, dates in uploads.items() if len(dates) > 1}
    assert {key: sorted(dates) for key, dates in superseded.items()} == {
        (2020, "transnetbw"): ["2021-07-30", "2021-08-05"],
        (2024, "tennet"): ["2025-09-16", "2025-12-08"],
    }
    # Each upload has one archive folder of its own, shared by its parts.
    for dates in uploads.values():
        assert all(len(folders) == 1 for folders in dates.values())
        assert len({folder for folders in dates.values() for folder in folders}) == len(dates)


def test_older_links_before_2021_come_from_the_wayback_machine() -> None:
    for link in older_links():
        wayback = link.url.startswith("https://web.archive.org/web/")
        assert wayback == (link.year is not None and link.year <= 2020 and not link.zip_member), (
            link.url
        )
        if wayback and (link.year or 0) >= 2016:
            assert "id_/https://www.netztransparenz.de/" in link.url


def test_files_before_2016_come_from_the_tso_sites_with_sha256() -> None:
    earlier = [
        link
        for link in older_links()
        if link.year is not None and link.year < 2016 and link.zip_member is None
    ]

    assert sorted((link.year, link.uenb) for link in earlier) == [
        (2011, "amprion"),
        (2011, "tennet"),
        (2012, "tennet"),
        (2013, "amprion"),
        (2013, "tennet"),
        (2014, "50hertz"),
        (2014, "tennet"),
        (2015, "50hertz"),
        (2015, "tennet"),
    ]
    sites = {"50hertz": "50hertz.com", "amprion": "amprion.net", "tennet": "tennet(tso.de|.eu)"}
    for link in earlier:
        wayback = (
            rf"https://web\.archive\.org/web/20\d{{12}}id_/https?://www\.{sites[str(link.uenb)]}/.+"
        )
        assert re.fullmatch(wayback, link.url)
        assert link.kind == "bewegungsdaten"
        assert link.filename.lower().endswith((".zip", ".csv"))
        assert link.file_id is None
        assert link.published
        assert not link.listed
        assert link.fallback_url is None
        assert link.sha256 is not None
        assert re.fullmatch(r"[0-9a-f]{64}", link.sha256)
    folders = {archive_path(Path("data"), link).parent for link in earlier}
    assert len(folders) == len(earlier)


def test_wayback_copies_of_files_on_the_server_have_url_and_sha256() -> None:
    with_copy = [link for link in older_links() if link.fallback_url]

    assert with_copy
    for link in older_links():
        if link.fallback_url is None:
            # Only the Wayback copies of the files before 2016 are checked without a fallback.
            assert (link.sha256 is not None) == (link.year is not None and link.year < 2016)
            continue
        assert link.url.startswith(
            ("https://www.netztransparenz.de/xspproxy/", "https://www.netztransparenz.de/cdn/")
        )
        assert re.fullmatch(
            r"https://web\.archive\.org/web/20\d{12}id_/" + re.escape(link.url), link.fallback_url
        )
        assert link.sha256 is not None
        assert re.fullmatch(r"[0-9a-f]{64}", link.sha256)
    assert len({link.sha256 for link in with_copy}) == len(with_copy)


def test_older_links_leave_out_the_files_on_the_page() -> None:
    page = parse_links(PAGE)
    older = older_links(page)

    assert not {link.url for link in older} & {link.url for link in page}
    assert not [link for link in older if link.year == 2025]
    assert [link for link in older_links() if link.year == 2025]


def test_find_links_loads_the_german_page() -> None:
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, text=PAGE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        links = find_links(client)

    assert [str(r.url) for r in requested] == [PAGE_URL]
    assert [link.url for link in links[:5]] == [link.url for link in parse_links(PAGE)]
    assert links[5:] == older_links(parse_links(PAGE))


def test_find_links_can_leave_out_older_files() -> None:
    links = links_on(PAGE, older=False)

    assert [link.url for link in links] == [link.url for link in parse_links(PAGE)]


def test_find_links_prefers_the_page_over_older_files() -> None:
    page = PAGE.replace("bewegungsdaten%202025.zip", "bewegungsdaten%202024.zip")

    links = links_on(page)

    data_2024 = [link for link in links if link.year == 2024]
    assert data_2024
    assert all(link.listed for link in data_2024)
    legends = [link.url for link in links if link.kind == "legende"]
    assert len(set(legends)) == len(legends)
    assert next(link for link in parse_links(page) if link.kind == "legende").url in legends


def test_find_links_fails_loudly_when_the_page_has_no_data_links() -> None:
    with pytest.raises(LookupError, match="No EEG-Bewegungsdaten links"):
        links_on("<html><a href='/x.pdf'>x</a></html>")


def test_find_links_raises_on_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        find_links(client)


def test_find_links_warns_about_a_year_it_does_not_know(caplog: pytest.LogCaptureFixture) -> None:
    later = newest_known_year() + 1
    page = PAGE.replace("bewegungsdaten%202025.zip", f"bewegungsdaten%20{later}.zip")

    with caplog.at_level(logging.WARNING):
        links_on(page)

    assert f"The page lists {later}, which this version" in caplog.text


def test_find_links_does_not_warn_about_known_years(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        links_on(PAGE)

    assert "does not know" not in caplog.text


def test_new_upload_does_not_inherit_an_older_uploads_fallback() -> None:
    page = PAGE.replace(
        "0687a707-cf7c-45c1-29cf-08defc51206b", "aaaaaaaa-1111-2222-3333-444444444444"
    )

    link = next(
        candidate
        for candidate in links_on(page)
        if candidate.year == 2025 and candidate.uenb == "50hertz"
    )

    assert link.fallback_url is None
    assert link.sha256 is None


def test_page_records_all_parts_of_a_new_upload_per_tso() -> None:
    page = "".join(
        f'<a href="https://example.org/2026/{uenb}_bewegungsdaten_teil{part}.zip">part</a>'
        for uenb, parts in (("tennet", (1, 2, 3)), ("amprion", (1, 2)))
        for part in parts
    )

    links = links_on(page, older=False)

    assert [(link.uenb, link.expected_parts) for link in links] == [
        *[("tennet", (1, 2, 3))] * 3,
        *[("amprion", (1, 2))] * 2,
    ]


def test_page_does_not_reduce_the_known_number_of_parts() -> None:
    part = next(
        candidate
        for candidate in older_links()
        if candidate.uenb == "tennet" and str(candidate.published) == "2025-12-08"
    )

    [link] = links_on(f'<a href="{part.url}">part 1</a>', older=False)

    assert link.expected_parts == (1, 2, 3, 4)


def test_parts_on_the_cdn_share_a_folder_per_tso() -> None:
    """Each part on the CDN has its own id, which cannot name the folder of the upload."""
    page = "".join(
        f'<a href="https://www.netztransparenz.de/cdn/files/{file_id}/'
        f'{uenb}%20eeg-zahlungen%20bewegungsdaten%202026_teil%20{part}.zip">part</a>'
        for uenb, part, file_id in (
            ("tennet", 1, "11111111-1111-1111-1111-111111111111"),
            ("tennet", 2, "22222222-2222-2222-2222-222222222222"),
            ("amprion", 1, "33333333-3333-3333-3333-333333333333"),
        )
    )

    links = links_on(page, older=False)

    folders: dict[str | None, set[Path]] = {}
    for link in links:
        folders.setdefault(link.uenb, set()).add(archive_path(Path("data"), link).parent)
    assert [link.expected_parts for link in links] == [(1, 2), (1, 2), (1,)]
    assert {uenb: len(found) for uenb, found in folders.items()} == {"tennet": 1, "amprion": 1}
    assert folders["tennet"] != folders["amprion"]


def test_zenodo_catalog_pins_each_member_and_groups_complementary_files() -> None:
    links = older_links()
    members = [link for link in links if link.zip_member]
    assert len(members) == 28
    assert len({link.zip_member for link in members}) == 28
    for link in members:
        assert link.size is not None
        assert link.size > 0
        assert link.sha256 is not None
        assert re.fullmatch(r"[0-9a-f]{64}", link.sha256)
        assert link.doi == "10.5281/zenodo.8010410"
        assert link.zip_member is not None
        assert "/Original files/" in link.zip_member
        assert not link.listed
    for year in (2013, 2014):
        parts = [link for link in links if (link.year, link.uenb) == (year, "amprion")]
        assert sorted(link.part for link in parts if link.part) == [1, 2]
        assert all(link.expected_parts == (1, 2) for link in parts)
        assert len({archive_path(Path("data"), link).parent for link in parts}) == 1


def test_zenodo_covers_every_tso_from_2007_to_2010() -> None:
    assert {
        (link.year, link.uenb)
        for link in older_links()
        if link.year is not None and 2007 <= link.year <= 2010
    } == {(year, tso) for year in range(2007, 2011) for tso in UENB}
