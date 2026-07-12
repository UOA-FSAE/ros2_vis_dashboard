"""Data-plane transports.

A Transport is the single seam between the dashboard and the source of ROS 2
messages. Every implementation delivers messages as plain nested dicts, so no
ROS install or message compilation is needed on the client. Swapping the data
source (rosbridge / foxglove / native rclpy / a replay/mock) is a one-line
change here.
"""
from fsae_dashboard.transport.base import Transport, TopicInfo, SubscriptionHandle
from fsae_dashboard.transport.mock import MockTransport

__all__ = ["Transport", "TopicInfo", "SubscriptionHandle", "MockTransport", "build_transport"]


def build_transport(settings: dict) -> Transport:
    """Construct a transport from a connection-settings dict.

    settings["transport"] is one of: "mock", "rosbridge".
    For rosbridge, host/port are the (already tunnelled) endpoint to connect to.
    """
    kind = settings.get("transport", "mock")
    if kind == "mock":
        return MockTransport()
    if kind == "rosbridge":
        # Imported lazily so the app still runs when roslibpy isn't installed.
        from fsae_dashboard.transport.rosbridge import RosbridgeTransport

        return RosbridgeTransport(
            host=settings.get("host", "localhost"),
            port=int(settings.get("port", 9090)),
        )
    raise ValueError(f"Unknown transport kind: {kind!r}")
