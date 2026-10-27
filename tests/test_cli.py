from __future__ import annotations

import dataclasses
import errno
import functools
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console

from eeg_bewegungsdaten import cli, display
from eeg_bewegungsdaten.download import HostLimits
from eeg_bewegungsdaten.links import (
    PAGE_URL,
    DownloadLink,
    find_links,
    older_links,
    parse_links,
)
from tests.builders import PAGE, zip_bytes

# The older files that `find_links` adds to the page
OLDER = older_links(parse_links(PAGE))


def serve_page(
    monkeypatch: pytest.MonkeyPatch,
    failing: Callable[[str], bool] = lambda url: False,
    *,
    recorded: bool = False,
    page: httpx.Response | None = None,
) -> list[str]:
    """Serve the fixture page (or `page` instead) and a tiny ZIP for every file link, or 429 for
    the `failing` ones; return the list of requested URLs. The tiny ZIP has other bytes than the
    files recorded in `OLDER_FILES`, so their SHA-256 is left out unless `recorded`."""
    if not recorded:
        for name, find in (("find_links", find_links), ("older_links", older_links)):

            def unrecorded(
                *args: Any, find: Callable[..., list[DownloadLink]] = find, **kwargs: Any
            ) -> list[DownloadLink]:
                return [dataclasses.replace(link, sha256=None) for link in find(*args, **kwargs)]

            monkeypatch.setattr(f"eeg_bewegungsdaten.cli.{name}", unrecorded)
    seen: list[str] = []
    body = zip_bytes({"a.csv": b"x\r\n"})

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url == PAGE_URL:
            return page or httpx.Response(200, text=PAGE)
        if failing(url):
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, content=body)

    def new_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "new_client", new_client)
    monkeypatch.setattr("eeg_bewegungsdaten.links.new_client", new_client)
    # Retries of the failing files do not wait, with --jobs 1 or without
    monkeypatch.setattr("eeg_bewegungsdaten.download.time.sleep", lambda seconds: None)
    monkeypatch.setattr(cli, "HostLimits", functools.partial(HostLimits, sleep=lambda s: None))
    return seen


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return serve_page(monkeypatch)


def test_links_prints_one_line_per_file(
    requests: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["links"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 5 + len(OLDER)
    assert lines[0].split("\t")[:3] == ["2025", "50hertz", "2026-09-04"]
    assert lines[5].split("\t")[:3] == ["2016", "50hertz", "2017-11-30"]


PAGE_DOWN = {
    "503": httpx.Response(503, text="error"),
    "no-links": httpx.Response(200, text="<html><body>Relaunch</body></html>"),
}


@pytest.mark.parametrize("page", PAGE_DOWN.values(), ids=PAGE_DOWN.keys())
def test_links_without_the_page_prints_the_known_files(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    page: httpx.Response,
) -> None:
    serve_page(monkeypatch, page=page)

    assert cli.main(["links"]) == 1

    assert len(capsys.readouterr().out.splitlines()) == len(older_links())
    (message,) = caplog.messages
    assert message.startswith(f"Cannot read {PAGE_URL} (")
    assert message.endswith("); going on with the files this version knows")


@pytest.mark.parametrize("page", PAGE_DOWN.values(), ids=PAGE_DOWN.keys())
def test_download_without_the_page_takes_the_known_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    page: httpx.Response,
) -> None:
    serve_page(monkeypatch, page=page)

    assert cli.main(["--data-dir", str(tmp_path), "download", "--year", "2018"]) == 1

    files = sorted(p.parent.parent.name for p in (tmp_path / "raw" / "2018").rglob("*.zip"))
    assert files == ["50hertz", "amprion", "tennet", "transnetbw"]
    assert len([m for m in caplog.messages if m.startswith("Cannot read ")]) == 1


def test_download_filters_by_tso_and_keeps_the_legend(
    requests: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "amprion"]) == 0

    downloaded = [url for url in requests if url != PAGE_URL]
    assert sorted(downloaded) == sorted(amprion_and_legends())
    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == f"Downloading {len(downloaded)} files to {tmp_path}"
    # One line per file, an empty line, the summary
    rows = lines[1 : lines.index("")]
    assert len(rows) == len(downloaded)
    assert all(row.startswith("✓ ") and " sha256 " in row for row in rows)
    assert lines[lines.index("") + 1].startswith("✓ Done ")
    assert summary(lines, "Files") == (
        f"{len(downloaded)}/{len(downloaded)} done · {len(downloaded)} downloaded · 0 failed"
    )
    # Each downloaded file allowed one more at a time, up to the cap
    assert summary(lines, "Hosts") == (
        "netztransparenz.de 6 at a time (up to 6) · Wayback Machine 3 at a time (up to 3)"
    )
    assert summary(lines, "Archive") == str(tmp_path / "raw")


def amprion_and_legends() -> list[str]:
    """The URLs of the files of Amprion and of the legends, in the order of the links."""
    page = [link.url for link in parse_links(PAGE) if link.uenb in ("amprion", None)]
    return page + [link.url for link in OLDER if link.uenb in ("amprion", None)]


