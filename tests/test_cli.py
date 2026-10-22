from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from eeg_bewegungsdaten import cli, links
from eeg_bewegungsdaten.links import EARLIER_FILES, PAGE_URL, older_links, parse_links
from tests.builders import PAGE, zip_bytes
from tests.test_zip_range import range_response

# The older files that `find_links` adds to the page
OLDER = older_links(parse_links(PAGE))


def serve_page(
    monkeypatch: pytest.MonkeyPatch, failing: Callable[[str], bool] = lambda url: False
) -> list[str]:
    """Serve the fixture page and a tiny ZIP or CSV for every file link, or 429 for the `failing`
    ones; return the list of requested URLs."""
    seen: list[str] = []
    bodies = {".zip": zip_bytes({"a.csv": b"x\r\n"}), ".csv": b"a;b\r\n1;2\r\n"}

    def body(url: str) -> bytes:
        return bodies[".csv" if url.lower().endswith(".csv") else ".zip"]

    member_body = bodies[".csv"]
    remote_zip = zip_bytes(
        {link.zip_member: member_body for link in EARLIER_FILES if link.zip_member}
    )

    # The files before 2016 must have their recorded SHA-256: record the hashes of the bodies.
    monkeypatch.setattr(
        links,
        "EARLIER_FILES",
        tuple(
            dataclasses.replace(
                link,
                sha256=hashlib.sha256(
                    member_body if link.zip_member else body(link.url)
                ).hexdigest(),
                size=len(member_body) if link.zip_member else link.size,
            )
            for link in EARLIER_FILES
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Count logical downloads, not the internal ZIP range requests.
        if "Range" not in request.headers or request.headers["Range"].startswith("bytes=-"):
            seen.append(url)
        if url == PAGE_URL:
            return httpx.Response(200, text=PAGE)
        if failing(url):
            return httpx.Response(429, text="slow down")
        if url == links.ZENODO_URL:
            return range_response(request, remote_zip)
        return httpx.Response(200, content=body(url))

    def new_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "new_client", new_client)
    monkeypatch.setattr("eeg_bewegungsdaten.links.new_client", new_client)
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
    assert lines[5].split("\t")[:3] == ["2011", "amprion", "2012-07-31"]
    assert lines[5 + len(EARLIER_FILES)].split("\t")[:3] == ["2016", "50hertz", "2017-11-30"]


def test_download_filters_by_tso_and_keeps_the_legend(
    requests: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "download", "--uenb", "amprion"]) == 0

    downloaded = [url for url in requests if url != PAGE_URL]
    older = [link.url for link in OLDER if link.uenb in ("amprion", None)]
    assert len(downloaded) == 2 + len(older)
    assert "amprion" in downloaded[0]
    assert downloaded[1].endswith("bewegungsdaten_legende.xlsx")
    assert downloaded[2:] == older
    assert len(capsys.readouterr().out.splitlines()) == len(downloaded)


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

    archived = capsys.readouterr().out.splitlines()
    older = [link for link in OLDER if link.uenb in ("tennet", None)]
    # TenneT 2025 and the legend from the page, all older files but the one for 2017
    assert len(archived) == 2 + len(older) - 1
    assert not [p for p in (tmp_path / "raw" / "2017").rglob("*") if p.is_file()]
    assert (tmp_path / "raw" / "2018" / "tennet").is_dir()


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
