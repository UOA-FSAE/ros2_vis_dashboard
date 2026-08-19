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

# topic -> ROS type, mirrors the real stack (see fsae_autonomous docs/ARCHITECTURE.md
# — the authoritative connection table). Everything lives under the /fsae namespace,
# split into perception/ slam/ planning/ control/ hardware/ mission/.
_TOPICS: dict[str, str] = {
    "/fsae/control/cmd_vel": "ackermann_msgs/AckermannDriveStamped",
    "/fsae/control/drive": "ackermann_msgs/AckermannDrive",
    "/fsae/control/drive_vis": "ackermann_msgs/AckermannDrive",
    "/fsae/slam/car_position": "geometry_msgs/PoseStamped",
    "/fsae/slam/car_odom": "nav_msgs/Odometry",
    "/fsae/slam/car_velocity": "geometry_msgs/Vector3",
    "/fsae/slam/left_track": "fsae_interfaces/Track",
    "/fsae/slam/right_track": "fsae_interfaces/Track",
    "/fsae/planning/selected_trajectory": "geometry_msgs/PoseArray",
    "/fsae/planning/target_speed_profile": "std_msgs/Float64MultiArray",
    "/fsae/perception/cone_detection": "fsae_interfaces/ConeDetection",
    "/fsae/perception/image": "sensor_msgs/Image",
    "/fsae/hardware/can_tx": "fsae_interfaces/CANStamped",
    "/fsae/hardware/can_rx": "fsae_interfaces/CANStamped",
    "/fsae/hardware/battery_state": "sensor_msgs/BatteryState",
    "/fsae/hardware/glv_state": "sensor_msgs/BatteryState",
    "/fsae/hardware/drive_status": "ackermann_msgs/AckermannDriveStamped",
    "/fsae/hardware/curr_vel": "ackermann_msgs/AckermannDriveStamped",
    "/fsae/hardware/hardware_state": "fsae_interfaces/HardwareStatesStamped",
    "/fsae/mission/mission_status": "fsae_interfaces/MissionStatesStamped",
    "/fsae/mission/as_status": "std_msgs/UInt8",
}

_RATE_HZ: dict[str, float] = {
    "/fsae/control/cmd_vel": 30.0,
    "/fsae/control/drive": 30.0,
    "/fsae/control/drive_vis": 30.0,
    "/fsae/slam/car_position": 30.0,
    "/fsae/slam/car_odom": 30.0,
    "/fsae/slam/car_velocity": 30.0,
    "/fsae/slam/left_track": 5.0,
    "/fsae/slam/right_track": 5.0,
    "/fsae/planning/selected_trajectory": 10.0,
    "/fsae/planning/target_speed_profile": 10.0,
    "/fsae/perception/cone_detection": 15.0,
    "/fsae/perception/image": 5.0,
    "/fsae/hardware/can_tx": 100.0,
    "/fsae/hardware/can_rx": 20.0,
    "/fsae/hardware/battery_state": 2.0,
    "/fsae/hardware/glv_state": 2.0,
    "/fsae/hardware/drive_status": 20.0,
    "/fsae/hardware/curr_vel": 20.0,
    "/fsae/hardware/hardware_state": 5.0,
    "/fsae/mission/mission_status": 1.0,
    "/fsae/mission/as_status": 1.0,
}


# Point count for the mock's planned-path preview -- selected_trajectory and
# target_speed_profile must stay index-aligned, same as the real stack's.
_TRAJ_N = 20


def _stamp(t: float) -> dict:
    return {"sec": int(t), "nanosec": int((t % 1) * 1e9)}


def _point(x: float, y: float, z: float = 0.0) -> dict:
    return {"x": x, "y": y, "z": z}


def _pose(x: float, y: float, yaw: float) -> dict:
    return {
        "position": _point(x, y),
        "orientation": {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)},
    }