@pytest.mark.parametrize("jobs", ["1", "2"])
def test_download_with_fixed_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], jobs: str
) -> None:
    requests = serve_page(monkeypatch)
    args = ["--data-dir", str(tmp_path), "download", "--uenb", "amprion", "--jobs", jobs]
    assert cli.main(args) == 0

    downloaded = [url for url in requests if url != PAGE_URL]
    lines = capsys.readouterr().err.splitlines()
    if jobs == "1":
        # One file after the other, in the order of the links, as before --jobs
        assert downloaded == amprion_and_legends()
        assert not [line for line in lines if line.startswith("Hosts ")]
    else:
        assert sorted(downloaded) == sorted(amprion_and_legends())
        assert (
            summary(lines, "Hosts")
            == "netztransparenz.de 2 at a time · Wayback Machine 2 at a time"
        )


@pytest.mark.parametrize("jobs", ["0", "-1", "x"])
def test_download_refuses_fewer_than_one_job(jobs: str) -> None:
    with pytest.raises(SystemExit):
        cli.main(["download", "--jobs", jobs])


def summary(lines: list[str], name: str) -> str:
    """The value of a line of the final summary."""
    line = next(line for line in reversed(lines) if line.startswith(f"{name} "))
    return line.removeprefix(name).strip()


def test_download_twice_fetches_only_the_page(requests: list[str], tmp_path: Path) -> None:
    cli.main(["--data-dir", str(tmp_path), "download"])
    first = len(requests)
    cli.main(["--data-dir", str(tmp_path), "download"])

    assert requests[first:] == [PAGE_URL]


def test_download_goes_on_after_a_failed_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    serve_page(monkeypatch, failing=lambda url: "web.archive.org" in url and "2017" in url)

    assert cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "tennet"]) == 1

    lines = capsys.readouterr().err.splitlines()
    older = [link for link in OLDER if link.uenb in ("tennet", None)]
    rows = lines[1 : lines.index("")]
    # TenneT 2025 and the legend from the page, all older files but the one for 2017
    assert len([row for row in rows if row.startswith("✓ ")]) == 2 + len(older) - 1
    failed = [row for row in rows if row.startswith("✗ ")]
    assert len(failed) == 1
    assert failed[0].split()[1:3] == ["2017", "tennet"]
    assert "HTTP 429 Too Many Requests  after " in failed[0]
    assert lines[lines.index("") + 1].startswith("✗ Finished with errors ")
    assert summary(lines, "Files").endswith(" · 1 failed")
    # The failed file again, with its URL
    assert lines[-3] == failed[0]
    assert lines[-2].strip().endswith("TenneT_Bewegungsdaten_2017.zip")
    assert lines[-1] == "Run the same command again to retry the failed files."
    assert not [p for p in (tmp_path / "raw" / "2017").rglob("*") if p.is_file()]
    assert (tmp_path / "raw" / "2018" / "tennet").is_dir()


def refuse_rename(monkeypatch: pytest.MonkeyPatch, error: OSError, when: str) -> None:
    """Let renaming the finished downloads whose path contains `when` fail with `error`."""
    replace = Path.replace

    def refused(self: Path, target: Path) -> Path:
        if when in str(target).lower():
            raise error
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", refused)
    monkeypatch.setattr("eeg_bewegungsdaten.download.time.sleep", lambda seconds: None)


def test_download_goes_on_after_an_os_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A virus scanner that does not let go of Amprion 2018 fails that file only."""
    serve_page(monkeypatch)
    refuse_rename(monkeypatch, PermissionError(13, "The file is in use"), "amprion")

    assert cli.main(["--data-dir", str(tmp_path), "download", "--year", "2018"]) == 1

    lines = capsys.readouterr().err.splitlines()
    failed = [row for row in lines[1 : lines.index("")] if row.startswith("✗ ")]
    assert len(failed) == 1
    assert failed[0].split()[1:3] == ["2018", "amprion"]
    assert "The file is in use" in failed[0]
    files = sorted(p.parent.parent.name for p in (tmp_path / "raw" / "2018").rglob("*.zip"))
    assert files == ["50hertz", "tennet", "transnetbw"]
    assert not list((tmp_path / "raw" / "2018" / "amprion").rglob("*.*"))


@pytest.mark.parametrize(
    ("jobs", "started"),
    # Two files at a time per host at first: those already running end, and fail as well
    [([], 4), (["--jobs", "1"], 1)],
    ids=["adaptive", "one-job"],
)
def test_download_stops_when_the_disk_is_full(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    jobs: list[str],
    started: int,
) -> None:
    requests = serve_page(monkeypatch)
    refuse_rename(monkeypatch, OSError(errno.ENOSPC, "No space left on device"), "")

    assert cli.main(["--data-dir", str(tmp_path), "download", "--year", "2018", *jobs]) == 1

    assert len([url for url in requests if url != PAGE_URL]) == started
    # The line of each file, and again in the summary
    lines = capsys.readouterr().err.splitlines()
    assert "\n".join(lines).count("No space left on device") == 2 * started
    assert summary(lines, "Files") == (
        f"{started}/7 done · {7 - started} waiting · 0 downloaded · {started} failed"
    )
    assert caplog.messages.count("The disk is full; the other files are not downloaded") == 1


def test_download_reports_files_with_other_bytes_than_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The server sends other bytes than `OLDER_FILES` records: archived, but reported."""
    serve_page(monkeypatch, recorded=True)

    args = ["--data-dir", str(tmp_path), "download", "--year", "2022", "--uenb", "amprion"]
    assert cli.main(args) == 1

    lines = capsys.readouterr().err.splitlines()
    row = next(row for row in lines if row.startswith("✓ 2022 amprion"))
    assert ", recorded 6256d028e3d8" in row
    assert lines[lines.index("") + 1].startswith("✗ Finished with changed files ")
    # The legends of the page and of 2022 to 2024 have recorded SHA-256 too
    changed = summary(lines, "Changed")
    assert changed.startswith("legend, 2022 amprion, legend, legend: ")
    assert changed.endswith(": other bytes than recorded, archived as now")
    assert len(list((tmp_path / "raw" / "2022" / "amprion").rglob("*.zip"))) == 1


