"""Progress reports of long steps, without a terminal library.

The functions that take a while accept a `Progress` callback. The command line interface turns
its calls into progress bars; library users can pass their own or leave it out.
"""

from __future__ import annotations

from collections.abc import Callable

Progress = Callable[[int, int | None], None]
"""Called with the amount done so far and the total, or None while the total is unknown. A call
with a smaller amount than before starts over, as when a download switches to its Wayback copy."""
