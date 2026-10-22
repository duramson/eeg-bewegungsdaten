"""Terminal output of the command line interface, on stderr.

On a terminal, `download` keeps a live area at the bottom: the running file with its bar, the
next files in the queue, a rule and a summary that updates while the run goes on. Each finished
file prints a line above it, with size, time, average speed, source and checksum. Elsewhere (a
pipe, CI, a log file) only these lines and the final summary are printed, with ASCII symbols if
the output cannot encode Unicode.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit

import httpx
from rich.console import Console, Group, RenderableType
from rich.filesize import decimal
from rich.live import Live
from rich.logging import RichHandler
from rich.progress_bar import ProgressBar
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from eeg_bewegungsdaten.download import ArchivedFile
from eeg_bewegungsdaten.links import DownloadLink
from eeg_bewegungsdaten.progress import Progress

#: The colors of all output, by role: one accent, muted states, graphite for the rest. Text
#: without a role keeps the terminal's own color, so that light terminals stay readable.
THEME = Theme(
    {
        "eeg.accent": "#5fa8a0",
        "eeg.ok": "#8fb38a",
        "eeg.warn": "#c9a86a",
        "eeg.error": "#c8756b",
        "eeg.muted": "#5d636d",
        "eeg.label": "#8b929c",
        "eeg.track": "#2c3038",
        "eeg.heading": "bold",
        # rich's own names, for bars, rules and log levels
        "bar.back": "#2c3038",
        "bar.complete": "#5fa8a0",
        "bar.finished": "#8fb38a",
        "bar.pulse": "#5fa8a0",
        "rule.line": "#2c3038",
        "logging.level.debug": "#5d636d",
        "logging.level.info": "#8b929c",
        "logging.level.warning": "#c9a86a",
        "logging.level.error": "bold #c8756b",
        "logging.level.critical": "bold #c8756b",
    }
)

#: Files of the queue shown below the running one
QUEUE_ROWS = 4
#: Seconds over which the current speed is measured
SPEED_WINDOW = 3.0


def console() -> Console:
    """The console of one command. Lines are never wrapped, so that paths and URLs stay whole."""
    return Console(stderr=True, highlight=False, soft_wrap=True, theme=THEME)


def log_handler(output: Console) -> logging.Handler:
    """Log records above the live area on a terminal, as plain lines elsewhere."""
    if output.is_terminal:
        return RichHandler(console=output, show_time=False, show_path=False, markup=False)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


@dataclass(frozen=True)
class _Symbols:
    downloaded: str
    archived: str
    failed: str
    waiting: str
    separator: str
    ellipsis: str


_UNICODE = _Symbols("✓", "=", "✗", "·", " · ", "…")
_ASCII = _Symbols("+", "=", "x", ".", " | ", "...")


def _symbols(output: Console) -> _Symbols:
    try:
        "".join(vars(_UNICODE).values()).encode(output.encoding)
    except (UnicodeEncodeError, LookupError):
        return _ASCII
    return _UNICODE


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _took(seconds: float) -> str:
    """The time of one file: tenths of a second below a minute."""
    return f"{seconds:.1f}s" if seconds < 60 else _duration(seconds)


def _rate(size: float, seconds: float) -> str:
    return f"{decimal(int(size / seconds))}/s" if seconds > 0 else "-"


def _source(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return "Wayback Machine" if host == "web.archive.org" else host.removeprefix("www.")


def reason(error: Exception) -> str:
    """A short cause for the line of a failed file; the URL is printed in the summary."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code} {error.response.reason_phrase}"
    if isinstance(error, httpx.TimeoutException):
        return "timed out"
    if isinstance(error, httpx.HTTPError):
        return f"{type(error).__name__}: {error}"
    message = str(error)
    # DownloadError messages start with the URL.
    if message.startswith(("http://", "https://")) and ": " in message:
        return message.split(": ", 1)[1]
    return message