def test_download_twice_lists_the_archived_files(
    requests: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "amprion"])
    files = len([url for url in requests if url != PAGE_URL])
    capsys.readouterr()

    assert cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "amprion"]) == 0

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].endswith(f", {files} of them already archived")
    rows = lines[1 : lines.index("")]
    assert len(rows) == files
    assert all(row.startswith("= ") and " in the archive since " in row for row in rows)
    assert (
        summary(lines, "Files")
        == f"{files}/{files} done · 0 downloaded · {files} archived · 0 failed"
    )
    assert summary(lines, "Data") == "0 bytes received"


def test_verbose_download_prints_sha256_and_path(
    requests: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["--data-dir", str(tmp_path), "-v", "download", "--uenb", "amprion"])

    lines = capsys.readouterr().err.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("✓ "))
    # File names contain spaces
    sha256, path = lines[first + 1].split(maxsplit=1)
    assert len(sha256) == 64
    assert Path(path).is_file()


def test_download_on_a_terminal_shows_the_same_lines(
    requests: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = io.StringIO()
    terminal = Console(file=out, force_terminal=True, width=120, color_system=None)
    monkeypatch.setattr(display, "console", lambda: terminal)

    assert cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "amprion"]) == 0

    text = out.getvalue()
    files = len([url for url in requests if url != PAGE_URL])
    assert text.count("✓ ") == files + 1
    assert "✓ Done " in text
    assert f"{files}/{files} done" in text


@pytest.mark.parametrize("jobs", [[], ["--jobs", "1"]], ids=["adaptive", "one-job"])
def test_interrupted_download_ends_without_traceback(
    requests: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs: list[str]
) -> None:
    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("eeg_bewegungsdaten.download.download", interrupt)

    assert cli.main(["--data-dir", str(tmp_path), "download", *jobs]) == 130


def test_download_unknown_year_fails(requests: list[str], tmp_path: Path) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "download", "--year", "1999"]) == 1
    assert not (tmp_path / "raw").exists()


def test_data_dir_defaults_to_the_environment_variable(
    requests: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EEG_BEWEGUNGSDATEN_DIR", str(tmp_path / "archive"))

    cli.main(["download", "--uenb", "tennet"])

    assert (tmp_path / "archive" / "raw" / "2025" / "tennet").is_dir()


def test_verify_reports_changed_files(
    requests: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "amprion"])
    assert cli.main(["--data-dir", str(tmp_path), "verify"]) == 0

    archived = next((tmp_path / "raw" / "2025").rglob("*.zip"))
    data = bytearray(archived.read_bytes())
    data[-1] ^= 0xFF
    archived.write_bytes(bytes(data))

    assert cli.main(["--data-dir", str(tmp_path), "verify"]) == 1


def test_verify_without_archive_fails(tmp_path: Path) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "verify"]) == 1


@pytest.mark.parametrize(("damage", "message"), [("truncate", "size"), ("delete", "missing")])
def test_verify_reports_incomplete_files_among_intact_ones(
    requests: list[str], tmp_path: Path, caplog: pytest.LogCaptureFixture, damage: str, message: str
) -> None:
    cli.main(["--data-dir", str(tmp_path), "download", "--year", "2025"])
    path = next((tmp_path / "raw" / "2025").rglob("*.zip"))
    if damage == "truncate":
        path.write_bytes(path.read_bytes()[:-1])
    else:
        path.unlink()

    assert cli.main(["--data-dir", str(tmp_path), "verify"]) == 1
    assert str(path) in caplog.text
    assert message in caplog.text
