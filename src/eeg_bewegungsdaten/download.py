"""Download files into the local archive, with SHA-256 and metadata.

Layout: ``raw/<year>/<tso>/<file id>/<file name>`` plus ``<file name>.meta.json`` next to it.
The legend goes to ``raw/legende/<file id>/``. A new upload gets a new file id on the CDN, so
older versions stay in the archive. URLs without a CDN id get a hash of the URL instead. The
parts of a split upload share one folder. Without CDN ids, it is named after a hash of their
server folder. On the CDN each part has its own id, so the folder is named after a hash of the
URLs of all parts.

A file counts as downloaded only once its metadata file exists. Data arrive in a ``.part`` file
that is renamed after the size and the ZIP structure have been checked, so an aborted download
never looks finished. A broken connection, a timeout, 429 and 5xx are tried again a few times.
A transfer that breaks off keeps its ``.part``, with a ``.part.json`` that notes the URL and the
ETag or Last-Modified. The next attempt, also in a later run, asks for the rest (``Range``,
``If-Range``) and starts again if the server sends anything but the rest of the same file.

When the server answers 404 for a file with a Wayback copy (`DownloadLink.fallback_url`), the
copy is downloaded instead, into the same place. It must have the SHA-256 recorded in the link.
A file from the server itself with other bytes than recorded is archived all the same, as the
TSO may have replaced it, with a warning and the recorded SHA-256 as ``expected_sha256`` in its
metadata.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import logging
import os
import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from eeg_bewegungsdaten._version import tool_info
from eeg_bewegungsdaten.http import new_client
from eeg_bewegungsdaten.links import PAGE_URL, DownloadLink
from eeg_bewegungsdaten.paths import raw_dir, utc_now, write_json
from eeg_bewegungsdaten.progress import Progress

log = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"
CHUNK_SIZE = 1 << 20
#: Attempts to rename a finished download. On Windows a virus scanner or the search indexer may
#: still hold the new file for a moment.
RENAME_ATTEMPTS = 5
#: Attempts per URL when the error may go away: the connection broke or timed out, or the server
#: is busy. The Wayback Machine often answers 429 or 503.
ATTEMPTS = 4
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
#: Seconds to wait at most before the next attempt, also when `Retry-After` asks for longer
MAX_WAIT = 60.0
#: Validator of a capture of the Wayback Machine: its bytes never change, but it comes without
#: ETag or Last-Modified. A broken-off download of it goes on without If-Range.
WAYBACK_CAPTURE = "wayback capture"
_WAYBACK_CAPTURE = re.compile(r"^https://web\.archive\.org/web/\d+id_/")


class DownloadError(RuntimeError):
    """The server sent something that is not the complete file."""


class ChecksumError(RuntimeError):
    """An archived file is missing or no longer has its recorded size and SHA-256."""


@dataclass(frozen=True)
class ArchivedFile:
    path: Path
    meta: dict[str, Any]
    downloaded: bool


def archive_path(data_dir: Path, link: DownloadLink) -> Path:
    if (
        not link.filename
        or link.filename in (".", "..")
        or any(c in link.filename for c in ("/", "\\", "\0"))
    ):
        raise DownloadError(f"Invalid download filename: {link.filename!r}")
    if link.part is not None and link.file_id:
        file_id = _short_hash("\n".join(link.part_urls or (link.url,)))
    elif link.file_id:
        file_id = link.file_id
    elif link.part is not None:
        file_id = _short_hash(link.url.rsplit("/", 1)[0])
    else:
        file_id = _short_hash(link.url)
    if link.kind == "legende":
        folder = raw_dir(data_dir) / "legende"
    else:
        folder = raw_dir(data_dir) / str(link.year) / str(link.uenb)
    path = folder / file_id / link.filename
    if not path.resolve().is_relative_to(raw_dir(data_dir).resolve()):
        raise DownloadError(f"Download path leaves the archive: {path}")
    return path


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def meta_path(path: Path) -> Path:
    return path.with_name(path.name + META_SUFFIX)


def read_meta(path: Path) -> dict[str, Any] | None:
    """Metadata of a finished download, or None if the download is missing or incomplete."""
    try:
        meta: dict[str, Any] = json.loads(meta_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not path.is_file() or path.stat().st_size != meta.get("size"):
        return None
    return meta


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    link: DownloadLink,
    data_dir: Path,
    client: httpx.Client | None = None,
    *,
    progress: Progress | None = None,
) -> ArchivedFile:
    """Download `link` into the archive unless a finished copy is already there. `progress`
    gets the bytes received and the `Content-Length`."""
    path = archive_path(data_dir, link)
    meta = read_meta(path)
    if meta is not None:
        expected = sorted(set(meta.get("expected_parts", ())) | set(link.expected_parts))
        if expected and meta.get("expected_parts") != expected:
            meta["expected_parts"] = expected
            write_json(meta_path(path), meta)
        log.debug("Already archived: %s", path)
        return ArchivedFile(path, meta, downloaded=False)

    http = client or new_client()
    try:
        meta = _fetch_or_fall_back(http, link, path, progress)
    finally:
        if client is None:
            http.close()
    meta["page_url"] = PAGE_URL if link.listed else None
    write_json(meta_path(path), meta)
    log.debug("Downloaded %s (%d bytes)", path, meta["size"])
    return ArchivedFile(path, meta, downloaded=True)


def _fetch_or_fall_back(
    http: httpx.Client, link: DownloadLink, path: Path, progress: Progress | None
) -> dict[str, Any]:
    try:
        meta = _retrying(link.url, lambda: _fetch(http, link, path, link.url, progress=progress))
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404 or link.fallback_url is None:
            raise
    else:
        if link.sha256 is not None and meta["sha256"] != link.sha256:
            # Kept, as it is what the TSO publishes now, but not without a word.
            log.warning(
                "%s has other bytes than recorded: SHA-256 %s, expected %s. The TSO may have "
                "replaced the file; it is archived as it is now.",
                link.url,
                meta["sha256"],
                link.sha256,
            )
            meta["expected_sha256"] = link.sha256
        return meta
    log.warning("%s answers 404, downloading the Wayback copy %s", link.url, link.fallback_url)
    fallback = link.fallback_url
    return _retrying(
        fallback,
        lambda: _fetch(http, link, path, fallback, sha256=link.sha256, progress=progress),
    )


def _retrying(url: str, fetch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """`fetch()`, tried again up to `ATTEMPTS` times in all on errors that may go away."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fetch()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            wait = _wait_before_retry(e, attempt)
            if wait is None or attempt == ATTEMPTS:
                raise
            cause = (
                f"HTTP {e.response.status_code}"
                if isinstance(e, httpx.HTTPStatusError)
                else type(e).__name__
            )
            log.info(
                "%s: %s, trying again in %.0f s (attempt %d of %d)",
                url,
                cause,
                wait,
                attempt + 1,
                ATTEMPTS,
            )
            time.sleep(wait)
    raise AssertionError("unreachable")


