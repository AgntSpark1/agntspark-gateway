"""In-memory rate limits for agent ingress.

The edge asks the gateway about every request to every agent, so these checks
stay in process instead of paying a Redis round trip each time. That matches
the gateway's single-process deployment (see scheduler.py); the counts reset
when the process restarts.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable


class FixedWindowLimiter:
    """Counts hits per key in fixed windows aligned to the wall clock.

    Memory is bounded by the keys seen in the current window: every window
    starts from an empty table.
    """

    def __init__(self, window_seconds: int = 60, clock: Callable[[], float] = time.time) -> None:
        self._window_seconds = window_seconds
        self._clock = clock
        self._window = -1
        self._counts: dict[str, int] = {}

    def hit(self, key: str, limit: int) -> int | None:
        """Count one hit for ``key``.

        Returns None if it's within ``limit``, otherwise the seconds until the
        window resets (the hit isn't counted).
        """
        now = self._clock()
        window = int(now // self._window_seconds)
        if window != self._window:
            self._window = window
            self._counts.clear()
        count = self._counts.get(key, 0)
        if count >= limit:
            return max(1, math.ceil((window + 1) * self._window_seconds - now))
        self._counts[key] = count + 1
        return None

    def reset(self) -> None:
        self._counts.clear()


# Per (agent, client IP): stops one caller from running up an agent's usage.
per_client = FixedWindowLimiter()
# Per agent, across callers: the plan's ceiling on what one URL serves.
per_agent = FixedWindowLimiter()


__all__ = ["FixedWindowLimiter", "per_agent", "per_client"]
