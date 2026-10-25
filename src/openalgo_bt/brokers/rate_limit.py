"""Nonblocking, broker-local request admission using monotonic time."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable


class BrokerRequestDeferred(RuntimeError):
    """A local request budget or server cooldown refused admission."""

    def __init__(self, retry_after: float):
        super().__init__("Broker request deferred by request budget")
        self.retry_after = retry_after


class BrokerRequestLimiter:
    """Keep separate read/write rolling windows and one shared cooldown."""

    def __init__(self, read_rate: int, write_rate: int, *, clock: Callable[[], float] | None = None):
        for name, rate in (("api_read_rate", read_rate), ("api_write_rate", write_rate)):
            if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._limits = {False: read_rate, True: write_rate}
        self._requests: dict[bool, deque[float]] = {False: deque(), True: deque()}
        self._clock = clock if clock is not None else lambda: time.monotonic()
        self._not_before = 0.0
        self._lock = threading.Lock()

    def acquire(self, *, write: bool = False) -> None:
        """Consume one slot or fail immediately; never wait while holding a lock."""
        with self._lock:
            now = self._clock()
            requests = self._requests[write]
            while requests and requests[0] <= now - 1.0:
                requests.popleft()
            delay = self._not_before - now
            if len(requests) >= self._limits[write]:
                delay = max(delay, requests[0] + 1.0 - now)
            if delay > 0:
                raise BrokerRequestDeferred(delay)
            requests.append(now)

    def defer(self, retry_after: float) -> None:
        """Extend both budgets' cooldown after a classified server throttle."""
        if isinstance(retry_after, bool):
            retry_after = 60.0
        try:
            delay = float(retry_after)
        except (TypeError, ValueError, OverflowError):
            delay = 60.0
        if not math.isfinite(delay) or delay <= 0:
            delay = 60.0
        with self._lock:
            now = self._clock()
            deadline = now + delay
            if not math.isfinite(deadline):
                deadline = now + 60.0
            self._not_before = max(self._not_before, deadline)
