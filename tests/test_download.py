from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from eeg_bewegungsdaten.download import (
    ATTEMPTS,
    RETRY_STATUS,
    ChecksumError,
    DownloadError,
    archive_path,
    archived_files,
    download,
    meta_path,
    read_meta,
    verify,
)
from eeg_bewegungsdaten.links import PAGE_URL, DownloadLink, find_links, older_links
from tests.builders import PAGE, TENNET_PART, classified, zip_bytes

URL = (
    "https://www.netztransparenz.de/cdn/files/0687a707-cf7c-45c1-29cf-08defc51206b/"
    "50hertz%20transmission%20gmbh%20eeg-zahlungen%20bewegungsdaten%202025.zip"
)


ZIP = zip_bytes({"Bewegungsdaten/Teil1.csv": b"EEG_Mastr_Nr;Monat\r\nEEG900000000001;0\r\n"})


@pytest.fixture
def link() -> DownloadLink:
    return classified(URL)


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The pauses before retries, recorded instead of waited for."""
    pauses: list[float] = []
    monkeypatch.setattr("eeg_bewegungsdaten.download.time.sleep", pauses.append)
    return pauses


class Server:
    """Serves one response per request and counts requests."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.respond(request)

        return httpx.Client(transport=httpx.MockTransport(handler))


def serve(content: bytes, status: int = 200, headers: dict[str, str] | None = None) -> Server:
    return Server(lambda request: httpx.Response(status, content=content, headers=headers))


def leftovers(data_dir: Path) -> list[str]:
    return sorted(p.name for p in data_dir.rglob("*") if p.is_file())


def test_download_writes_file_and_metadata(tmp_path: Path, link: DownloadLink) -> None:
    server = serve(ZIP, headers={"Last-Modified": "Thu, 04 Sep 2026 10:00:00 GMT"})

    with server.client() as client:
        result = download(link, tmp_path, client)

    expected_path = (
        tmp_path / "raw/2025/50hertz/0687a707-cf7c-45c1-29cf-08defc51206b" / link.filename
    )
    assert result.path == expected_path
    assert result.downloaded
    assert expected_path.read_bytes() == ZIP
    meta = json.loads(meta_path(expected_path).read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(ZIP).hexdigest()
    assert meta["size"] == len(ZIP)
    assert meta["url"] == URL
    assert meta["uenb"] == "50hertz"
    assert meta["year"] == 2025
    assert meta["part"] is None
    assert meta["page_url"] == PAGE_URL
    assert meta["last_modified"] == "Thu, 04 Sep 2026 10:00:00 GMT"
    assert meta["downloaded_at"].endswith("+00:00")
    assert leftovers(tmp_path) == sorted([link.filename, link.filename + ".meta.json"])


def test_download_skips_finished_files(tmp_path: Path, link: DownloadLink) -> None:
    server = serve(ZIP)
    with server.client() as client:
        download(link, tmp_path, client)
        again = download(link, tmp_path, client)

    assert len(server.requests) == 1
    assert not again.downloaded
    assert again.meta["sha256"] == hashlib.sha256(ZIP).hexdigest()


def test_download_repeats_when_metadata_is_missing(tmp_path: Path, link: DownloadLink) -> None:
    path = archive_path(tmp_path, link)
    path.parent.mkdir(parents=True)
    path.write_bytes(ZIP[:10])

    server = serve(ZIP)
    with server.client() as client:
        result = download(link, tmp_path, client)

    assert result.downloaded
    assert path.read_bytes() == ZIP


def test_download_repeats_when_file_size_changed(tmp_path: Path, link: DownloadLink) -> None:
    server = serve(ZIP)
    with server.client() as client:
        first = download(link, tmp_path, client)
        first.path.write_bytes(ZIP[:-5])
        assert read_meta(first.path) is None
        again = download(link, tmp_path, client)

    assert again.downloaded
    assert len(server.requests) == 2


def test_aborted_download_leaves_nothing_behind(tmp_path: Path, link: DownloadLink) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield ZIP[:20]
            raise httpx.ReadError("connection reset")

    server = Server(
        lambda r: httpx.Response(200, stream=Broken(), headers={"Content-Length": str(len(ZIP))})
    )
    with server.client() as client, pytest.raises(httpx.ReadError):
        download(link, tmp_path, client)

    assert leftovers(tmp_path) == []
    assert read_meta(archive_path(tmp_path, link)) is None


def held(monkeypatch: pytest.MonkeyPatch, times: int) -> list[float]:
    """Let renaming fail `times` times with PermissionError, as while a virus scanner holds the
    file; return the pauses in between."""
    replace = Path.replace
    calls: list[Path] = []
    pauses: list[float] = []

    def locked(self: Path, target: Path) -> Path:
        calls.append(target)
        if len(calls) <= times:
            raise PermissionError(13, "The process cannot access the file", str(self))
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr("eeg_bewegungsdaten.download.time.sleep", pauses.append)
    return pauses


def test_rename_is_retried_while_the_file_is_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, link: DownloadLink
) -> None:
    pauses = held(monkeypatch, times=2)

    with serve(ZIP).client() as client:
        result = download(link, tmp_path, client)

    assert result.path.read_bytes() == ZIP
    assert pauses == [0.2, 0.4]


