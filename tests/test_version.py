from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from eeg_bewegungsdaten import _version
from eeg_bewegungsdaten._version import (
    CHECKOUT_PATH,
    REPOSITORY_ENV,
    __version__,
    _checkout,
    _git,
    tool_info,
)
from tests.builders import COMMIT, fake_git

ROOT = Path(__file__).parents[1]


def test_checkout_path_is_where_the_package_lies() -> None:
    package = Path(_version.__file__).resolve().parent
    assert package.relative_to(ROOT.resolve()).as_posix() + "/" == CHECKOUT_PATH


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


def test_git_runs_once_per_run_also_for_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    git = fake_git()

    def slow(cwd: Path, *args: str) -> str | None:
        calls.append(args)
        time.sleep(0.01)  # so that the other threads ask before the first answer
        return git(cwd, *args)

    monkeypatch.setattr(_version, "_git", slow)
    with ThreadPoolExecutor(8) as pool:
        infos = list(pool.map(lambda _: tool_info(), range(16)))
    assert {info["tool_commit"] for info in infos} == {COMMIT}
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


@pytest.mark.skipif(os.name == "nt", reason="the stand-in for git is a shell script")
def test_git_output_that_is_not_utf8_is_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # git status with core.quotePath=false prints file names in the encoding of the system.
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nprintf ' M \\201.txt\\n'\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _git(tmp_path, "status") == " M �.txt\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_checkout_reads_a_real_repository(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Keep the settings of this machine, such as signed commits, out of the test, and the
    # repository of a hook or `git rebase -x` that runs the tests.
    for name in REPOSITORY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(_version, "_git", _git)
    package = tmp_path / "repo" / "src" / "eeg_bewegungsdaten"
    package.mkdir(parents=True)
    (package / "code.py").write_text("x = 1\n", encoding="utf-8")

    def git(*args: str) -> str:
        user = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
        env = {name: value for name, value in os.environ.items() if name not in REPOSITORY_ENV}
        done = subprocess.run(
            ["git", *user, *args], cwd=package, env=env, capture_output=True, check=True
        )
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

    # A hook or `git rebase -x` points git at its own repository; the checkout still counts.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other" / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "other" / "index"))
    _checkout.cache_clear()
    assert _checkout(package) == (commit, True)
