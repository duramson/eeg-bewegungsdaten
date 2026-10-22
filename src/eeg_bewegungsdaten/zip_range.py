"""A seekable HTTP range source for the standard library's ZIP reader.

The ZIP reader handles directories, local headers, decompression and CRC checks. This source
only supplies bounded byte ranges, rejecting a full response before reading its body.
"""

from __future__ import annotations

import io
import re
import zipfile

import httpx

_TAIL_SIZE = 65557  # End-of-central-directory record plus the longest ZIP comment.
_RANGE_SIZE = 16 << 20
_MAX_READ_SIZE = 64 << 20  # Larger than any selected member or the central directory.
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class RangeFile(io.RawIOBase):
    """Read a remote ZIP through a shared HTTP client, with its tail kept in memory."""

    def __init__(self, http: httpx.Client, url: str) -> None:
        super().__init__()
        self.http = http
        self.url = url
        self.size = 0
        self.position = 0
        self.headers = httpx.Headers()
        self.source_url = url
        self.tail = self._request(None, _TAIL_SIZE)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self.position + offset
        elif whence == io.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError(f"Unknown seek mode: {whence}")
        if position < 0:
            raise ValueError("Negative seek position")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        end = self.size if size < 0 else min(self.position + size, self.size)
        if end - self.position > _MAX_READ_SIZE:
            raise zipfile.BadZipFile(f"{self.url}: ZIP read exceeds the byte range limit")
        chunks = []
        while self.position < end:
            count = min(end - self.position, _RANGE_SIZE)
            if self.position >= self.size - len(self.tail):
                start = self.position - (self.size - len(self.tail))
                data = self.tail[start : start + count]
            else:
                data = self._request(self.position, count)
            chunks.append(data)
            self.position += len(data)
        return b"".join(chunks)

    def _request(self, start: int | None, count: int) -> bytes:
        value = f"-{count}" if start is None else f"{start}-{start + count - 1}"
        headers = {
            "Range": f"bytes={value}",
            "Accept-Encoding": "identity",
            "User-Agent": "eeg-archive-check/0.1",
        }
        with self.http.stream("GET", self.url, headers=headers) as response:
            response.raise_for_status()
            match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if response.status_code != 206 or match is None:
                raise zipfile.BadZipFile(f"{self.url}: server did not honor the byte range")
            first, last, total = map(int, match.groups())
            expected_first = max(0, total - count) if start is None else start
            expected_last = total - 1 if start is None else start + count - 1
            if (
                first != expected_first
                or last != expected_last
                or not 0 <= first <= last < total
                or (self.size and self.size != total)
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise zipfile.BadZipFile(f"{self.url}: inconsistent byte range")
            expected = last - first + 1
            data = bytearray()
            for chunk in response.iter_bytes(1 << 16):
                data.extend(chunk)
                if len(data) > expected:
                    raise zipfile.BadZipFile(f"{self.url}: byte range response is too long")
            if len(data) != expected:
                raise zipfile.BadZipFile(f"{self.url}: truncated byte range response")
            self.size = total
            self.headers = response.headers
            self.source_url = str(response.url)
            return bytes(data)