def _wait_before_retry(error: httpx.HTTPError, attempt: int) -> float | None:
    """Seconds to wait before the next attempt, or None if trying again cannot help: 1, 2, 4
    seconds, or what `Retry-After` asks for, up to `MAX_WAIT`."""
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code not in RETRY_STATUS:
            return None
        asked = _retry_after(error.response.headers.get("Retry-After"))
        if asked is not None:
            return min(asked, MAX_WAIT)
    return min(2.0 ** (attempt - 1), MAX_WAIT)


def _retry_after(value: str | None) -> float | None:
    """`Retry-After` in seconds: a number of seconds or an HTTP date."""
    if value is None:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max((when - dt.datetime.now(dt.timezone.utc)).total_seconds(), 0.0)


def _fetch(
    http: httpx.Client,
    link: DownloadLink,
    path: Path,
    url: str,
    sha256: str | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    state = _state_path(part)
    # Whether `part` holds the start of the file, so that the next attempt can go on from there
    # if this one breaks off: a broken connection, a busy server, Ctrl+C.
    resumable = False
    keep = False
    try:
        resume = _resume_point(part, url)
        resumable = resume is not None
        while True:
            with http.stream("GET", url, headers=_range_headers(resume)) as response:
                if (
                    resume
                    and response.status_code in (206, 416)
                    and not _continues(response, resume, url)
                ):
                    # A part of another version of the file, or none: start again.
                    resume = None
                    resumable = False
                    continue
                response.raise_for_status()
                if response.status_code != 206:
                    resume = None  # the whole file, also when the server ignored the range
                headers = response.headers
                start = resume[0] if resume else 0
                digest = _sha256_of_part(part, start)
                size = start
                expected = headers.get("Content-Length")
                # Content-Length counts the bytes on the wire, before any Content-Encoding.
                encoded = headers.get("Content-Encoding", "identity") != "identity"
                total = (
                    start + int(expected) if expected is not None and expected.isdigit() else None
                )
                validator = None if encoded else _validator(headers, url)
                if validator is None:
                    state.unlink(missing_ok=True)
                else:
                    state.write_text(json.dumps({"url": url, "validator": validator}))
                resumable = validator is not None
                if progress is not None:
                    progress(start, total)
                with part.open("ab" if start else "wb") as f:
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        f.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        if progress is not None:
                            downloaded = response.num_bytes_downloaded
                            progress(downloaded if encoded else size, total)
                    f.flush()
                    os.fsync(f.fileno())
                # All bytes are in; from here a failed check deletes the part.
                resumable = False
                received = response.num_bytes_downloaded if encoded else size - start
                if expected is not None and int(expected) != received:
                    raise DownloadError(
                        f"{url}: got {start + received} of {start + int(expected)} bytes"
                    )
                source_url = str(response.url)
            break
        if sha256 is not None and digest.hexdigest() != sha256:
            raise DownloadError(f"{url}: SHA-256 {digest.hexdigest()}, expected {sha256}")
        _check_zip(part, url)
        _replace(part, path)
    except (httpx.TransportError, httpx.HTTPStatusError, KeyboardInterrupt):
        keep = resumable
        raise
    finally:
        if not keep:
            part.unlink(missing_ok=True)
            state.unlink(missing_ok=True)

    return {
        "url": link.url,
        # Where the bytes came from, after redirects: `url` or its Wayback copy.
        "source_url": source_url,
        "filename": link.filename,
        "kind": link.kind,
        "uenb": link.uenb,
        "year": link.year,
        "file_id": link.file_id,
        "part": link.part,
        "expected_parts": list(link.expected_parts),
        "published": link.published.isoformat() if link.published else None,
        "size": size,
        "sha256": digest.hexdigest(),
        "downloaded_at": utc_now(),
        "content_type": headers.get("Content-Type"),
        "last_modified": headers.get("Last-Modified"),
        # The Wayback Machine keeps the headers of the original server under this prefix.
        "orig_last_modified": headers.get("X-Archive-Orig-Last-Modified"),
        "etag": headers.get("ETag"),
        **tool_info(),
    }


def _state_path(part: Path) -> Path:
    """Where a broken-off `.part` notes its URL and the validator of the file it holds."""
    return part.with_name(part.name + ".json")


def _validator(headers: httpx.Headers, url: str) -> str | None:
    """What tells whether the file changed since: a strong ETag, or else Last-Modified. A capture
    of the Wayback Machine never changes, but has neither; it gets `WAYBACK_CAPTURE`."""
    etag = str(headers.get("ETag") or "")
    if etag and not etag.startswith("W/"):
        return etag
    modified = str(headers.get("Last-Modified") or "")
    if modified:
        return modified
    return WAYBACK_CAPTURE if _WAYBACK_CAPTURE.match(url) else None


def _range_headers(resume: tuple[int, str] | None) -> dict[str, str]:
    """Ask for the rest of the file; with If-Range, the server sends the whole file instead if it
    changed."""
    if resume is None:
        return {}
    start, validator = resume
    asked = {"Range": f"bytes={start}-"}
    if validator != WAYBACK_CAPTURE:
        asked["If-Range"] = validator
    return asked


def _resume_point(part: Path, url: str) -> tuple[int, str] | None:
    """Bytes in `part` and their validator, if a broken-off download of `url` left them."""
    state = _state_path(part)
    try:
        noted = json.loads(state.read_text(encoding="utf-8"))
        size = part.stat().st_size
    except (OSError, ValueError):
        noted, size = None, 0
    if not isinstance(noted, dict) or noted.get("url") != url or not size:
        part.unlink(missing_ok=True)
        state.unlink(missing_ok=True)
        return None
    return size, str(noted["validator"])


def _continues(response: httpx.Response, resume: tuple[int, str], url: str) -> bool:
    """Whether a 206 answer holds the rest of the same file, from the first missing byte on."""
    start, validator = resume
    content_range = response.headers.get("Content-Range", "")
    return (
        response.status_code == 206
        and content_range.startswith(f"bytes {start}-")
        and _validator(response.headers, url) == validator
    )


def _sha256_of_part(part: Path, size: int) -> Any:
    """SHA-256 over the first `size` bytes of `part`, to go on with the rest."""
    digest = hashlib.sha256()
    if size:
        with part.open("rb") as f:
            for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    return digest


def _replace(part: Path, path: Path) -> None:
    """Rename `part` to `path`; while another program holds the file, wait and try again (0.2 to
    1.6 seconds, 3 seconds in all)."""
    for attempt in range(1, RENAME_ATTEMPTS + 1):
        try:
            part.replace(path)
            return
        except PermissionError:
            if attempt == RENAME_ATTEMPTS:
                raise
            log.debug("%s is in use, renaming it again (attempt %d)", part, attempt + 1)
            time.sleep(0.1 * 2**attempt)


def _check_zip(path: Path, url: str) -> None:
    """Both data files (ZIP) and the legend (XLSX) are ZIP containers."""
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
    except zipfile.BadZipFile as e:
        raise DownloadError(f"{url}: not a ZIP file ({e})") from e
    if broken is not None:
        raise DownloadError(f"{url}: CRC error in {broken}")


def archived_files(data_dir: Path, *, include_incomplete: bool = False) -> list[ArchivedFile]:
    """Downloads, oldest first. Include missing or truncated files when verifying the archive."""
    found = []
    for meta_file in raw_dir(data_dir).rglob(f"*{META_SUFFIX}"):
        path = meta_file.with_name(meta_file.name.removesuffix(META_SUFFIX))
        meta = (
            json.loads(meta_file.read_text(encoding="utf-8"))
            if include_incomplete
            else read_meta(path)
        )
        if meta is not None:
            found.append(ArchivedFile(path, meta, downloaded=False))
    return sorted(found, key=lambda f: (str(f.meta["downloaded_at"]), str(f.path)))


def verify(archived: ArchivedFile) -> None:
    """Recompute the SHA-256 of an archived file and compare it with its metadata."""
    if not archived.path.is_file():
        raise ChecksumError(f"{archived.path}: archived file is missing")
    size = archived.path.stat().st_size
    if size != archived.meta["size"]:
        raise ChecksumError(f"{archived.path}: size {size}, expected {archived.meta['size']}")
    actual = sha256_of(archived.path)
    if actual != archived.meta["sha256"]:
        raise ChecksumError(
            f"{archived.path}: SHA-256 {actual}, expected {archived.meta['sha256']}"
        )