def test_file_held_too_long_fails_and_leaves_nothing_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, link: DownloadLink
) -> None:
    pauses = held(monkeypatch, times=99)

    with serve(ZIP).client() as client, pytest.raises(PermissionError):
        download(link, tmp_path, client)

    assert len(pauses) == 4
    assert leftovers(tmp_path) == []


def test_short_body_is_rejected(tmp_path: Path, link: DownloadLink) -> None:
    class Short(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield ZIP[:-100]

    server = Server(
        lambda r: httpx.Response(200, stream=Short(), headers={"Content-Length": str(len(ZIP))})
    )
    with server.client() as client, pytest.raises(DownloadError, match="got"):
        download(link, tmp_path, client)

    assert leftovers(tmp_path) == []


def test_html_error_page_is_not_archived(tmp_path: Path, link: DownloadLink) -> None:
    server = serve(b"<html>Wartungsarbeiten</html>", headers={"Content-Type": "text/html"})

    with server.client() as client, pytest.raises(DownloadError, match="not a ZIP"):
        download(link, tmp_path, client)

    assert leftovers(tmp_path) == []


def test_corrupt_zip_is_not_archived(tmp_path: Path, link: DownloadLink) -> None:
    damaged = bytearray(ZIP)
    start = ZIP.index(b"Bewegungsdaten/Teil1.csv") + len("Bewegungsdaten/Teil1.csv")
    damaged[start + 5] ^= 0xFF
    server = serve(bytes(damaged))

    with server.client() as client, pytest.raises(DownloadError):
        download(link, tmp_path, client)

    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("status", [404, 500])
def test_http_errors_are_raised(tmp_path: Path, link: DownloadLink, status: int) -> None:
    server = serve(b"error", status=status)

    with server.client() as client, pytest.raises(httpx.HTTPStatusError):
        download(link, tmp_path, client)

    assert leftovers(tmp_path) == []


def once(first: httpx.Response | Exception) -> Server:
    """Fails the first request with `first`, then serves the ZIP."""

    def respond(request: httpx.Request) -> httpx.Response:
        if len(server.requests) == 1:
            if isinstance(first, Exception):
                raise first
            return first
        return httpx.Response(200, content=ZIP)

    server = Server(respond)
    return server


@pytest.mark.parametrize(
    "first",
    [
        httpx.ReadError("connection reset"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("peer closed connection"),
        httpx.Response(503, text="busy"),
        httpx.Response(429, text="slow down", headers={"Retry-After": "0"}),
    ],
    ids=["read-error", "read-timeout", "protocol-error", "503", "429"],
)
def test_transient_error_is_retried(
    tmp_path: Path, link: DownloadLink, waits: list[float], first: httpx.Response | Exception
) -> None:
    server = once(first)

    with server.client() as client:
        result = download(link, tmp_path, client)

    assert result.path.read_bytes() == ZIP
    assert len(server.requests) == 2
    assert len(waits) == 1


@pytest.mark.parametrize(("asked", "waited"), [("3", 3.0), ("600", 60.0)])
def test_retry_after_is_honoured_up_to_a_limit(
    tmp_path: Path, link: DownloadLink, waits: list[float], asked: str, waited: float
) -> None:
    server = once(httpx.Response(503, headers={"Retry-After": asked}))

    with server.client() as client:
        download(link, tmp_path, client)

    assert waits == [waited]


def test_persistent_error_fails_after_the_last_attempt(
    tmp_path: Path, link: DownloadLink, waits: list[float]
) -> None:
    server = serve(b"busy", status=503)

    with server.client() as client, pytest.raises(httpx.HTTPStatusError):
        download(link, tmp_path, client)

    assert len(server.requests) == ATTEMPTS
    assert waits == [1.0, 2.0, 4.0]
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("status", [400, 403, 404, 410])
def test_other_client_errors_are_not_retried(
    tmp_path: Path, link: DownloadLink, waits: list[float], status: int
) -> None:
    server = serve(b"error", status=status)

    with server.client() as client, pytest.raises(httpx.HTTPStatusError):
        download(link, tmp_path, client)

    assert len(server.requests) == 1
    assert waits == []


def test_retry_is_logged(
    tmp_path: Path, link: DownloadLink, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="eeg_bewegungsdaten.download")

    with once(httpx.Response(503)).client() as client:
        download(link, tmp_path, client)

    assert caplog.messages == [f"{URL}: HTTP 503, trying again in 1 s (attempt 2 of {ATTEMPTS})"]


def test_new_upload_is_archived_next_to_the_old_one(tmp_path: Path, link: DownloadLink) -> None:
    newer = classified(
        URL.replace("0687a707-cf7c-45c1-29cf-08defc51206b", "aaaaaaaa-1111-2222-3333-444444444444")
    )
    server = serve(ZIP)
    with server.client() as client:
        old = download(link, tmp_path, client)
        new = download(newer, tmp_path, client)

    assert old.path != new.path
    assert [f.path for f in archived_files(tmp_path)] == [old.path, new.path]


def test_legend_goes_to_its_own_folder(tmp_path: Path) -> None:
    legend = classified(
        "https://www.netztransparenz.de/cdn/files/9d2cf962-999e-4810-29d3-08defc51206b/"
        "bewegungsdaten_legende.xlsx"
    )

    assert archive_path(tmp_path, legend) == (
        tmp_path / "raw/legende/9d2cf962-999e-4810-29d3-08defc51206b/bewegungsdaten_legende.xlsx"
    )


def test_parts_of_one_upload_share_a_folder(tmp_path: Path) -> None:
    first, second = (
        archive_path(tmp_path, classified(TENNET_PART.format(folder="2024", part=part)))
        for part in (1, 2)
    )
    other = archive_path(tmp_path, classified(TENNET_PART.format(folder="neu/2024", part=1)))

    assert first.parent == second.parent
    assert first.parent.parent == tmp_path / "raw/2024/tennet"
    assert other.parent != first.parent


def test_files_without_cdn_id_get_one_folder_each(tmp_path: Path) -> None:
    base = "https://web.archive.org/web/20220326043008id_/https://www.netztransparenz.de/portals/1/"
    name = "Amprion_Bewegungsdaten_2019.zip"
    amprion = archive_path(tmp_path, classified(base + name))
    again = archive_path(tmp_path, classified(base + name))
    other = archive_path(tmp_path, classified(base.replace("043008", "043009") + name))

    assert amprion == again
    assert amprion.parent != other.parent


def test_metadata_records_part_and_missing_page_link(tmp_path: Path) -> None:
    part = classified(TENNET_PART.format(folder="2024", part=3))

    with serve(ZIP).client() as client:
        result = download(dataclasses.replace(part, listed=False), tmp_path, client)

    assert (result.meta["year"], result.meta["part"], result.meta["page_url"]) == (2024, 3, None)


def test_verify_detects_changed_content(tmp_path: Path, link: DownloadLink) -> None:
    with serve(ZIP).client() as client:
        archived = download(link, tmp_path, client)
    verify(archived)

    data = bytearray(archived.path.read_bytes())
    data[0] ^= 0xFF
    archived.path.write_bytes(bytes(data))

    with pytest.raises(ChecksumError):
        verify(archived)


LIVE_2022 = (
    "https://www.netztransparenz.de/xspproxy/api/staticfiles/ntp-relaunch/dokumente/"
    "erneuerbare%20energien%20und%20umlagen/eeg-jahresabrechnungen/2022/"
    "ampriongmbheeg-zahlungenbewegungsdaten2022.zip"
)
WAYBACK_2022 = "https://web.archive.org/web/20250909082600id_/" + LIVE_2022
ORIG_LAST_MODIFIED = "Wed, 13 Sep 2023 08:00:00 GMT"


def with_copy(sha256: str | None = hashlib.sha256(ZIP).hexdigest()) -> DownloadLink:
    return dataclasses.replace(
        classified(LIVE_2022), listed=False, fallback_url=WAYBACK_2022, sha256=sha256
    )


def live_gone(wayback: bytes = ZIP, live_status: int = 404) -> Server:
    """netztransparenz.de answers `live_status`, the Wayback Machine serves `wayback`."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "web.archive.org":
            return httpx.Response(
                200,
                content=wayback,
                headers={
                    "Content-Type": "application/x-zip-compressed",
                    "X-Archive-Orig-Last-Modified": ORIG_LAST_MODIFIED,
                },
            )
        return httpx.Response(live_status, content=b"error")

    return Server(respond)


def requested(server: Server) -> list[str]:
    return [str(request.url) for request in server.requests]


@pytest.mark.parametrize("sha256", [hashlib.sha256(ZIP).hexdigest(), None], ids=["sha256", "none"])
def test_file_gone_from_the_server_comes_from_the_wayback_copy(
    tmp_path: Path, sha256: str | None
) -> None:
    link = with_copy(sha256)
    server = live_gone()

    with server.client() as client:
        result = download(link, tmp_path, client)

    assert requested(server) == [LIVE_2022, WAYBACK_2022]
    assert result.downloaded
    assert result.path == archive_path(tmp_path, link)
    assert result.path.read_bytes() == ZIP
    assert result.meta["url"] == LIVE_2022
    assert result.meta["source_url"] == WAYBACK_2022
    assert result.meta["sha256"] == hashlib.sha256(ZIP).hexdigest()
    assert result.meta["orig_last_modified"] == ORIG_LAST_MODIFIED
    assert read_meta(result.path) == result.meta


def test_wayback_copy_with_other_bytes_is_not_archived(tmp_path: Path) -> None:
    other = zip_bytes({"Bewegungsdaten/Teil1.csv": b"EEG_Mastr_Nr;Monat\r\nEEG900000000002;0\r\n"})
    server = live_gone(other)

    with server.client() as client, pytest.raises(DownloadError, match="SHA-256"):
        download(with_copy(), tmp_path, client)

    assert requested(server) == [LIVE_2022, WAYBACK_2022]
    assert leftovers(tmp_path) == []


def test_file_on_the_server_does_not_touch_the_wayback_copy(tmp_path: Path) -> None:
    server = serve(ZIP, headers={"Last-Modified": "Wed, 13 Sep 2023 08:00:00 GMT"})

    with server.client() as client:
        result = download(with_copy(), tmp_path, client)

    assert requested(server) == [LIVE_2022]
    assert result.meta["source_url"] == LIVE_2022
    assert result.meta["orig_last_modified"] is None


@pytest.mark.parametrize("status", [403, 410, 500, 503])
def test_only_404_leads_to_the_wayback_copy(tmp_path: Path, status: int) -> None:
    server = live_gone(live_status=status)

    with server.client() as client, pytest.raises(httpx.HTTPStatusError):
        download(with_copy(), tmp_path, client)

    # A 5xx is tried again on the same URL, but never leads to the Wayback copy
    assert requested(server) == [LIVE_2022] * (ATTEMPTS if status in RETRY_STATUS else 1)
    assert leftovers(tmp_path) == []


def test_missing_wayback_copy_is_an_error(tmp_path: Path) -> None:
    server = serve(b"not found", status=404)

    with server.client() as client, pytest.raises(httpx.HTTPStatusError) as raised:
        download(with_copy(), tmp_path, client)

    assert str(raised.value.request.url) == WAYBACK_2022
    assert requested(server) == [LIVE_2022, WAYBACK_2022]
    assert leftovers(tmp_path) == []


def test_404_without_wayback_copy_is_an_error(tmp_path: Path) -> None:
    link = dataclasses.replace(with_copy(), fallback_url=None)
    server = live_gone()

    with server.client() as client, pytest.raises(httpx.HTTPStatusError):
        download(link, tmp_path, client)

    assert requested(server) == [LIVE_2022]


def test_wayback_download_records_the_original_date(tmp_path: Path) -> None:
    link = classified(
        "https://web.archive.org/web/20220326043008id_/https://www.netztransparenz.de/portals/1/"
        "Amprion_Bewegungsdaten_2019.zip"
    )
    server = live_gone()

    with server.client() as client:
        result = download(link, tmp_path, client)

    assert result.meta["url"] == result.meta["source_url"] == link.url
    assert result.meta["orig_last_modified"] == ORIG_LAST_MODIFIED


@pytest.mark.parametrize(
    "filename",
    ["/tmp/amprion_bewegungsdaten_2025.zip", "../a.zip", r"..\a.zip", "", ".", "..", "a\0.zip"],
)
def test_download_refuses_unsafe_filenames(
    tmp_path: Path, link: DownloadLink, filename: str
) -> None:
    server = serve(ZIP)
    with server.client() as client, pytest.raises(DownloadError, match="filename"):
        download(dataclasses.replace(link, filename=filename), tmp_path, client)
    assert server.requests == []
    assert not (tmp_path / "raw").exists()


def test_encoded_separator_cannot_escape_the_archive(tmp_path: Path) -> None:
    link = classified("https://example.org/%2Ftmp%2Famprion_bewegungsdaten_2025.zip")

    with pytest.raises(DownloadError, match="filename"):
        archive_path(tmp_path, link)


def test_direct_link_cannot_override_the_archive_root(tmp_path: Path, link: DownloadLink) -> None:
    with pytest.raises(DownloadError, match="leaves the archive"):
        archive_path(tmp_path, dataclasses.replace(link, file_id=str(tmp_path / "outside")))


def test_archive_path_refuses_a_subfolder_symlink_to_outside(
    tmp_path: Path, link: DownloadLink
) -> None:
    data = tmp_path / "data"
    target = archive_path(data, link)
    outside = tmp_path / "outside"
    outside.mkdir()
    target.parent.parent.mkdir(parents=True)
    target.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DownloadError, match="leaves the archive"):
        archive_path(data, link)


def test_current_page_link_uses_its_known_wayback_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    known = next(
        candidate
        for candidate in older_links()
        if candidate.year == 2025 and candidate.uenb == "50hertz"
    )
    monkeypatch.setattr(
        "eeg_bewegungsdaten.links.OLDER_FILES",
        ((known.url, "2026-09-04", "20260905131904", hashlib.sha256(ZIP).hexdigest()),),
    )

    def response(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAGE_URL:
            return httpx.Response(200, text=PAGE)
        if str(request.url) == known.fallback_url:
            return httpx.Response(200, content=ZIP)
        return httpx.Response(404)

    server = Server(response)
    with server.client() as client:
        link = next(candidate for candidate in find_links(client) if candidate.url == known.url)
        result = download(link, tmp_path, client)

    assert result.meta["source_url"] == known.fallback_url
    assert result.meta["sha256"] == hashlib.sha256(ZIP).hexdigest()
    assert result.meta["page_url"] == PAGE_URL
    assert [str(r.url) for r in server.requests] == [PAGE_URL, known.url, known.fallback_url]


def test_cached_download_records_newly_discovered_parts(tmp_path: Path, link: DownloadLink) -> None:
    server = serve(ZIP)
    old_link = dataclasses.replace(link, part=1)
    with server.client() as client:
        first = download(old_link, tmp_path, client)
        again = download(dataclasses.replace(old_link, expected_parts=(1, 2)), tmp_path, client)
        third = download(dataclasses.replace(old_link, expected_parts=(1,)), tmp_path, client)
    assert len(server.requests) == 1
    assert not again.downloaded
    assert third.meta["expected_parts"] == [1, 2]
    assert json.loads(meta_path(first.path).read_text(encoding="utf-8"))["expected_parts"] == [1, 2]


def test_progress_reports_the_bytes_received(tmp_path: Path, link: DownloadLink) -> None:
    calls: list[tuple[int, int | None]] = []
    server = serve(ZIP, headers={"Content-Length": str(len(ZIP))})

    with server.client() as client:
        download(link, tmp_path, client, progress=lambda done, total: calls.append((done, total)))

    assert calls[0] == (0, len(ZIP))
    assert calls[-1] == (len(ZIP), len(ZIP))


def test_progress_without_content_length_has_no_total(tmp_path: Path, link: DownloadLink) -> None:
    calls: list[tuple[int, int | None]] = []

    class Chunked(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield ZIP

    server = Server(lambda request: httpx.Response(200, stream=Chunked()))
    with server.client() as client:
        download(link, tmp_path, client, progress=lambda done, total: calls.append((done, total)))

    assert {total for _, total in calls} == {None}
    assert calls[-1][0] == len(ZIP)


def test_progress_starts_over_for_the_wayback_copy(tmp_path: Path) -> None:
    calls: list[tuple[int, int | None]] = []

    with live_gone().client() as client:
        download(with_copy(), tmp_path, client, progress=lambda d, t: calls.append((d, t)))

    assert [done for done, _ in calls] == [0, len(ZIP)]


def test_finished_download_reports_no_progress(tmp_path: Path, link: DownloadLink) -> None:
    calls: list[tuple[int, int | None]] = []
    with serve(ZIP).client() as client:
        download(link, tmp_path, client)
        download(link, tmp_path, client, progress=lambda d, t: calls.append((d, t)))

    assert calls == []
