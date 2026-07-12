"""rosbridge_server transport via roslibpy (WebSocket).

Works against `rosbridge_suite` or `foxglove_bridge` (rosbridge-compatible).
No ROS install required on this machine: messages arrive as dicts and topic
discovery uses the rosapi service. CBOR compression avoids the JSON-encoding
performance trap for images and large arrays.
"""
from __future__ import annotations

import threading

from fsae_dashboard.transport.base import (
    MessageCallback,
    SubscriptionHandle,
    TopicInfo,
    Transport,
)

try:
    import roslibpy
except ImportError:  # pragma: no cover - optional dependency
    roslibpy = None


def _normalise_type(msg_type: str) -> str:
    """Convert a 2-part ROS 1 style type to the 3-part ROS 2 form rosbridge wants.

    ``ackermann_msgs/AckermannDriveStamped`` -> ``ackermann_msgs/msg/AckermannDriveStamped``.
    Already-3-part types (``.../msg/...``, ``.../srv/...``) pass through unchanged.
    """
    if not msg_type or "/" not in msg_type:
        return msg_type
    parts = msg_type.split("/")
    if len(parts) == 2:
        return f"{parts[0]}/msg/{parts[1]}"
    return msg_type


class RosbridgeTransport(Transport):
    kind = "rosbridge"

    def __init__(self, host: str = "localhost", port: int = 9090):
        super().__init__()
        if roslibpy is None:
            raise RuntimeError(
                "roslibpy is not installed. Run: pip install roslibpy"
            )
        self.host = host
        self.port = port
        self._client: "roslibpy.Ros | None" = None
        self._topics: dict[str, "roslibpy.Topic"] = {}
        self._lock = threading.Lock()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._client = roslibpy.Ros(host=self.host, port=self.port)
        self._client.on_ready(lambda: self._emit_status(True, f"{self.host}:{self.port}"))

        def _run():
            try:
                self._client.run(timeout=10)
            except Exception as exc:  # noqa: BLE001
                self._emit_status(False, f"connect failed: {exc}")

        threading.Thread(target=_run, name="rosbridge", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            for topic in list(self._topics.values()):
                try:
                    topic.unsubscribe()
                except Exception:  # noqa: BLE001
                    pass
            self._topics.clear()
        if self._client is not None:
            try:
                # close() disconnects the websocket but leaves the Twisted
                # reactor running. terminate() would stop the reactor, and
                # Twisted's global reactor can never be restarted in the same
                # process -> ReactorNotRestartable on the next connect.
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        self._client = None
        self._emit_status(False, "disconnected")

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    # --- discovery ---------------------------------------------------------
    def list_topics(self) -> list[TopicInfo] | None:
        if not self.connected:
            return None
        service = roslibpy.Service(self._client, "/rosapi/topics", "rosapi/Topics")
        try:
            result = service.call(roslibpy.ServiceRequest(), timeout=5)
        except Exception:  # noqa: BLE001
            return None  # timeout or connection error — caller keeps stale cache
        names = result.get("topics", [])
        types = result.get("types", [])
        pairs = zip(names, types) if len(types) == len(names) else ((n, "") for n in names)
        return [TopicInfo(name=n, type=t) for n, t in pairs]

    # --- pub/sub -----------------------------------------------------------
    def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: MessageCallback,
        throttle_rate: int = 0,
        compression: str | None = None,
    ) -> SubscriptionHandle:
        if self._client is None:
            raise RuntimeError("Transport not started")
        # rosbridge (ROS 2) needs the 3-part "pkg/msg/Type" form. Accept the
        # 2-part "pkg/Type" form from older configs and normalise it, else the
        # server fails to register the subscription and delivers nothing.
        msg_type = _normalise_type(msg_type)
        # Plain JSON by default: it decodes to dicts reliably. cbor-raw must NOT
        # be used here — it delivers opaque serialised bytes we can't unpack
        # into fields without the .msg definitions.
        comp = compression or "none"
        ros_topic = roslibpy.Topic(
            self._client,
            topic,
            msg_type,
            throttle_rate=throttle_rate,
            queue_length=1,
            compression=comp,
        )
        ros_topic.subscribe(callback)
        with self._lock:
            self._topics[topic] = ros_topic
        return SubscriptionHandle(topic, ros_topic)

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        ros_topic = handle.token
        try:
            ros_topic.unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._topics.pop(handle.topic, None)
