"""Links, anchors and tables of the Markdown files, for the tests of the docs and the datacard."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from eeg_bewegungsdaten.datapackage import REPOSITORY

ROOT = Path(__file__).resolve().parents[1]
# The files with links: the Markdown files of the repository and the issue forms
DOCUMENTS = (
    *(ROOT / name for name in ("README.md", "CONTRIBUTING.md")),
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "analysis").glob("*.md")),
    *sorted((ROOT / ".github").glob("**/*.md")),
    *sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml")),
)
# An inline link `[text](target)`, also `(<target> "title")`, and a reference `[label]: target`
_LINK = re.compile(r'\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
_REFERENCE = re.compile(r"\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?.*")
_HTML_ANCHOR = re.compile(r'<a id="([\w-]+)"></a>')
_HEADING = re.compile(r"(#{1,6}) (.+)")
# Pages of the repository on GitHub that are not files
_GITHUB_PAGES = ("/issues", "/pulls", "/pull/", "/releases", "/actions", "/discussions")


def outside_code(text: str) -> list[str]:
    """The lines of a Markdown text that are not in a fenced code block."""
    lines: list[str] = []
    fence = None
    for line in text.splitlines():
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~") and fence in (None, marker):
            fence = None if fence else marker
        elif fence is None:
            lines.append(line)
    return lines


def github_anchor(heading: str) -> str:
    """The anchor that GitHub gives a heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """The anchors of the headings of a Markdown file, numbered as GitHub does when a heading
    repeats, and those of `<a id="…"></a>`."""
    lines = outside_code(path.read_text(encoding="utf-8"))
    found = {anchor for line in lines for anchor in _HTML_ANCHOR.findall(line)}
    seen: Counter[str] = Counter()
    for line in lines:
        if match := _HEADING.fullmatch(line):
            anchor = github_anchor(match[2])
            found.add(f"{anchor}-{seen[anchor]}" if seen[anchor] else anchor)
            seen[anchor] += 1
    return found


def links(text: str) -> list[str]:
    """The targets of the links of a Markdown text, inline and by reference, outside code
    blocks."""
    found = []
    for line in outside_code(text):
        found += _LINK.findall(line)
        if match := _REFERENCE.fullmatch(line):
            found.append(match[1])
    return found


def broken(target: str, source: Path) -> str | None:
    """Why the link `target` in the file `source` leads nowhere, or None if it works.

    Relative links and links to files of this repository on GitHub are checked against the files
    here, with the anchor of a Markdown file. Other web links are not checked. The issue forms and
    templates in `.github` need absolute links: GitHub shows them in issues and pull requests,
    where a relative link leads nowhere.
    """
    if target == REPOSITORY or target.startswith((f"{REPOSITORY}/", f"{REPOSITORY}#")):
        rest = target.removeprefix(REPOSITORY)
        if rest.startswith(_GITHUB_PAGES):
            return None
        match = re.fullmatch(r"(?:/(?:blob|tree)/main/([^#]*))?(?:#(.*))?", rest)
        if match is None:
            return "not a file of the repository"
        path, anchor = ROOT / unquote(match[1] or "README.md"), match[2]
        if anchor == "readme" and path == ROOT / "README.md":
            # Where GitHub shows the README on the page of the repository
            anchor = None
    elif re.match(r"[a-z]+:", target):
        return None
    else:
        file, _, anchor = target.partition("#")
        if file and source.is_relative_to(ROOT / ".github"):
            return "a relative link, which leads nowhere in an issue or pull request"
        path = source.parent / unquote(file) if file else source
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        return f"{path} is outside the repository"
    if not path.exists():
        return f"{path.relative_to(ROOT)} does not exist"
    anchor = unquote(anchor) if anchor else None
    if anchor and path.suffix == ".md" and anchor not in anchors(path):
        return f"{path.relative_to(ROOT)} has no section #{anchor}"
    return None


def sections(path: Path) -> dict[str, list[str]]:
    """The lines under each heading of a Markdown file, outside code blocks, up to the next
    heading."""
    found: dict[str, list[str]] = {}
    current: list[str] = []
    for line in outside_code(path.read_text(encoding="utf-8")):
        if match := _HEADING.fullmatch(line):
            current = found.setdefault(match[2], [])
        else:
            current.append(line)
    return found


def tables(lines: list[str]) -> list[list[list[str]]]:
    """The tables in `lines`: each a list of rows, the header first, without the separator."""
    found: list[list[list[str]]] = []
    previous = ""
    for line in lines:
        if line.startswith("|"):
            if not previous.startswith("|"):
                found.append([])
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                found[-1].append(cells)
        previous = line
    return found
