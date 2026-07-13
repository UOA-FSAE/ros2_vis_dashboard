"""Record a single topic to rolling JSON Lines files.

Each message is one self-contained line: ``{"t": <epoch>, "topic": ..., "msg": {...}}``.
JSONL (not one big JSON array) is deliberate — every line stands alone, so a
crash mid-write loses at most the last partial line rather than corrupting the
whole file, and downstream tools can stream it. When a file hits the record or
byte cap it is closed and recording continues in the next-numbered file in the
same folder, so no single file grows unbounded.
"""
from __future__ import annotations

import json
import os
import threading
import time

from fsae_dashboard.data.hub import DataHub
from fsae_dashboard.data.subscriptions import SubscriptionManager

# Roll to a new file after this many records or this many bytes, whichever first.
DEFAULT_MAX_RECORDS = 50_000
DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


def _slugify(topic: str) -> str:
    return topic.strip("/").replace("/", "_") or "topic"


class JsonRecorder:
    def __init__(
        self,
        hub: DataHub,
        subs: SubscriptionManager,
        topic: str,
        msg_type: str = "",
        out_dir: str | None = None,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self.hub = hub
        self.subs = subs
        self.topic = topic
        self.msg_type = msg_type
        self.max_records = max(1, int(max_records))
        self.max_bytes = max(1, int(max_bytes))

        slug = _slugify(topic)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = out_dir or os.getcwd()
        self.folder = os.path.join(base, f"{slug}_{stamp}")

        self._lock = threading.Lock()
        self._file = None
        self._file_index = 0
        self._records_in_file = 0
        self._bytes_in_file = 0
        self.total_records = 0
        self._closed = True

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        with self._lock:
            self._closed = False
            self._open_next_file()
        # ensure messages actually flow even if no panel subscribes this topic
        if self.msg_type:
            self.subs.subscribe(self.topic, self.msg_type)
        self.hub.add_listener(self._on_msg)

    def stop(self) -> None:
        self.hub.remove_listener(self._on_msg)
        if self.msg_type:
            self.subs.release(self.topic)
        with self._lock:
            self._closed = True
            self._close_file()

    @property
    def active(self) -> bool:
        return not self._closed

    @property
    def status(self) -> str:
        return f"JSON {self.topic} → {self.total_records} msgs"

    # --- write side (transport thread) ------------------------------------
    def _on_msg(self, topic: str, msg: dict, t: float) -> None:
        if topic != self.topic:
            return
        try:
            line = json.dumps({"t": t, "topic": topic, "msg": msg}, default=str) + "\n"
        except (TypeError, ValueError):
            return  # unserialisable message — skip it, keep recording
        data = line.encode("utf-8")
        with self._lock:
            if self._closed or self._file is None:
                return
            if self._records_in_file >= self.max_records or (
                self._bytes_in_file + len(data) > self.max_bytes
                and self._records_in_file > 0
            ):
                self._open_next_file()
            self._file.write(line)
            self._records_in_file += 1
            self._bytes_in_file += len(data)
            self.total_records += 1

    # --- file rotation (holds self._lock) ---------------------------------
    def _open_next_file(self) -> None:
        self._close_file()
        self._file_index += 1
        path = os.path.join(self.folder, f"part_{self._file_index:04d}.jsonl")
        # line-buffered text mode: every completed line is flushed to the OS,
        # which is what makes an interrupted recording lose at most one line.
        self._file = open(path, "w", buffering=1, encoding="utf-8")
        self._records_in_file = 0
        self._bytes_in_file = 0

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:  # noqa: BLE001
                pass
            self._file = None
