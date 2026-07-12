"""In-process simulator that mimics the UOA-FSAE autonomous stack.

Publishes synthetic but plausible data on every topic the dashboard cares
about, so the whole UI can be developed and demoed on any laptop with zero ROS
infrastructure. The car drives a figure-of-eight through a cone-lined track.
"""
from __future__ import annotations

import math
import threading
import time

from fsae_dashboard.transport.base import (
    MessageCallback,
    SubscriptionHandle,
    TopicInfo,
    Transport,
)

# topic -> ROS type, mirrors the real stack (see IMPLEMENTATION_PLAN.md §1).
_TOPICS: dict[str, str] = {
    "/moa/cmd_vel": "ackermann_msgs/AckermannDriveStamped",
    "/moa/drive_vis": "ackermann_msgs/AckermannDrive",
    "/moa/car_position": "geometry_msgs/Pose",
    "/moa/zed/car_velocity": "geometry_msgs/Vector3",
    "/moa/selected_trajectory": "geometry_msgs/PoseArray",
    "/moa/left_track": "fsae_interfaces/Track",
    "/moa/right_track": "fsae_interfaces/Track",
    "/moa/zed/cone_detection": "fsae_interfaces/Detections",
    "/moa/battery_state": "sensor_msgs/BatteryState",
    "/moa/glv_state": "sensor_msgs/BatteryState",
    "/moa/as_status": "std_msgs/UInt8",
    "/moa/pub_raw_can": "fsae_interfaces/CANStamped",
    "/moa/imu/data": "sensor_msgs/Imu",
}

_RATE_HZ: dict[str, float] = {
    "/moa/cmd_vel": 30.0,
    "/moa/drive_vis": 30.0,
    "/moa/car_position": 30.0,
    "/moa/zed/car_velocity": 30.0,
    "/moa/selected_trajectory": 10.0,
    "/moa/left_track": 5.0,
    "/moa/right_track": 5.0,
    "/moa/zed/cone_detection": 15.0,
    "/moa/battery_state": 2.0,
    "/moa/glv_state": 2.0,
    "/moa/as_status": 1.0,
    "/moa/pub_raw_can": 100.0,
    "/moa/imu/data": 100.0,
}


def _stamp(t: float) -> dict:
    return {"sec": int(t), "nanosec": int((t % 1) * 1e9)}


def _point(x: float, y: float, z: float = 0.0) -> dict:
    return {"x": x, "y": y, "z": z}


def _pose(x: float, y: float, yaw: float) -> dict:
    return {
        "position": _point(x, y),
        "orientation": {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)},
    }