@dataclass
class _File:
    """One file of the run and what is known about it so far."""

    link: DownloadLink
    archived: bool
    state: str = "waiting"  # running, downloaded, archived or failed
    started: float = 0.0
    seconds: float = 0.0
    received: int = 0
    total: int | None = None
    result: ArchivedFile | None = None
    error: Exception | None = None
    samples: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=64))
    spinner: Spinner = field(default_factory=lambda: Spinner("dots", style="eeg.accent"))

    def speed(self, now: float) -> float:
        """Bytes per second over the last `SPEED_WINDOW` seconds."""
        while len(self.samples) > 2 and now - self.samples[1][0] > SPEED_WINDOW:
            self.samples.popleft()
        if len(self.samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
        return (b1 - b0) / (t1 - t0) if t1 > t0 else 0.0


@dataclass
class DownloadDisplay:
    """What `download` prints: a header, a line per file, the live area, a summary."""

    output: Console
    links: list[DownloadLink]
    archived: set[str]
    data_dir: Path
    verbose: bool = False
    files: list[_File] = field(init=False)
    symbols: _Symbols = field(init=False)
    _by_url: dict[str, _File] = field(init=False)
    _started: float = field(init=False)
    _live: Live | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _interrupted: bool = False

    def __post_init__(self) -> None:
        self.files = [_File(link, link.url in self.archived) for link in self.links]
        self._by_url = {file.link.url: file for file in self.files}
        self.symbols = _symbols(self.output)
        self._started = time.monotonic()
        self._uenb_width = max(len(self._uenb(link)) for link in self.links)
        self._part_width = max(len(self._part(link)) for link in self.links)

    # Lines of single files

    @staticmethod
    def _uenb(link: DownloadLink) -> str:
        return link.uenb or ("legend" if link.kind == "legende" else link.kind)

    @staticmethod
    def _part(link: DownloadLink) -> str:
        if link.part is None:
            return ""
        return f"part {link.part}/{max((*link.expected_parts, link.part))}"

    def _name(self, link: DownloadLink) -> str:
        """Year, TSO and part, padded to the widest of the run."""
        name = f"{link.year or '-':<4} {self._uenb(link):<{self._uenb_width}}"
        if self._part_width:
            name += f" {self._part(link):<{self._part_width}}"
        return name

    def _row(self, symbol: Text, link: DownloadLink, *rest: Text) -> Text:
        """One file as a line of text: it never gets squeezed, whatever the width of the output."""
        line = Text.assemble(symbol, " ", self._name(link))
        for part in rest:
            if part.plain:
                line.append("  ")
                line.append_text(part)
        return line

    def _done_row(self, file: _File) -> Text:
        result = file.result
        if file.state == "failed":
            assert file.error is not None
            took = f"after {_took(file.seconds)}" if file.seconds else ""
            return self._row(
                Text(self.symbols.failed, "eeg.error"),
                file.link,
                Text(reason(file.error), "eeg.error"),
                Text(took, "eeg.muted"),
            )
        assert result is not None
        size = Text(f"{decimal(int(result.meta['size'])):>9}")
        checksum = Text(f"sha256 {result.meta['sha256'][:12]}", "eeg.muted")
        if file.state == "archived":
            day = str(result.meta.get("downloaded_at", ""))[:10]
            return self._row(
                Text(self.symbols.archived, "eeg.muted"),
                file.link,
                Text(size.plain, "eeg.muted"),
                Text(f"in the archive since {day}", "eeg.muted"),
                checksum,
            )
        source = _source(str(result.meta.get("source_url") or result.meta["url"]))
        return self._row(
            Text(self.symbols.downloaded, "eeg.ok"),
            file.link,
            size,
            Text(f"{_took(file.seconds):>5}"),
            Text(f"{_rate(int(result.meta['size']), file.seconds):>10}"),
            Text(f"{source:<18}", "eeg.warn" if source == "Wayback Machine" else "eeg.accent"),
            checksum,
        )

    def _running_row(self, file: _File, now: float) -> Table:
        """The bar of a running file; only on a terminal, so the width is known."""
        bar = ProgressBar(
            total=file.total,
            completed=file.received,
            width=28,
            pulse=file.total is None,
        )
        amount = decimal(file.received)
        if file.total:
            amount += f" / {decimal(file.total)}"
        speed = file.speed(now)
        left = ""
        if file.total and speed > 0:
            left = f"{_duration((file.total - file.received) / speed)} left"
        grid = Table.grid(padding=(0, 1))
        grid.add_row(
            file.spinner,
            self._name(file.link),
            bar,
            Text(f"{amount:>21}"),
            Text(f"{decimal(int(speed)) + '/s' if speed else '':>10}"),
            Text(left, "eeg.muted"),
        )
        return grid

    def _waiting_row(self, file: _File) -> Text:
        note = "in the archive" if file.archived else "waiting"
        return self._row(
            Text(self.symbols.waiting, "eeg.muted"), file.link, Text(note, "eeg.muted")
        )

    # The summary

    def _counts(self) -> dict[str, int]:
        counts = dict.fromkeys(("waiting", "running", "downloaded", "archived", "failed"), 0)
        for file in self.files:
            counts[file.state] += 1
        return counts

    def _summary(self, now: float, final: bool = False) -> Group:
        counts = self._counts()
        s = self.symbols.separator
        downloaded = [f for f in self.files if f.state == "downloaded"]
        received = sum(f.received for f in self.files if f.state in ("downloaded", "running"))
        busy = sum(f.seconds for f in downloaded) + sum(
            now - f.started for f in self.files if f.state == "running"
        )
        wayback = sum(
            1
            for f in downloaded
            if f.result and _source(str(f.result.meta.get("source_url"))) == "Wayback Machine"
        )

        lines: list[RenderableType] = []

        def add(name: str, value: Text | str) -> None:
            lines.append(Text.assemble((f"{name:<9}", "eeg.label"), value))

        files = Text()
        done = counts["downloaded"] + counts["archived"] + counts["failed"]
        files.append(f"{done}/{len(self.files)} done")
        for state, style in (
            ("running", "eeg.accent"),
            ("waiting", "eeg.muted"),
            ("downloaded", "eeg.ok"),
            ("archived", "eeg.muted"),
            ("failed", "eeg.error"),
        ):
            if counts[state] or state in ("downloaded", "failed"):
                files.append(s)
                files.append(f"{counts[state]} {state}", style if counts[state] else "")
        add("Files", files)

        data = f"{decimal(received)} received"
        if busy > 0:
            data += f"{s}{_rate(received, busy)} average"
        now_speed = sum(f.speed(now) for f in self.files if f.state == "running")
        if not final and now_speed:
            data += f"{s}{decimal(int(now_speed))}/s now"
        if wayback:
            data += f"{s}{wayback} from the Wayback Machine"
        add("Data", data)

        elapsed = f"{_duration(now - self._started)} elapsed"
        left = self._time_left(now, received, busy)
        if not final and left is not None:
            elapsed += f"{s}about {_duration(left)} left"
        add("Time", elapsed)

        if not final:
            expected = self._expected_bytes(received)
            bar = ProgressBar(
                total=expected or len(self.files),
                completed=received if expected else done,
                width=40,
            )
            share = received / expected if expected else done / len(self.files)
            overall = Table.grid(padding=(0, 1))
            overall.add_row(Text("Overall  ", "eeg.label"), bar, Text(f"{share:4.0%}"))
            lines.append(overall)
        else:
            add("Archive", str(self.data_dir / "raw"))
        return Group(*lines)

    def _expected_bytes(self, received: int) -> int | None:
        """Bytes of the whole run: known sizes, and the average size for the files not started."""
        sizes = [f.total for f in self.files if f.state in ("downloaded", "running") and f.total]
        if not sizes:
            return None
        pending = sum(1 for f in self.files if f.state == "waiting" and not f.archived)
        running_left = sum((f.total or 0) - f.received for f in self.files if f.state == "running")
        return received + running_left + pending * sum(sizes) // len(sizes)

    def _time_left(self, now: float, received: int, busy: float) -> float | None:
        expected = self._expected_bytes(received)
        if expected is None or busy <= 0 or received <= 0:
            return None
        return (expected - received) / (received / busy)

    # The live area

    def __rich__(self) -> RenderableType:
        now = time.monotonic()
        with self._lock:
            rows: list[RenderableType] = [
                self._running_row(f, now) for f in self.files if f.state == "running"
            ]
            waiting = [f for f in self.files if f.state == "waiting"]
            rows += [self._waiting_row(f) for f in waiting[:QUEUE_ROWS]]
            if len(waiting) > QUEUE_ROWS:
                more = f"  {self.symbols.ellipsis} {len(waiting) - QUEUE_ROWS} more waiting"
                rows.append(Text(more, "eeg.muted"))
            return Group(*rows, Rule(), self._summary(now))

    def __enter__(self) -> DownloadDisplay:
        self.output.push_theme(THEME)
        header = Text()
        header.append("Downloading ", "eeg.heading")
        header.append(f"{len(self.files)} files", "eeg.heading")
        header.append(f" to {self.data_dir}")
        if self.archived:
            header.append(f", {len(self.archived)} of them already archived", "eeg.muted")
        self.output.print(header)
        if self.output.is_terminal:
            self._live = Live(self, console=self.output, refresh_per_second=8, transient=True)
            self._live.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._live is not None:
            self._live.stop()
        self._interrupted = kind is KeyboardInterrupt
        if kind is None or self._interrupted:
            self._final()
        self.output.pop_theme()

    # Calls from the command

    def start(self, link: DownloadLink) -> Progress | None:
        """Mark `link` as running and return the callback that moves its bar."""
        file = self._by_url[link.url]
        with self._lock:
            file.state = "running"
            file.started = time.monotonic()

        def progress(done: int, total: int | None) -> None:
            now = time.monotonic()
            with self._lock:
                if done < file.received:  # a new attempt, e.g. the Wayback copy
                    file.samples.clear()
                file.received, file.total = done, total
                file.samples.append((now, done))

        return progress

    def finished(self, link: DownloadLink, result: ArchivedFile) -> None:
        file = self._by_url[link.url]
        with self._lock:
            file.result = result
            file.state = "downloaded" if result.downloaded else "archived"
            file.seconds = time.monotonic() - file.started if result.downloaded else 0.0
            file.received = int(result.meta["size"]) if result.downloaded else 0
            file.total = int(result.meta["size"])
        self.output.print(self._done_row(file))
        if self.verbose:
            self.output.print(Text(f"  {result.meta['sha256']}  {result.path}", "eeg.muted"))

    def failed(self, link: DownloadLink, error: Exception) -> None:
        file = self._by_url[link.url]
        with self._lock:
            file.error = error
            file.state = "failed"
            file.seconds = time.monotonic() - file.started
            file.received = 0
        self.output.print(self._done_row(file))

    @property
    def failures(self) -> list[_File]:
        return [file for file in self.files if file.state == "failed"]

    def _final(self) -> None:
        now = time.monotonic()
        failures = self.failures
        if self._interrupted:
            title = Text(f"{self.symbols.failed} Interrupted", "eeg.warn")
        elif failures:
            title = Text(f"{self.symbols.failed} Finished with errors", "eeg.error")
        else:
            title = Text(f"{self.symbols.downloaded} Done", "eeg.ok")
        self.output.print()
        self.output.print(Rule(title, align="left"))
        self.output.print(self._summary(now, final=True))
        if failures:
            self.output.print()
            for file in failures:
                assert file.error is not None
                self.output.print(self._done_row(file))
                # On a line of its own, so that a long URL does not squeeze the columns
                self.output.print(Text(f"  {file.link.url}", "eeg.muted"))
            self.output.print("Run the same command again to retry the failed files.")
