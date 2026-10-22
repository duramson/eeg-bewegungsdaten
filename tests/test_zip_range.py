from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Callable, Iterator

import httpx
import pytest

from eeg_bewegungsdaten.zip_range import RangeFile
from tests.builders import zip_bytes

URL = "https://example.test/data.zip"
MEMBER = "PaymentRegister/Transnet/Original files/2012.csv"
BODY = b"a;b\r\n1;2\r\n" * 20000


def range_response(request: httpx.Request, body: bytes) -> httpx.Response:
    value = request.headers["Range"].removeprefix("bytes=")
    left, right = value.split("-")
    start = int(left) if left else max(0, len(body) - int(right))
    end = int(right) if left else len(body) - 1
    end = min(end, len(body) - 1)
    return httpx.Response(
        206,
        content=body[start : end + 1],
        headers={"Content-Range": f"bytes {start}-{end}/{len(body)}"},
    )


def test_only_the_selected_member_is_read_and_inflated() -> None:
    body = zip_bytes({MEMBER: BODY, "unrelated.bin": bytes(range(256)) * 100000})
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["User-Agent"] == "eeg-archive-check/0.1"
        assert request.headers["Accept-Encoding"] == "identity"
        return range_response(request, body)

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        RangeFile(http, URL) as remote,
        zipfile.ZipFile(remote) as archive,
    ):
        assert archive.read(MEMBER) == BODY
    assert len(requests) >= 2
    assert all(request.headers["Range"] != f"bytes=0-{len(body) - 1}" for request in requests)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_members_and_zip_comments_follow_the_standard_zip_reader(compression: int) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        archive.writestr(MEMBER, BODY)
        archive.comment = b"a comment"
    body = buffer.getvalue()
    with (
        httpx.Client(transport=httpx.MockTransport(lambda r: range_response(r, body))) as http,
        RangeFile(http, URL) as remote,
        zipfile.ZipFile(remote) as archive,
    ):
        assert archive.read(MEMBER) == BODY


@pytest.mark.parametrize(
    "respond",
    [
        lambda r: httpx.Response(200, content=b"whole archive"),
        lambda r: httpx.Response(206, content=b"x"),
        lambda r: httpx.Response(206, content=b"x", headers={"Content-Range": "bytes 0-1/2"}),
        lambda r: httpx.Response(206, content=b"xx", headers={"Content-Range": "bytes 1-2/4"}),
    ],
)
def test_invalid_range_responses_are_rejected(
    respond: Callable[[httpx.Request], httpx.Response],
) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        pytest.raises(zipfile.BadZipFile, match="range"),
    ):
        RangeFile(http, URL)


def test_a_server_ignoring_range_is_rejected_without_reading_its_body() -> None:
    class Unreadable(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise AssertionError("The whole ZIP must not be read")
            yield b""

    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Unreadable()))
        ) as http,
        pytest.raises(zipfile.BadZipFile, match="range"),
    ):
        RangeFile(http, URL)


def test_changed_archive_size_between_requests_is_rejected() -> None:
    body = zip_bytes({MEMBER: BODY}) + b" " * 100000
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return range_response(request, body if calls == 1 else body + b"x")

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        RangeFile(http, URL) as remote,
        pytest.raises(zipfile.BadZipFile, match="range"),
    ):
        remote.read(30)


def test_corrupt_member_crc_is_rejected() -> None:
    body = bytearray(zip_bytes({MEMBER: BODY}))
    central = body.index(b"PK\x01\x02")
    struct.pack_into("<L", body, central + 16, 0)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: range_response(r, bytes(body)))
        ) as http,
        RangeFile(http, URL) as remote,
        zipfile.ZipFile(remote) as archive,
        pytest.raises(zipfile.BadZipFile, match="CRC"),
    ):
        archive.read(MEMBER)


def test_oversized_zip_directory_cannot_trigger_a_whole_archive_read() -> None:
    total = 2_131_334_747
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["Range"] == "bytes=-65557"
        return httpx.Response(
            206,
            content=b"x" * 65557,
            headers={"Content-Range": f"bytes {total - 65557}-{total - 1}/{total}"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        RangeFile(http, URL) as remote,
        pytest.raises(zipfile.BadZipFile, match="range limit"),
    ):
        remote.read(total)
    assert calls == 1
