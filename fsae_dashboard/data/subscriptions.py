"""Ref-counted subscription manager tying transport <-> data hub <-> UI.

Multiple panels can watch the same topic; the manager keeps a single transport
subscription per topic and fans the messages into the DataHub. It is a QObject
so it can emit thread-safe signals (transport callbacks arrive off-thread; Qt
queues the signal onto the UI thread).
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from fsae_dashboard.data.hub import DataHub
from fsae_dashboard.transport.base import SubscriptionHandle, TopicInfo, Transport


class SubscriptionManager(QObject):
    # (connected, detail)
    status_changed = Signal(bool, str)
    # emitted after list_topics() refresh
    topics_changed = Signal(list)

    def __init__(self, hub: DataHub):
        super().__init__()
        self.hub = hub
        self._transport: Transport | None = None
        self._lock = threading.RLock()
        self._refcount: dict[str, int] = {}
        self._handles: dict[str, SubscriptionHandle] = {}
        self._types: dict[str, str] = {}
        self._throttle: dict[str, int] = {}
        self._cached_topics: list[TopicInfo] = []
        self._fetching = False

    # --- transport lifecycle ----------------------------------------------
    def set_transport(self, transport: Transport | None) -> None:
        """Swap the active transport, re-subscribing any live topics."""
        with self._lock:
            live = dict(self._refcount)
            if self._transport is not None:
                for topic in list(self._handles):
                    self._teardown(topic)
                self._transport.set_status_callback(None)
                self._transport.stop()
            self._transport = transport
            self._handles.clear()
            self._cached_topics = []
            self._fetching = False
            if transport is not None:
                transport.set_status_callback(self._on_status)
                transport.start()
                # re-establish subscriptions that panels still want
                for topic, count in live.items():
                    if count > 0:
                        self._establish(topic)

    @property
    def transport(self) -> Transport | None:
        return self._transport

    @property
    def connected(self) -> bool:
        return bool(self._transport and self._transport.connected)

    def _on_status(self, connected: bool, detail: str) -> None:
        self.status_changed.emit(connected, detail)

    # --- topic discovery ---------------------------------------------------
    def list_topics(self) -> list[TopicInfo]:
        """Return cached topics immediately and kick off a background refresh.

        The fresh result arrives via the topics_changed signal so callers must
        connect to that signal to receive updates without blocking the UI thread.
        """
        if self._transport is None:
            return []
        with self._lock:
            if self._fetching:
                return list(self._cached_topics)
            self._fetching = True
            transport = self._transport
            cached = list(self._cached_topics)

        def _fetch():
            try:
                topics = transport.list_topics()
                if topics is None:
                    return  # service call failed; keep stale cache, don't clear panels
                with self._lock:
                    if self._transport is not transport:
                        return  # transport changed; discard stale result
                    self._cached_topics = topics
                    retype: list[str] = []
                    for ti in topics:
                        if ti.type:
                            old_type = self._types.get(ti.name, "")
                            self._types[ti.name] = ti.type
                            if (ti.name in self._handles and old_type
                                    and old_type != ti.type):
                                retype.append(ti.name)
                    # Static panel defaults let subscriptions start before the
                    # asynchronous topic list arrives. If discovery reveals a
                    # different live type (notably Pose vs PoseStamped), replace
                    # that handle so data starts flowing with the real contract.
                    for topic in retype:
                        self._teardown(topic)
                        self._establish(topic)
                self.topics_changed.emit(topics)
            finally:
                with self._lock:
                    self._fetching = False

        threading.Thread(target=_fetch, name="list_topics", daemon=True).start()
        return cached

    def type_of(self, topic: str) -> str:
        return self._types.get(topic, "")

    # --- ref-counted subscribe/release ------------------------------------
    def subscribe(self, topic: str, msg_type: str = "", throttle_rate: int = 0) -> None:
        with self._lock:
            if msg_type:
                self._types[topic] = msg_type
            if throttle_rate:
                self._throttle[topic] = throttle_rate
            self._refcount[topic] = self._refcount.get(topic, 0) + 1
            if self._refcount[topic] == 1:
                self._establish(topic)

    def release(self, topic: str) -> None:
        with self._lock:
            if topic not in self._refcount:
                return
            self._refcount[topic] -= 1
            if self._refcount[topic] <= 0:
                self._refcount.pop(topic, None)
                self._teardown(topic)

    # Minimum ms between image frames sent over the WebSocket. Image topics
    # without this cap easily saturate the link and delay all other topics.
    _IMAGE_THROTTLE_MS = 500  # 2 fps max

    def _establish(self, topic: str) -> None:
        if self._transport is None or topic in self._handles:
            return
        msg_type = self._types.get(topic, "")
        if not msg_type:
            # can't subscribe without a type; will retry once discovery fills it
            return
        throttle = self._throttle.get(topic, 0)
        if throttle == 0 and "Image" in msg_type:
            throttle = self._IMAGE_THROTTLE_MS
        try:
            handle = self._transport.subscribe(
                topic,
                msg_type,
                lambda msg, t=topic: self.hub.ingest(t, msg),
                throttle_rate=throttle,
            )
            self._handles[topic] = handle
        except Exception:  # noqa: BLE001
            pass

    def _teardown(self, topic: str) -> None:
        handle = self._handles.pop(topic, None)
        if handle is not None and self._transport is not None:
            try:
                self._transport.unsubscribe(handle)
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        self.set_transport(None)
