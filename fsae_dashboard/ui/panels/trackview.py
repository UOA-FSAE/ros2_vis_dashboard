"""Top-down 2D track view — cones, boundaries, planned path, car pose.

This is the practical replacement for an embedded RViz: everything a driverless
debug session actually looks at, rendered by pyqtgraph at 60 fps. Each layer is
bound to a topic (defaults match the /moa stack) and drawn only when data is
present, so it degrades gracefully on a half-connected stack.
"""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout

from fsae_dashboard.ui.panels.base import Panel, register_panel

_DEFAULTS = {
    "car_topic": "/moa/car_position",
    "trajectory_topic": "/moa/selected_trajectory",
    "left_topic": "/moa/left_track",
    "right_topic": "/moa/right_track",
    "detections_topic": "/moa/zed/cone_detection",
}
_TYPES = {
    "car_topic": "geometry_msgs/Pose",
    "trajectory_topic": "geometry_msgs/PoseArray",
    "left_topic": "fsae_interfaces/Track",
    "right_topic": "fsae_interfaces/Track",
    "detections_topic": "fsae_interfaces/Detections",
}


def _points_from(node) -> np.ndarray:
    """Extract Nx2 xy from a list of point dicts or pose dicts."""
    if not isinstance(node, list):
        return np.empty((0, 2))
    out = []
    for item in node:
        if not isinstance(item, dict):
            continue
        if "position" in item:  # Pose
            p = item["position"]
            out.append((p.get("x", 0.0), p.get("y", 0.0)))
        elif "x" in item:  # Point
            out.append((item.get("x", 0.0), item.get("y", 0.0)))
    return np.array(out) if out else np.empty((0, 2))


def _yaw_from_quat(q: dict) -> float:
    z, w = q.get("z", 0.0), q.get("w", 1.0)
    return 2.0 * math.atan2(z, w)


@register_panel
class TrackViewPanel(Panel):
    panel_type = "trackview"
    display_name = "Track View (2D)"

    def build_ui(self) -> None:
        self._topics = dict(_DEFAULTS)
        self._trail: list[tuple[float, float]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        toolbar = QHBoxLayout()
        self.follow_cb = QCheckBox("Follow car")
        toolbar.addWidget(self.follow_cb)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#141414")
        self.plot.setAspectLocked(True)
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        root.addWidget(self.plot)

        self.left_curve = self.plot.plot(pen=pg.mkPen("#4C9AFF", width=2))
        self.right_curve = self.plot.plot(pen=pg.mkPen("#FFB000", width=2))
        self.traj_curve = self.plot.plot(pen=pg.mkPen("#57D9A3", width=2, style=pg.QtCore.Qt.DashLine))
        self.trail_curve = self.plot.plot(pen=pg.mkPen("#888888", width=1))

        self.blue_scatter = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#4C9AFF"))
        self.yellow_scatter = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#FFB000"))
        self.orange_scatter = pg.ScatterPlotItem(size=12, brush=pg.mkBrush("#F76707"))
        self.car_scatter = pg.ScatterPlotItem(size=16, brush=pg.mkBrush("#FF3B3B"), symbol="t")
        for item in (self.blue_scatter, self.yellow_scatter, self.orange_scatter, self.car_scatter):
            self.plot.addItem(item)

    def get_config(self) -> dict:
        return {"topics": self._topics, "follow": self.follow_cb.isChecked()}

    def apply_config(self, config: dict) -> None:
        self._topics.update(config.get("topics", {}))
        self.follow_cb.setChecked(bool(config.get("follow", False)))
        for key, topic in self._topics.items():
            if topic:
                self.subscribe(topic, _TYPES.get(key, ""))

    def on_tick(self) -> None:
        hub = self.ctx.hub

        left = _points_from((hub.latest(self._topics["left_topic"]) or {}).get("cones"))
        if len(left):
            self.left_curve.setData(left[:, 0], left[:, 1])
        right = _points_from((hub.latest(self._topics["right_topic"]) or {}).get("cones"))
        if len(right):
            self.right_curve.setData(right[:, 0], right[:, 1])

        traj = _points_from((hub.latest(self._topics["trajectory_topic"]) or {}).get("poses"))
        if len(traj):
            self.traj_curve.setData(traj[:, 0], traj[:, 1])

        car = hub.latest(self._topics["car_topic"])
        car_xy = None
        if car and "position" in car:
            cx, cy = car["position"].get("x", 0.0), car["position"].get("y", 0.0)
            car_xy = (cx, cy)
            self.car_scatter.setData([cx], [cy])
            self._trail.append(car_xy)
            if len(self._trail) > 2000:
                self._trail = self._trail[-2000:]
            trail = np.array(self._trail)
            self.trail_curve.setData(trail[:, 0], trail[:, 1])

        det = hub.latest(self._topics["detections_topic"])
        if det:
            yaw = _yaw_from_quat(car["orientation"]) if (car and "orientation" in car) else 0.0
            ox, oy = car_xy if car_xy else (0.0, 0.0)
            self._draw_detections(det, ox, oy, yaw)

        if self.follow_cb.isChecked() and car_xy:
            self.plot.setXRange(car_xy[0] - 15, car_xy[0] + 15, padding=0)
            self.plot.setYRange(car_xy[1] - 15, car_xy[1] + 15, padding=0)

    def _draw_detections(self, det: dict, ox: float, oy: float, yaw: float) -> None:
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)

        def to_global(pts: np.ndarray) -> np.ndarray:
            if not len(pts):
                return pts
            gx = ox + pts[:, 0] * cos_y - pts[:, 1] * sin_y
            gy = oy + pts[:, 0] * sin_y + pts[:, 1] * cos_y
            return np.column_stack([gx, gy])

        blue = to_global(_points_from(det.get("blue")))
        yellow = to_global(_points_from(det.get("yellow")))
        orange = to_global(_points_from(det.get("big_orange")))
        self.blue_scatter.setData(blue[:, 0], blue[:, 1]) if len(blue) else self.blue_scatter.clear()
        self.yellow_scatter.setData(yellow[:, 0], yellow[:, 1]) if len(yellow) else self.yellow_scatter.clear()
        self.orange_scatter.setData(orange[:, 0], orange[:, 1]) if len(orange) else self.orange_scatter.clear()
