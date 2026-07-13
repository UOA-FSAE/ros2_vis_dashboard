"""Thread-safe store between transport callbacks and the UI render loop.

Transport threads call :meth:`DataHub.ingest`; the Qt thread reads snapshots on
a timer. Decoupling message rate from paint rate is what keeps the UI smooth:
CAN can arrive at 100 Hz while the panel repaints at 30 Hz, and the panel only
ever touches preallocated arrays.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from fsae_dashboard.data.fields import get_field

# keep this many seconds of history per numeric series
DEFAULT_HISTORY_S = 60.0


class Series:
    """A time-ordered ring buffer of (t, value) samples, bounded by duration."""

    def __init__(self, history_s: float = DEFAULT_HISTORY_S):
        self.history_s = history_s
        self._t: deque[float] = deque()
        self._v: deque[float] = deque()

    def append(self, t: float, v: float) -> None:
        self._t.append(t)
        self._v.append(v)
        cutoff = t - self.history_s
        tq, vq = self._t, self._v
        while tq and tq[0] < cutoff:
            tq.popleft()
            vq.popleft()

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        return np.fromiter(self._t, dtype=float), np.fromiter(self._v, dtype=float)

    def latest(self) -> float | None:
        return self._v[-1] if self._v else None


class _RateTracker:
    def __init__(self) -> None:
        self._times: deque[float] = deque(maxlen=100)

    def tick(self, t: float) -> None:
        self._times.append(t)

    def hz(self, now: float) -> float:
        window = [t for t in self._times if now - t <= 1.0]
        return float(len(window))


class DataHub:
    def __init__(self, history_s: float = DEFAULT_HISTORY_S):
        self.history_s = history_s
        self._lock = threading.RLock()
        self._latest: dict[str, dict] = {}
        self._series: dict[tuple[str, str], Series] = {}
        self._rates: dict[str, _RateTracker] = {}
        self._first_seen: dict[str, float] = {}
        # (topic, msg, t) taps — used by recorders to observe every message.
        self._listeners: list[Callable[[str, dict, float], None]] = []

    # --- taps (recorders etc.) --------------------------------------------
    def add_listener(self, cb: Callable[[str, dict, float], None]) -> None:
        """Register a callback fired for every ingested message: cb(topic, msg, t).

        Called on the transport thread. Callbacks must be quick and must not
        raise; exceptions are swallowed so one bad tap can't stall ingestion.
        """
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[str, dict, float], None]) -> None:
        with self._lock:
            if cb in self._listeners:
                self._listeners.remove(cb)

    # --- write side (transport threads) -----------------------------------
    def ingest(self, topic: str, msg: dict) -> None:
        now = time.time()
        with self._lock:
            self._latest[topic] = msg
            self._first_seen.setdefault(topic, now)
            self._rates.setdefault(topic, _RateTracker()).tick(now)
            # update only the series that have been requested for this topic
            for (t, field), series in self._series.items():
                if t != topic:
                    continue
                val = get_field(msg, field)
                if val is not None:
                    series.append(now, val)
            listeners = list(self._listeners)
        # fire taps outside the lock: writing a recording to disk must not
        # block other ingesting threads or the UI-thread readers.
        for cb in listeners:
            try:
                cb(topic, msg, now)
            except Exception:  # noqa: BLE001
                pass

    # --- read side (Qt thread) --------------------------------------------
    def latest(self, topic: str) -> dict | None:
        with self._lock:
            return self._latest.get(topic)

    def ensure_series(self, topic: str, field: str) -> Series:
        key = (topic, field)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = Series(self.history_s)
                self._series[key] = series
            return series

    def series_snapshot(self, topic: str, field: str) -> tuple[np.ndarray, np.ndarray]:
        return self.ensure_series(topic, field).snapshot()

    def rate(self, topic: str) -> float:
        with self._lock:
            tracker = self._rates.get(topic)
            return tracker.hz(time.time()) if tracker else 0.0

    def seen_topics(self) -> list[str]:
        with self._lock:
            return sorted(self._latest.keys())

    def clear(self) -> None:
        with self._lock:
            self._latest.clear()
            self._series.clear()
            self._rates.clear()
            self._first_seen.clear()
