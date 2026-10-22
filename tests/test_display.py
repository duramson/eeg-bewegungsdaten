from __future__ import annotations

import dataclasses
import io

import httpx
import pytest
from rich.console import Console

from eeg_bewegungsdaten import display
from eeg_bewegungsdaten.download import DownloadError
from tests.builders import TENNET_PART, classified


def console(encoding: str) -> Console:
    return Console(file=io.TextIOWrapper(io.BytesIO(), encoding=encoding), color_system=None)


@pytest.mark.parametrize(("encoding", "symbol"), [("utf-8", "✓"), ("cp1252", "+"), ("ascii", "+")])
def test_symbols_fall_back_to_ascii(encoding: str, symbol: str) -> None:
    assert display._symbols(console(encoding)).done == symbol


def test_label_shows_the_part_of_the_upload() -> None:
    link = classified(TENNET_PART.format(folder="2024", part=2))
    link = dataclasses.replace(link, expected_parts=(1, 2, 3, 4))
    assert display._link_item(link, set()).part == "part 2/4"


def test_reason_of_an_http_error_is_its_status() -> None:
    request = httpx.Request("GET", "https://example.org/a.zip")
    error = httpx.HTTPStatusError(
        "Client error", request=request, response=httpx.Response(429, request=request)
    )
    assert display.reason(error) == "HTTP 429 Too Many Requests"


def test_reason_of_a_download_error_leaves_out_the_url() -> None:
    error = DownloadError("https://example.org/a.zip: got 1 of 2 bytes")
    assert display.reason(error) == "got 1 of 2 bytes"


@pytest.mark.parametrize(
    ("seconds", "text"), [(0, "0:00"), (59.9, "0:59"), (192, "3:12"), (3725, "1:02:05")]
)
def test_duration(seconds: float, text: str) -> None:
    assert display._duration(seconds) == text
