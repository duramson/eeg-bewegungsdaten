"""Shared test setup: keep offline tests offline, opt in to live and real-data tests."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from eeg_bewegungsdaten import _version

OPT_IN = {
    "live": ("--live", "calls netztransparenz.de"),
    "realdata": ("--realdata", "reads the downloaded files from the data folder"),
}


def pytest_addoption(parser: pytest.Parser) -> None:
    for option, help_text in OPT_IN.values():
        parser.addoption(option, action="store_true", help=f"also run tests that {help_text}")


def pytest_configure(config: pytest.Config) -> None:
    for marker, (option, help_text) in OPT_IN.items():
        config.addinivalue_line("markers", f"{marker}: {help_text} (skipped unless {option})")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, (option, help_text) in OPT_IN.items():
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=f"{help_text}; run with {option}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if an offline test tries to reach the network."""
    if "live" in request.keywords:
        return

    def refuse(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"Network access in offline test: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Write outputs as an installed package does, whatever the state of the checkout the tests
    run from. Tests of a checkout replace `_version._git`."""
    monkeypatch.setattr(_version, "_git", lambda cwd, *args: None)
    _version._checkout.cache_clear()
    yield
    _version._checkout.cache_clear()
