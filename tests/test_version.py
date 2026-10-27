from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from eeg_bewegungsdaten import _version
from eeg_bewegungsdaten._version import __version__, _checkout, _git, tool_info
from tests.builders import COMMIT, fake_git


@pytest.mark.parametrize(("status", "dirty"), [("", False), (" M README.md\n", True)])
def test_a_checkout_records_its_commit_and_whether_tracked_files_changed(
    monkeypatch: pytest.MonkeyPatch, status: str, dirty: bool
) -> None:
    monkeypatch.setattr(_version, "_git", fake_git(status=status))

    assert tool_info() == {"tool_version": __version__, "tool_commit": COMMIT, "tool_dirty": dirty}


def test_an_installed_package_records_the_version_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fixture `_no_git`: git is missing, or the package is not in a repository.
    assert tool_info() == {"tool_version": __version__, "tool_commit": None, "tool_dirty": None}

    # Installed into a .venv inside a checkout: the commit is that of another tree.
    _checkout.cache_clear()
    venv = ".venv/lib/python3.12/site-packages/eeg_bewegungsdaten/"
    monkeypatch.setattr(_version, "_git", fake_git(prefix=venv))
    assert tool_info()["tool_commit"] is None


def test_a_failing_git_status_records_no_commit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(_version, "_git", fake_git(status=None))

    with caplog.at_level(logging.WARNING):
        assert tool_info()["tool_commit"] is None
    assert "git status failed" in caplog.text


def test_git_runs_once_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    git = fake_git()

    def counted(cwd: Path, *args: str) -> str | None:
        calls.append(args)
        return git(cwd, *args)

    monkeypatch.setattr(_version, "_git", counted)
    for _ in range(3):
        assert tool_info()["tool_commit"] == COMMIT
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("git"),
        subprocess.TimeoutExpired("git", 10),
        subprocess.CalledProcessError(128, "git"),
    ],
)
def test_git_that_is_missing_fails_or_hangs_gives_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    def run(*args: Any, **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    assert _git(tmp_path, "rev-parse", "HEAD") is None


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_checkout_reads_a_real_repository(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Keep the settings of this machine, such as signed commits, out of the test.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(_version, "_git", _git)
    package = tmp_path / "repo" / "src" / "eeg_bewegungsdaten"
    package.mkdir(parents=True)
    (package / "code.py").write_text("x = 1\n", encoding="utf-8")

    def git(*args: str) -> str:
        user = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
        done = subprocess.run(["git", *user, *args], cwd=package, capture_output=True, check=True)
        return done.stdout.decode().strip()

    assert _checkout(package) == (None, None)  # not a repository
    git("init", str(tmp_path / "repo"))
    git("add", ".")
    git("commit", "-m", "Add code")
    commit = git("rev-parse", "HEAD")
    _checkout.cache_clear()
    assert _checkout(package) == (commit, False)
    # A file in another folder of a repository is not the package of a checkout.
    assert _checkout(package.parent) == (None, None)

    # Untracked files do not count, changes to tracked ones do.
    (package / "notes.txt").write_text("notes\n", encoding="utf-8")
    _checkout.cache_clear()
    assert _checkout(package) == (commit, False)
    (package / "code.py").write_text("x = 2\n", encoding="utf-8")
    _checkout.cache_clear()
    assert _checkout(package) == (commit, True)