def _pose_yaw_in_w(x: float, y: float, yaw: float) -> dict:
    """Car pose the way the real stack publishes slam/car_position.

    The camera node repurposes ``orientation.w`` to carry the yaw angle (rad)
    directly rather than a real quaternion (see ARCHITECTURE.md — "yaw (rad)
    repurposed into orientation.w"). TrackView's ``_car_yaw`` detects the
    non-unit norm and reads ``w`` as the heading, so we reproduce that here.
    """
    return {
        "position": _point(x, y),
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": yaw},
    }


def _synthetic_image(t: float, w: int = 160, h: int = 120) -> dict:
    """A small synthetic rgb8 sensor_msgs/Image so the camera panel has a feed.

    The real ``perception/image`` is the annotated ZED frame; here we just draw a
    moving vertical bar over a gradient. Data is a flat list of byte ints, which
    is how rosbridge delivers uint8[] and what CameraPanel._as_bytes accepts.
    """
    bar = int((t * 40) % w)
    data = bytearray(w * h * 3)
    for x in range(w):
        r = (x * 255) // w
        # highlight a moving column
        g = 220 if abs(x - bar) < 3 else (x * 128) // w
        b = 255 - r
        col = (r & 0xFF, g & 0xFF, b & 0xFF)
        for y in range(h):
            i = (y * w + x) * 3
            data[i], data[i + 1], data[i + 2] = col
    return {
        "header": {"stamp": _stamp(t), "frame_id": "camera_link"},
        "height": h,
        "width": w,
        "encoding": "rgb8",
        "is_bigendian": 0,
        "step": w * 3,
        "data": list(data),
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

        # actual (fed-back) motion lags the command slightly
        act_speed = speed - 0.3
        act_steer = steer * 0.9

        if topic == "/fsae/control/cmd_vel":
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
        if topic in ("/fsae/control/drive", "/fsae/control/drive_vis"):
            return {"steering_angle": steer, "steering_angle_velocity": 0.0,
                    "speed": speed, "acceleration": 0.0, "jerk": 0.0}
        if topic == "/fsae/slam/car_position":
            # PoseStamped: yaw is repurposed into pose.orientation.w by the real
            # sim_perception node, and header.stamp carries the measurement time.
            return {
                "header": {"stamp": _stamp(now), "frame_id": "map"},
                "pose": _pose_yaw_in_w(cx, cy, yaw),
            }
        if topic == "/fsae/slam/car_odom":
            # Same snapshot as car_position, plus twist -- see sim_perception.py's
            # car_odom (real stack's actual/actual-speed source for the dashboard).
            return {
                "header": {"stamp": _stamp(now), "frame_id": "map"},
                "pose": {"pose": _pose(cx, cy, yaw)},
                "twist": {"twist": {
                    "linear": {"x": act_speed, "y": 0.0, "z": 0.0},
                    "angular": {"x": 0.0, "y": 0.0, "z": act_steer * act_speed / 2.5},
                }},
            }
        if topic == "/fsae/slam/car_velocity":
            return _point(speed * math.cos(yaw), speed * math.sin(yaw), 0.0)
        if topic == "/fsae/planning/selected_trajectory":
            poses = []
            for i in range(_TRAJ_N):
                a = theta + 0.02 * i
                poses.append(_pose(radius * math.cos(a), radius * math.sin(a), a + math.pi / 2))
            return {"header": {"stamp": _stamp(now), "frame_id": "map"}, "poses": poses}
        if topic == "/fsae/planning/target_speed_profile":
            # Illustrative only -- NOT a port of fsae_control.control_utils's
            # curvature_speed_profile() (that logic has one home, in fsae_planning;
            # duplicating it here would risk silently drifting out of sync with
            # it). This mock track is a constant-radius circle, which in reality
            # curvature_speed_profile() would score as one near-uniform target
            # speed; a gentle synthetic wave is used instead purely so the demo
            # shows the trackview speed gradient doing something.
            data = [9.0 + 4.0 * math.sin(theta + 0.15 * i) for i in range(_TRAJ_N)]
            return {"layout": {"dim": [], "data_offset": 0}, "data": data}
        if topic in ("/fsae/slam/left_track", "/fsae/slam/right_track"):
            offset = 2.0 if topic == "/fsae/slam/left_track" else -2.0
            cones = []
            for i in range(40):
                a = 0.157 * i
                r = radius + offset
                cones.append(_point(r * math.cos(a), r * math.sin(a)))
            return {"cones": cones}
        if topic == "/fsae/perception/cone_detection":
            # Cones ahead of the car in the car's local frame. ConeDetection now
            # carries a header (camera capture stamp) alongside the embedded pose.
            blue, yellow = [], []
            for i in range(6):
                d = 3.0 + 2.0 * i
                blue.append(_point(d, 2.0 + 0.1 * math.sin(t + i)))
                yellow.append(_point(d, -2.0 + 0.1 * math.cos(t + i)))
            return {
                "header": {"stamp": _stamp(now), "frame_id": "camera_link"},
                "car_pose": _pose(cx, cy, yaw),
                "yellow": yellow,
                "blue": blue,
                "small_orange": [],
                "big_orange": [_point(1.0, 0.0)],
            }
        if topic == "/fsae/perception/image":
            return _synthetic_image(now)
        if topic in ("/fsae/hardware/battery_state", "/fsae/hardware/glv_state"):
            base = 58.0 if topic == "/fsae/hardware/battery_state" else 13.2
            drop = 0.0005 * t
            return {
                "voltage": base - drop,
                "current": -12.0 + 2.0 * math.sin(t),
                "percentage": max(0.0, 1.0 - 0.001 * t),
                "temperature": 32.0 + 3.0 * math.sin(0.1 * t),
            }
        if topic in ("/fsae/hardware/drive_status", "/fsae/hardware/curr_vel"):
            # velocity/steering feedback from the car, as AckermannDriveStamped
            return {
                "header": {"stamp": _stamp(now), "frame_id": "base_link"},
                "drive": {
                    "steering_angle": act_steer,
                    "steering_angle_velocity": 0.0,
                    "speed": act_speed,
                    "acceleration": 0.0,
                    "jerk": 0.0,
                },
            }
        if topic == "/fsae/hardware/hardware_state":
            return {
                "header": {"stamp": _stamp(now), "frame_id": "base_link"},
                "hardware_states": {
                    "ebs_active": 0,
                    "ts_active": 1,
                    "in_gear": 1,
                    "master_switch_on": 1,
                    "asb_ready": 1,
                    "brakes_engaged": 0,
                },
            }
        if topic == "/fsae/mission/mission_status":
            return {
                "header": {"stamp": _stamp(now), "frame_id": "base_link"},
                "mission_states": {"mission_selected": 1, "mission_finished": 0},
            }
        if topic == "/fsae/mission/as_status":
            # AS state enum: 0 finished, 1 emergency, 2 ready, 3 driving, 4 off.
            # Sit in DRIVING for the demo.
            return {"data": 3}
        if topic == "/fsae/hardware/can_tx":
            # outbound Ackermann command frame (id 0x300)
            data = [(int(t * 50) + i) & 0xFF for i in range(8)]
            return {
                "header": {"stamp": _stamp(now), "frame_id": "can"},
                "can": {"id": 0x300, "is_rtr": False, "data": data},
            }
        if topic == "/fsae/hardware/can_rx":
            # inbound frames off the bus — cycle a few ids the decoder cares about
            can_id = (0x300, 0x301, 0x602)[int(t * 5) % 3]
            data = [(int(t * 30) + i) & 0xFF for i in range(8)]
            return {
                "header": {"stamp": _stamp(now), "frame_id": "can"},
                "can": {"id": can_id, "is_rtr": False, "data": data},
            }
        return {}
