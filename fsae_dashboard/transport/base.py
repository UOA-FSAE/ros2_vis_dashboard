"""Transport interface shared by every data source."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

# A message callback receives the decoded message as a nested dict.
MessageCallback = Callable[[dict], None]

# Status callback: (connected: bool, human-readable detail: str)
StatusCallback = Callable[[bool, str], None]


@dataclass(frozen=True)
class TopicInfo:
    name: str
    type: str


class SubscriptionHandle:
    """Opaque per-subscription token returned by subscribe()."""

    def __init__(self, topic: str, token: Any):
        self.topic = topic
        self.token = token


class Transport(ABC):
    """Delivers ROS 2 messages as dicts. Callbacks fire on a background thread."""

    #: short identifier, e.g. "rosbridge"
    kind: str = "base"

    def __init__(self) -> None:
        self._status_cb: StatusCallback | None = None

    def set_status_callback(self, cb: StatusCallback | None) -> None:
        self._status_cb = cb

    def _emit_status(self, connected: bool, detail: str = "") -> None:
        if self._status_cb is not None:
            self._status_cb(connected, detail)

    # --- lifecycle ---------------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        """Begin connecting. Non-blocking; report result via status callback."""

    @abstractmethod
    def stop(self) -> None:
        """Disconnect and release all resources."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        ...

    # --- discovery ---------------------------------------------------------
    @abstractmethod
    def list_topics(self) -> list[TopicInfo] | None:
        """Return currently advertised topics, or None on failure. May block briefly."""

    # --- pub/sub -----------------------------------------------------------
    @abstractmethod
    def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: MessageCallback,
        throttle_rate: int = 0,
        compression: str | None = None,
    ) -> SubscriptionHandle:
        """Subscribe to *topic*. throttle_rate is min ms between messages."""

    @abstractmethod
    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        ...