class MockTransport(Transport):
    kind = "mock"

    def __init__(self) -> None:
        super().__init__()
        self._subs: dict[int, tuple[str, MessageCallback]] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._t0 = time.time()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._running = True
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._loop, name="mock-transport", daemon=True)
        self._thread.start()
        self._emit_status(True, "mock simulator")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._emit_status(False, "stopped")

    @property
    def connected(self) -> bool:
        return self._running

    # --- discovery ---------------------------------------------------------
    def list_topics(self) -> list[TopicInfo]:
        return [TopicInfo(name=n, type=t) for n, t in sorted(_TOPICS.items())]

    # --- pub/sub -----------------------------------------------------------
    def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: MessageCallback,
        throttle_rate: int = 0,
        compression: str | None = None,
    ) -> SubscriptionHandle:
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            self._subs[sid] = (topic, callback)
        return SubscriptionHandle(topic, sid)

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        with self._lock:
            self._subs.pop(handle.token, None)

    # --- simulation --------------------------------------------------------
    def _loop(self) -> None:
        last_sent: dict[str, float] = {}
        while self._running:
            now = time.time()
            for topic, rate in _RATE_HZ.items():
                period = 1.0 / rate
                if now - last_sent.get(topic, 0.0) >= period:
                    last_sent[topic] = now
                    self._publish(topic, now)
            time.sleep(0.002)

    def _publish(self, topic: str, now: float) -> None:
        with self._lock:
            targets = [cb for (t, cb) in self._subs.values() if t == topic]
        if not targets:
            return
        msg = self._make_message(topic, now)
        for cb in targets:
            try:
                cb(msg)
            except Exception:  # noqa: BLE001 - a bad panel must not kill the sim
                pass

    def _make_message(self, topic: str, now: float) -> dict:
        t = now - self._t0
        # Car pose: driving a lap on a ~15 m radius loop.
        theta = 0.2 * t
        radius = 15.0
        cx = radius * math.cos(theta)
        cy = radius * math.sin(theta)
        yaw = theta + math.pi / 2
        speed = 8.0 + 1.5 * math.sin(0.5 * t)
        steer = 0.35 * math.sin(0.5 * t)

        if topic == "/moa/cmd_vel":
            return {
                "header": {"stamp": _stamp(now), "frame_id": "base_link"},
                "drive": {
                    "steering_angle": steer,
                    "steering_angle_velocity": 0.0,
                    "speed": speed,
                    "acceleration": 0.0,
                    "jerk": 0.0,
                },
            }
        if topic == "/moa/drive_vis":
            return {"steering_angle": steer, "speed": speed, "acceleration": 0.0, "jerk": 0.0}
        if topic == "/moa/car_position":
            return _pose(cx, cy, yaw)
        if topic == "/moa/zed/car_velocity":
            return _point(speed * math.cos(yaw), speed * math.sin(yaw), 0.0)
        if topic == "/moa/selected_trajectory":
            poses = []
            for i in range(20):
                a = theta + 0.02 * i
                poses.append(_pose(radius * math.cos(a), radius * math.sin(a), a + math.pi / 2))
            return {"header": {"stamp": _stamp(now), "frame_id": "map"}, "poses": poses}
        if topic in ("/moa/left_track", "/moa/right_track"):
            offset = 2.0 if topic == "/moa/left_track" else -2.0
            cones = []
            for i in range(40):
                a = 0.157 * i
                r = radius + offset
                cones.append(_point(r * math.cos(a), r * math.sin(a)))
            return {"cones": cones}
        if topic == "/moa/zed/cone_detection":
            # Cones ahead of the car in the car's local frame.
            blue, yellow = [], []
            for i in range(6):
                d = 3.0 + 2.0 * i
                blue.append(_point(d, 2.0 + 0.1 * math.sin(t + i)))
                yellow.append(_point(d, -2.0 + 0.1 * math.cos(t + i)))
            return {
                "car_pose": _pose(0.0, 0.0, 0.0),
                "yellow": yellow,
                "blue": blue,
                "small_orange": [],
                "big_orange": [_point(1.0, 0.0)],
            }
        if topic in ("/moa/battery_state", "/moa/glv_state"):
            base = 58.0 if topic == "/moa/battery_state" else 13.2
            drop = 0.0005 * t
            return {
                "voltage": base - drop,
                "current": -12.0 + 2.0 * math.sin(t),
                "percentage": max(0.0, 1.0 - 0.001 * t),
                "temperature": 32.0 + 3.0 * math.sin(0.1 * t),
            }
        if topic == "/moa/as_status":
            # cycle through AS states 0..5 slowly
            return {"data": int(t / 5) % 6}
        if topic == "/moa/pub_raw_can":
            can_id = 0x300 + (int(t * 10) % 4)
            data = [(int(t * 50) + i) & 0xFF for i in range(8)]
            return {
                "header": {"stamp": _stamp(now), "frame_id": "can"},
                "can": {"id": can_id, "is_rtr": False, "dlc": 8, "data": data},
            }
        if topic == "/moa/imu/data":
            return {
                "header": {"stamp": _stamp(now), "frame_id": "imu"},
                "linear_acceleration": _point(
                    2.0 * math.sin(2 * t), 3.0 * math.cos(1.5 * t), 9.81
                ),
                "angular_velocity": _point(0.0, 0.0, 0.4 * math.sin(0.5 * t)),
                "orientation": {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)},
            }
        return {}
