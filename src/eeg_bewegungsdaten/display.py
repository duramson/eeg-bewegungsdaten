"""Terminal output of the command line interface, on stderr.

Commands that work through a list of files (`download`, `ingest`) share one display. On a
terminal it keeps a live area at the bottom: the running item with its bar, the next items in the
queue, a rule and a summary that updates while the run goes on. Each finished item prints a line
above it. Elsewhere (a pipe, CI, a log file) only these lines and the final summary are printed,
with ASCII symbols if the output cannot encode Unicode. All colors come from `THEME`.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, TypeVar
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

_Display = TypeVar("_Display", bound="RunDisplay")

#: Items of the queue shown below the running one
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
    done: str
    skipped: str
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
    """The time of one item: tenths of a second below a minute."""
    return f"{seconds:.1f}s" if seconds < 60 else _duration(seconds)


def _rate(size: float, seconds: float) -> str:
    return f"{decimal(int(size / seconds))}/s" if seconds > 0 else "-"


def _source(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return "Wayback Machine" if host == "web.archive.org" else host.removeprefix("www.")


def reason(error: Exception) -> str:
    """A short cause for the line of a failed item; details follow in the summary."""
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
class Item:
    """One file or upload of a run and what is known about it so far."""

    key: str
    year: int | None
    name: str
    part: str = ""
    skip: bool = False
    """Known in advance to need no work, such as a file already in the archive."""
    total: int | None = None
    """Bytes to process, if known before the item starts."""
    state: str = "waiting"  # running, done, skipped or failed
    started: float = 0.0
    seconds: float = 0.0
    amount: int = 0
    result: Any = None
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


class RunDisplay:
    """The shared display of a run over `items`; subclasses fill in the columns and numbers."""

    #: Words for the states in the summary, and their style
    states: ClassVar[dict[str, tuple[str, str]]] = {
        "running": ("running", "eeg.accent"),
        "waiting": ("waiting", "eeg.muted"),
        "done": ("done", "eeg.ok"),
        "skipped": ("skipped", "eeg.muted"),
        "failed": ("failed", "eeg.error"),
    }
    #: What the items are, for the summary
    noun = "Files"
    #: What to do about failed items
    retry_hint = "Run the same command again to retry the failed items."

    def __init__(self, output: Console, items: Sequence[Item], *, verbose: bool = False) -> None:
        self.output = output
        self.items = list(items)
        self.verbose = verbose
        self.symbols = _symbols(output)
        self._by_key = {item.key: item for item in self.items}
        self._name_width = max((len(item.name) for item in self.items), default=0)
        self._part_width = max((len(item.part) for item in self.items), default=0)
        self._started = time.monotonic()
        self._live: Live | None = None
        self._lock = threading.Lock()
        self._interrupted = False

    # What subclasses fill in

    def header(self) -> Text:
        raise NotImplementedError

    def done_cells(self, item: Item) -> list[Text]:
        """The columns after the name of a finished item."""
        return []

    def skipped_cells(self, item: Item) -> list[Text]:
        return [Text("skipped", "eeg.muted")]

    def failure_detail(self, item: Item) -> str | None:
        """A second line for a failed item in the final summary, such as its URL."""
        return None

    def summary_lines(self, now: float, final: bool) -> list[tuple[str, Text | str]]:
        """Lines between the counts and the time, such as rows or bytes."""
        return []

    def final_lines(self) -> list[tuple[str, Text | str]]:
        """Lines after the time in the final summary, such as where the output went."""
        return []

    def running_note(self, item: Item) -> str | None:
        """Text instead of speed and time left, e.g. for a phase that has no progress."""
        return None

    # Lines of single items

    def _name(self, item: Item) -> str:
        """Year, name and part, padded to the widest of the run."""
        name = f"{item.year or '-':<4} {item.name:<{self._name_width}}"
        if self._part_width:
            name += f" {item.part:<{self._part_width}}"
        return name

    def _row(self, symbol: Text, item: Item, cells: list[Text]) -> Text:
        """One item as a line of text: it never gets squeezed, whatever the width of the output."""
        line = Text.assemble(symbol, " ", self._name(item))
        for cell in cells:
            if cell.plain:
                line.append("  ")
                line.append_text(cell)
        return line

    def _done_row(self, item: Item) -> Text:
        if item.state == "failed":
            assert item.error is not None
            took = f"after {_took(item.seconds)}" if item.seconds else ""
            cells = [Text(reason(item.error), "eeg.error"), Text(took, "eeg.muted")]
            return self._row(Text(self.symbols.failed, "eeg.error"), item, cells)
        if item.state == "skipped":
            return self._row(
                Text(self.symbols.skipped, "eeg.muted"), item, self.skipped_cells(item)
            )
        return self._row(Text(self.symbols.done, "eeg.ok"), item, self.done_cells(item))

    def _running_row(self, item: Item, now: float) -> Table:
        """The bar of a running item; only on a terminal, so the width is known."""
        bar = ProgressBar(total=item.total, completed=item.amount, width=28, pulse=not item.total)
        amount = decimal(item.amount)
        if item.total:
            amount += f" / {decimal(item.total)}"
        note = self.running_note(item)
        speed = item.speed(now)
        rate = f"{decimal(int(speed))}/s" if speed and note is None else ""
        left = note or ""
        if note is None and item.total and speed > 0:
            left = f"{_duration((item.total - item.amount) / speed)} left"
        grid = Table.grid(padding=(0, 1))
        grid.add_row(
            item.spinner,
            self._name(item),
            bar,
            Text(f"{amount:>21}"),
            Text(f"{rate:>10}"),
            Text(left, "eeg.muted"),
        )
        return grid

    def _waiting_row(self, item: Item) -> Text:
        note = self.skipped_cells(item)[0].plain if item.skip else "waiting"
        symbol = Text(self.symbols.waiting, "eeg.muted")
        return self._row(symbol, item, [Text(note, "eeg.muted")])

    # The summary

    def _received(self) -> int:
        return sum(item.amount for item in self.items if item.state in ("running", "done"))

    def _busy(self, now: float) -> float:
        """Seconds spent on items that did some work."""
        return sum(i.seconds for i in self.items if i.state == "done") + sum(
            now - i.started for i in self.items if i.state == "running"
        )

    def _expected(self) -> int | None:
        """Bytes of the whole run: the known sizes, and their average for the unknown ones."""
        work = [i for i in self.items if i.state in ("running", "done", "waiting") and not i.skip]
        known = [i.total for i in work if i.total]
        if not known:
            return None
        unknown = sum(1 for i in work if not i.total)
        return sum(known) + unknown * sum(known) // len(known)

    def _summary(self, now: float, final: bool = False) -> Group:
        s = self.symbols.separator
        counts = dict.fromkeys(self.states, 0)
        for item in self.items:
            counts[item.state] += 1
        lines: list[RenderableType] = []

        def add(name: str, value: Text | str) -> None:
            lines.append(Text.assemble((f"{name:<9}", "eeg.label"), value))

        finished = counts["done"] + counts["skipped"] + counts["failed"]
        line = Text(f"{finished}/{len(self.items)} done")
        for state, (word, style) in self.states.items():
            if counts[state] or state in ("done", "failed"):
                line.append(s)
                line.append(f"{counts[state]} {word}", style if counts[state] else "")
        add(self.noun, line)

        for name, value in self.summary_lines(now, final):
            add(name, value)

        received, busy, expected = self._received(), self._busy(now), self._expected()
        elapsed = f"{_duration(now - self._started)} elapsed"
        if not final and expected and received and busy > 0:
            left = (expected - received) / (received / busy)
            elapsed += f"{s}about {_duration(max(left, 0))} left"
        add("Time", elapsed)

        if final:
            for name, value in self.final_lines():
                add(name, value)
        else:
            share = min(received / expected, 1.0) if expected else finished / len(self.items)
            bar = ProgressBar(total=1.0, completed=share, width=40)
            overall = Table.grid(padding=(0, 1))
            overall.add_row(Text(f"{'Overall':<8}", "eeg.label"), bar, Text(f"{share:4.0%}"))
            lines.append(overall)
        return Group(*lines)

    def _speed_now(self, now: float) -> float:
        return sum(item.speed(now) for item in self.items if item.state == "running")

    # The live area

    def __rich__(self) -> RenderableType:
        now = time.monotonic()
        with self._lock:
            rows: list[RenderableType] = [
                self._running_row(i, now) for i in self.items if i.state == "running"
            ]
            waiting = [i for i in self.items if i.state == "waiting"]
            rows += [self._waiting_row(i) for i in waiting[:QUEUE_ROWS]]
            if len(waiting) > QUEUE_ROWS:
                more = f"  {self.symbols.ellipsis} {len(waiting) - QUEUE_ROWS} more waiting"
                rows.append(Text(more, "eeg.muted"))
            return Group(*rows, Rule(), self._summary(now))

    def __enter__(self: _Display) -> _Display:
        self.output.push_theme(THEME)
        self.output.print(self.header())
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

    def start(self, key: str) -> Progress:
        """Mark the item as running and return the callback that moves its bar."""
        item = self._by_key[key]
        with self._lock:
            item.state = "running"
            item.started = time.monotonic()

        def progress(done: int, total: int | None) -> None:
            now = time.monotonic()
            with self._lock:
                if done < item.amount:  # a new attempt, e.g. the Wayback copy
                    item.samples.clear()
                item.amount, item.total = done, total
                item.samples.append((now, done))

        return progress

    def finish(self, key: str, result: Any, *, size: int, skipped: bool = False) -> None:
        """Mark the item as done (or skipped) with `size` bytes processed, and print its line."""
        item = self._by_key[key]
        with self._lock:
            item.result = result
            item.state = "skipped" if skipped else "done"
            item.seconds = 0.0 if skipped else time.monotonic() - item.started
            item.amount = 0 if skipped else size
            item.total = size
        self.output.print(self._done_row(item))

    def fail(self, key: str, error: Exception) -> None:
        item = self._by_key[key]
        with self._lock:
            item.error = error
            item.state = "failed"
            item.seconds = time.monotonic() - item.started
            item.amount = 0
        self.output.print(self._done_row(item))

    @property
    def failures(self) -> list[Item]:
        return [item for item in self.items if item.state == "failed"]

    def _final(self) -> None:
        now = time.monotonic()
        failures = self.failures
        if self._interrupted:
            title = Text(f"{self.symbols.failed} Interrupted", "eeg.warn")
        elif failures:
            title = Text(f"{self.symbols.failed} Finished with errors", "eeg.error")
        else:
            title = Text(f"{self.symbols.done} Done", "eeg.ok")
        self.output.print()
        self.output.print(Rule(title, align="left"))
        self.output.print(self._summary(now, final=True))
        if failures:
            self.output.print()
            for item in failures:
                self.output.print(self._done_row(item))
                detail = self.failure_detail(item)
                if detail:
                    # On a line of its own, so that a long URL does not squeeze the columns
                    self.output.print(Text(f"  {detail}", "eeg.muted"))
            self.output.print(self.retry_hint)


def _link_item(link: DownloadLink, archived: set[str]) -> Item:
    part = ""
    if link.part is not None:
        part = f"part {link.part}/{max((*link.expected_parts, link.part))}"
    name = link.uenb or ("legend" if link.kind == "legende" else link.kind)
    return Item(link.url, link.year, name, part, skip=link.url in archived)


class DownloadDisplay(RunDisplay):
    """`download`: size, time, speed, source and checksum per file."""

    states: ClassVar[dict[str, tuple[str, str]]] = {
        **RunDisplay.states,
        "done": ("downloaded", "eeg.ok"),
        "skipped": ("archived", "eeg.muted"),
    }
    retry_hint = "Run the same command again to retry the failed files."

    def __init__(
        self,
        output: Console,
        links: Sequence[DownloadLink],
        archived: set[str],
        data_dir: Path,
        *,
        verbose: bool = False,
    ) -> None:
        super().__init__(output, [_link_item(link, archived) for link in links], verbose=verbose)
        self.links = {link.url: link for link in links}
        self.archived = archived
        self.data_dir = data_dir

    def header(self) -> Text:
        header = Text.assemble(
            ("Downloading ", "eeg.heading"),
            (f"{len(self.items)} files", "eeg.heading"),
            f" to {self.data_dir}",
        )
        if self.archived:
            header.append(f", {len(self.archived)} of them already archived", "eeg.muted")
        return header

    def done_cells(self, item: Item) -> list[Text]:
        meta = item.result.meta
        source = _source(str(meta.get("source_url") or meta["url"]))
        return [
            Text(f"{decimal(int(meta['size'])):>9}"),
            Text(f"{_took(item.seconds):>5}"),
            Text(f"{_rate(int(meta['size']), item.seconds):>10}"),
            Text(f"{source:<18}", "eeg.warn" if source == "Wayback Machine" else "eeg.accent"),
            Text(f"sha256 {meta['sha256'][:12]}", "eeg.muted"),
        ]

    def skipped_cells(self, item: Item) -> list[Text]:
        if item.result is None:  # still in the queue
            return [Text("in the archive", "eeg.muted")]
        meta = item.result.meta
        day = str(meta.get("downloaded_at", ""))[:10]
        return [
            Text(f"{decimal(int(meta['size'])):>9}", "eeg.muted"),
            Text(f"in the archive since {day}", "eeg.muted"),
            Text(f"sha256 {meta['sha256'][:12]}", "eeg.muted"),
        ]

    def failure_detail(self, item: Item) -> str:
        return item.key

    def summary_lines(self, now: float, final: bool) -> list[tuple[str, Text | str]]:
        s = self.symbols.separator
        received, busy = self._received(), self._busy(now)
        data = f"{decimal(received)} received"
        if busy > 0:
            data += f"{s}{_rate(received, busy)} average"
        speed = self._speed_now(now)
        if not final and speed:
            data += f"{s}{decimal(int(speed))}/s now"
        wayback = sum(
            1
            for i in self.items
            if i.state == "done"
            and _source(str(i.result.meta.get("source_url"))) == "Wayback Machine"
        )
        if wayback:
            data += f"{s}{wayback} from the Wayback Machine"
        return [("Data", data)]

    def final_lines(self) -> list[tuple[str, Text | str]]:
        return [("Archive", str(self.data_dir / "raw"))]

    # The calls of `download`, by link

    def finished(self, link: DownloadLink, result: ArchivedFile) -> None:
        size = int(result.meta["size"])
        self.finish(link.url, result, size=size, skipped=not result.downloaded)
        if self.verbose:
            self.output.print(Text(f"  {result.meta['sha256']}  {result.path}", "eeg.muted"))

    def failed(self, link: DownloadLink, error: Exception) -> None:
        self.fail(link.url, error)


@dataclass(frozen=True)
class Upload:
    """What `ingest` reads for one year and TSO."""

    year: int
    uenb: str
    paths: list[Path]
    size: int
    """Uncompressed bytes of its CSV files"""

    @property
    def key(self) -> str:
        return f"{self.year}/{self.uenb}"


class IngestDisplay(RunDisplay):
    """`ingest`: rows, clean rows, quarantine, time and throughput per upload."""

    noun = "Uploads"
    states: ClassVar[dict[str, tuple[str, str]]] = {
        **RunDisplay.states,
        "done": ("read", "eeg.ok"),
    }
    retry_hint = (
        "The outputs of the failed uploads were left as they were. A FormatError usually means "
        "a new file layout: update eeg-bewegungsdaten, or report it."
    )

    def __init__(
        self, output: Console, uploads: Sequence[Upload], data_dir: Path, *, verbose: bool = False
    ) -> None:
        items = [Item(u.key, u.year, u.uenb, total=u.size) for u in uploads]
        super().__init__(output, items, verbose=verbose)
        self.uploads = {u.key: u for u in uploads}
        self.data_dir = data_dir

    def header(self) -> Text:
        size = decimal(sum(u.size for u in self.uploads.values()))
        return Text.assemble(
            ("Reading ", "eeg.heading"),
            (f"{len(self.items)} uploads", "eeg.heading"),
            f" ({size} of CSV) from {self.data_dir / 'raw'}",
        )

    def done_cells(self, item: Item) -> list[Text]:
        rows = item.result["rows"]
        codes = ", ".join(f"{code} {n:,}" for code, n in item.result["quarantine"].items())
        return [
            # Up to 9,999,999 rows per upload in fixed columns
            Text(f"{rows['raw']:>9,} rows"),
            Text(f"{rows['clean']:>9,} clean"),
            Text(
                f"{rows['quarantine']:>7,} quarantine",
                "eeg.warn" if rows["quarantine"] else "eeg.muted",
            ),
            Text(f"{_took(item.seconds):>6}"),
            Text(f"{_rate(item.amount, item.seconds):>10}"),
            Text(codes, "eeg.muted"),
        ]

    def failure_detail(self, item: Item) -> str:
        return ", ".join(str(path) for path in self.uploads[item.key].paths)

    def running_note(self, item: Item) -> str | None:
        if item.total and item.amount >= item.total:
            return "writing the Parquet files"
        return None

    def summary_lines(self, now: float, final: bool) -> list[tuple[str, Text | str]]:
        s = self.symbols.separator
        reports = [i.result for i in self.items if i.state == "done"]
        raw = sum(r["rows"]["raw"] for r in reports)
        quarantine = sum(r["rows"]["quarantine"] for r in reports)
        rows = Text(f"{raw:,} read{s}{raw - quarantine:,} clean{s}")
        rows.append(f"{quarantine:,} quarantine", "eeg.warn" if quarantine else "")
        received, busy = self._received(), self._busy(now)
        data = f"{decimal(received)} of CSV read"
        if busy > 0:
            data += f"{s}{_rate(received, busy)} average"
        speed = self._speed_now(now)
        if not final and speed:
            data += f"{s}{decimal(int(speed))}/s now"
        return [("Rows", rows), ("Data", data)]

    def final_lines(self) -> list[tuple[str, Text | str]]:
        return [
            ("Output", f"{self.data_dir}  (text, clean, quarantine, reports)"),
        ]
