"""The version of the tool and, when it runs from a git checkout, its commit.

The version comes from the package metadata and stays the same between releases. So outputs
built from a checkout also record the commit and whether tracked files differ from it. An
installed package records the version only.
"""

from __future__ import annotations

import logging
import subprocess
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


def tool_info() -> dict[str, Any]:
    """The keys that reports and metadata record of the tool: `tool_version`, and `tool_commit`
    and `tool_dirty` for a run from a checkout, None for both otherwise."""
    commit, dirty = _checkout(Path(__file__).parent)
    return {"tool_version": __version__, "tool_commit": commit, "tool_dirty": dirty}


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
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
