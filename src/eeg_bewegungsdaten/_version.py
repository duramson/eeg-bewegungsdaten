"""The version of the tool and, when it runs from a git checkout, its commit.

The version comes from the package metadata and stays the same between releases. So outputs
built from a checkout also record the commit and whether tracked files differ from it. An
installed package records the version only.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from functools import cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__version__ = version("eeg-bewegungsdaten")

# Where the package lies in a checkout. Anywhere else, such as in the .venv of a repository, it
# is installed, and the commit of that repository says nothing about it.
CHECKOUT_PATH = "src/eeg_bewegungsdaten/"
# Seconds to wait for git
GIT_TIMEOUT = 10
# Variables that point git at a repository (`git rev-parse --local-env-vars`). A hook or
# `git rebase -x` sets them for its own repository; git must find the checkout from its folder.
REPOSITORY_ENV = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)

# Makes threads that ask at the same time wait for the first answer instead of running git too.
_lock = threading.Lock()


def tool_info() -> dict[str, Any]:
    """The keys that reports and metadata record of the tool: `tool_version`, and `tool_commit`
    and `tool_dirty` for a run from a checkout, None for both otherwise."""
    with _lock:
        commit, dirty = _checkout(Path(__file__).parent)
    return {"tool_version": __version__, "tool_commit": commit, "tool_dirty": dirty}


def tool_text(
    content: dict[str, Any], commit: str = "commit", changes: str = "with local changes"
) -> str:
    """The version of the tool in `content`, with the commit for a run from a checkout. `commit`
    and `changes` are the words for them, for text in another language."""
    if not content.get("tool_commit"):
        return str(content["tool_version"])
    dirty = f", {changes}" if content.get("tool_dirty") else ""
    return f"{content['tool_version']} ({commit} {content['tool_commit']}{dirty})"


@cache
def _checkout(package: Path) -> tuple[str | None, bool | None]:
    """The commit of the checkout that holds `package`, and whether tracked files differ from it.
    Cached, so that git runs once per run and not once per file."""
    head = _git(package, "rev-parse", "--show-prefix", "HEAD")
    lines = head.splitlines() if head else []
    if len(lines) != 2 or lines[0] != CHECKOUT_PATH:
        return None, None
    status = _git(package, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no")
    if status is None:
        log.warning("git status failed in %s; the commit is not recorded", package)
        return None, None
    return lines[1], bool(status.strip())


def _git(cwd: Path, *args: str) -> str | None:
    """The output of git, or None if git is missing, fails or takes too long."""
    env = {name: value for name, value in os.environ.items() if name not in REPOSITORY_ENV}
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            # File names in the output of git status need not be UTF-8; only the commit is read.
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=GIT_TIMEOUT,
            env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
