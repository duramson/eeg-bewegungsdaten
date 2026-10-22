"""Download files into the local archive, with SHA-256 and metadata.

Layout: ``raw/<year>/<tso>/<file id>/<file name>`` plus ``<file name>.meta.json`` next to it.
The legend goes to ``raw/legende/<file id>/``. A new upload gets a new file id on the CDN, so
older versions stay in the archive. URLs without a CDN id get a hash of the URL instead. The
parts of a split upload share one folder. Without CDN ids, it is named after a hash of their
server folder. On the CDN each part has its own id, so the folder is named after a hash of the
URLs of all parts.

A file counts as downloaded only once its metadata file exists. Data arrive in a ``.part`` file
that is renamed after the size and the structure (ZIP, or the header line of a bare CSV) have
been checked, so an aborted download never looks finished.

When the server answers 404 for a file with a Wayback copy (`DownloadLink.fallback_url`), the
copy is downloaded instead, into the same place. It must have the SHA-256 recorded in the link.
So must a file whose `url` is a Wayback copy without a fallback (the files before 2016).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zipfile
import zlib
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from eeg_bewegungsdaten._version import __version__
from eeg_bewegungsdaten.http import new_client
from eeg_bewegungsdaten.links import PAGE_URL, DownloadLink
from eeg_bewegungsdaten.paths import raw_dir, utc_now, write_json
from eeg_bewegungsdaten.zip_range import RangeFile

log = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"
CHUNK_SIZE = 1 << 20


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
    if link.upload_id:
        file_id = _short_hash(link.upload_id)
    elif link.part is not None and link.file_id:
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
    link: DownloadLink, data_dir: Path, client: httpx.Client | None = None
) -> ArchivedFile:
    """Download `link` into the archive unless a finished copy is already there."""
    path = archive_path(data_dir, link)
    meta = read_meta(path)
    if meta is not None:
        expected = sorted(set(meta.get("expected_parts", ())) | set(link.expected_parts))
        if expected and meta.get("expected_parts") != expected:
            meta["expected_parts"] = expected
            write_json(meta_path(path), meta)
        log.info("Already archived: %s", path)
        return ArchivedFile(path, meta, downloaded=False)

    http = client or new_client()
    try:
        meta = _fetch_or_fall_back(http, link, path)
    finally:
        if client is None:
            http.close()
    meta["page_url"] = PAGE_URL if link.listed else None
    write_json(meta_path(path), meta)
    log.info("Downloaded %s (%d bytes)", path, meta["size"])
    return ArchivedFile(path, meta, downloaded=True)


def _fetch_or_fall_back(http: httpx.Client, link: DownloadLink, path: Path) -> dict[str, Any]:
    # The recorded SHA-256 guards the Wayback copy: the fallback, or `url` itself when it is the
    # only copy. A file on the server is archived as the server sends it.
    only_copy = link.fallback_url is None
    try:
        return _fetch(http, link, path, link.url, sha256=link.sha256 if only_copy else None)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404 or link.fallback_url is None:
            raise
    log.warning("%s answers 404, downloading the Wayback copy %s", link.url, link.fallback_url)
    return _fetch(http, link, path, link.fallback_url, sha256=link.sha256)


def _fetch(
    http: httpx.Client, link: DownloadLink, path: Path, url: str, sha256: str | None = None
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with ExitStack() as stack:
            expected: int | None
            response = None
            if link.zip_member is not None:
                if link.size is None or sha256 is None or link.doi is None:
                    raise DownloadError(f"{url}: ZIP member needs size, SHA-256 and DOI")
                remote = stack.enter_context(RangeFile(http, url))
                archive = stack.enter_context(zipfile.ZipFile(remote))
                matches = [i for i in archive.infolist() if i.filename == link.zip_member]
                if len(matches) != 1:
                    raise DownloadError(f"{url}: expected one ZIP member {link.zip_member!r}")
                info = matches[0]
                if info.file_size != link.size:
                    raise DownloadError(
                        f"{url}: member size {info.file_size}, expected {link.size}"
                    )
                if info.compress_size > max(1024, link.size * 2):
                    raise DownloadError(f"{url}: compressed member exceeds its size bound")
                if info.flag_bits & 1 or info.compress_type not in (0, 8):
                    raise DownloadError(f"{url}: unsupported ZIP member compression or encryption")
                member = stack.enter_context(archive.open(info))
                chunks = iter((member.read(),))
                expected = link.size
                headers = remote.headers
                source_url = remote.source_url
            else:
                response = stack.enter_context(http.stream("GET", url))
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                expected = int(length) if length is not None else None
                chunks = response.iter_bytes(CHUNK_SIZE)
                headers = response.headers
                source_url = str(response.url)
            with part.open("wb") as f:
                for chunk in chunks:
                    f.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                f.flush()
                os.fsync(f.fileno())
            # Content-Length counts the bytes on the wire, before any Content-Encoding.
            encoded = headers.get("Content-Encoding", "identity") != "identity"
            received = response.num_bytes_downloaded if response is not None and encoded else size
            if expected is not None and expected != received:
                raise DownloadError(f"{url}: got {received} of {expected} bytes")
        if sha256 is not None and digest.hexdigest() != sha256:
            raise DownloadError(f"{url}: SHA-256 {digest.hexdigest()}, expected {sha256}")
        if link.filename.lower().endswith((".csv", ".txt")):
            _check_csv(part, url)
        else:
            _check_zip(part, url)
        part.replace(path)
    except (zipfile.BadZipFile, zlib.error) as e:
        raise DownloadError(f"{url}: invalid ZIP member ({e})") from e
    finally:
        part.unlink(missing_ok=True)

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
        "tool_version": __version__,
        **({"doi": link.doi, "zip_member": link.zip_member} if link.zip_member else {}),
    }


def _check_zip(path: Path, url: str) -> None:
    """Both data files (ZIP) and the legend (XLSX) are ZIP containers."""
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
    except zipfile.BadZipFile as e:
        raise DownloadError(f"{url}: not a ZIP file ({e})") from e
    if broken is not None:
        raise DownloadError(f"{url}: CRC error in {broken}")


def _check_csv(path: Path, url: str) -> None:
    """Bare CSV/TXT files need a header with separators; an HTML error page has none."""
    with path.open("rb") as f:
        first = f.readline(1 << 16)
    if first.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"<") or b";" not in first:
        raise DownloadError(f"{url}: not a CSV file")


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
