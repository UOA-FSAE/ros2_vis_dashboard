"""Top-down 2D track view — cones, boundaries, planned path, car pose.

This is the practical replacement for an embedded RViz: everything a driverless
debug session actually looks at, rendered by pyqtgraph at 60 fps. Each layer is
bound to a topic (defaults match the /fsae stack) and drawn only when data is
present, so it degrades gracefully on a half-connected stack.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fsae_dashboard.ui.panels.base import Panel, register_panel

_DEFAULTS = {
    "car_topic": "/fsae/slam/car_position",
    "trajectory_topic": "/fsae/planning/selected_trajectory",
    "left_topic": "/fsae/slam/left_track",
    "right_topic": "/fsae/slam/right_track",
    "detections_topic": "/fsae/perception/cone_detection",
}
_TYPES = {
    "car_topic": "geometry_msgs/Pose",
    "trajectory_topic": "geometry_msgs/PoseArray",
    "left_topic": "fsae_interfaces/Track",
    "right_topic": "fsae_interfaces/Track",
    "detections_topic": "fsae_interfaces/ConeDetection",
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


def _car_yaw(q: dict) -> float:
    """Heading in radians, tolerant of this stack's non-standard convention.

    Several UOA-FSAE nodes stuff the yaw angle straight into ``orientation.w``
    rather than a real quaternion (see stanley_controller.py: ``car_yaw =
    car_pose.orientation.w``). A genuine unit quaternion has x²+y²+z²+w²==1; if
    it doesn't, we treat ``w`` as the yaw angle directly.
    """
    x, y = q.get("x", 0.0), q.get("y", 0.0)
    z, w = q.get("z", 0.0), q.get("w", 1.0)
    norm = x * x + y * y + z * z + w * w
    if abs(norm - 1.0) > 1e-3:
        return w  # yaw stored directly in w
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _fmt_lap(seconds: float | None) -> str:
    """Format a lap duration as ``M:SS.mmm`` (or ``SS.mmm`` under a minute)."""
    if seconds is None:
        return "—"
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}:{rem:06.3f}" if minutes else f"{rem:.3f}"


class _LapTimer:
    """Detects laps from the car's xy track and records per-lap durations.

    The car's first observed position becomes a virtual start/finish line. A lap
    is booked once the car has driven clear of that line (past ``leave_radius``)
    and later returns within ``arm_radius`` of it. The two radii give hysteresis
    so sitting on the line can't double-count. Purely position-driven, so it
    needs no extra topic and works the same live or on replay.
    """

    def __init__(self, arm_radius: float = 3.0, leave_radius: float = 6.0):
        self.arm_radius = arm_radius
        self.leave_radius = leave_radius
        self.reset()

    def reset(self) -> None:
        self._start: tuple[float, float] | None = None
        self._lap_start_t: float | None = None
        self._left = False
        self.laps: list[float] = []

    def update(self, x: float, y: float, t: float) -> bool:
        """Feed a car position (+ timestamp); return True if a lap just closed."""
        if self._start is None:
            self._start = (x, y)
            self._lap_start_t = t
            return False
        d = math.hypot(x - self._start[0], y - self._start[1])
        if not self._left:
            if d > self.leave_radius:
                self._left = True
            return False
        if d < self.arm_radius:
            self.laps.append(t - self._lap_start_t)
            self._lap_start_t = t
            self._left = False
            return True
        return False

    def current(self, now: float) -> float:
        return (now - self._lap_start_t) if self._lap_start_t is not None else 0.0

    @property
    def best(self) -> float | None:
        return min(self.laps) if self.laps else None

    @property
    def last(self) -> float | None:
        return self.laps[-1] if self.laps else None


class _LapWidget(QFrame):
    """Compact, draggable, collapsible lap-time readout overlaid on the plot."""

    def __init__(self, parent: QWidget, on_export, on_reset):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame{background:rgba(20,20,20,220);border:1px solid #333;border-radius:6px;}"
            " QLabel{color:#ddd;} QPushButton{color:#ddd;}"
        )
        self.setMaximumWidth(210)
        self._collapsed = False
        self._drag_from: QPoint | None = None
        self._drag_orig: QPoint | None = None
        self._moved = False  # True once the user has dragged it somewhere

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(2)

        header = QHBoxLayout()
        self._toggle = QPushButton("▾ Lap times")
        self._toggle.setFlat(True)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setStyleSheet(
            "QPushButton{color:#fff;font-weight:600;border:none;text-align:left;}"
        )
        self._toggle.clicked.connect(self.toggle_collapsed)
        header.addWidget(self._toggle)
        header.addStretch(1)
        self._summary = QLabel("—")  # shown only while collapsed
        self._summary.setStyleSheet("color:#57D9A3;font-weight:600;")
        self._summary.setVisible(False)
        header.addWidget(self._summary)
        lay.addLayout(header)

        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 2, 0, 0)
        body.setSpacing(1)
        self._count = QLabel("Lap #1")
        self._cur = QLabel("Cur   —")
        self._last = QLabel("Last  —")
        self._best = QLabel("Best  —")
        self._cur.setStyleSheet("color:#57D9A3;font-size:15px;font-weight:600;")
        self._best.setStyleSheet("color:#FFB000;")
        for w in (self._count, self._cur, self._last, self._best):
            body.addWidget(w)
        btns = QHBoxLayout()
        exp = QPushButton("Export…")
        exp.clicked.connect(on_export)
        rst = QPushButton("Reset")
        rst.clicked.connect(on_reset)
        btns.addWidget(exp)
        btns.addWidget(rst)
        body.addLayout(btns)
        lay.addWidget(self._body)

    # --- collapse ----------------------------------------------------------
    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._summary.setVisible(collapsed)
        self._toggle.setText("▸ Lap times" if collapsed else "▾ Lap times")
        self.adjustSize()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    # --- drag within the parent viewport -----------------------------------
    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint()
            self._drag_orig = self.pos()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_from is not None:
            delta = event.globalPosition().toPoint() - self._drag_from
            self.move(self._drag_orig + delta)
            self.clamp_into_parent()
            self._moved = True

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_from = None

    def clamp_into_parent(self) -> None:
        """Keep the widget fully inside the plot even after a drag or resize."""
        parent = self.parentWidget()
        if parent is None:
            return
        max_x = max(0, parent.width() - self.width())
        max_y = max(0, parent.height() - self.height())
        self.move(min(max(0, self.x()), max_x), min(max(0, self.y()), max_y))

    # --- data --------------------------------------------------------------
    def update_stats(self, timer: _LapTimer, now: float) -> None:
        cur = _fmt_lap(timer.current(now)) if timer.laps or timer._lap_start_t else "—"
        self._count.setText(f"Lap #{len(timer.laps) + 1}")
        self._cur.setText(f"Cur   {cur}")
        self._last.setText(f"Last  {_fmt_lap(timer.last)}")
        self._best.setText(f"Best  {_fmt_lap(timer.best)}")
        self._summary.setText(cur)


@register_panel
class TrackViewPanel(Panel):
    panel_type = "trackview"
    display_name = "Track View (2D)"

    def build_ui(self) -> None:
        self._topics = dict(_DEFAULTS)
        self._trail: list[tuple[float, float]] = []
        self._yaw_offset = 0.0  # radians, added to car heading for detections
        self._laps = _LapTimer()

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        toolbar = QHBoxLayout()
        self.follow_cb = QCheckBox("Follow car")
        toolbar.addWidget(self.follow_cb)
        self.walls_cb = QCheckBox("Cone walls")
        self.walls_cb.setChecked(True)
        self.walls_cb.setToolTip(
            "Show the left/right track boundaries from /fsae/slam/*_track.\n"
            "Uncheck to hide them (e.g. when the sim isn't publishing walls)."
        )
        self.walls_cb.toggled.connect(self._on_walls_toggled)
        toolbar.addWidget(self.walls_cb)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("Heading offset °"))
        self.yaw_spin = QDoubleSpinBox()
        self.yaw_spin.setRange(-180.0, 180.0)
        self.yaw_spin.setSingleStep(5.0)
        self.yaw_spin.valueChanged.connect(
            lambda deg: setattr(self, "_yaw_offset", math.radians(deg))
        )
        toolbar.addWidget(self.yaw_spin)
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

        # Lap-time readout floats over the plot: draggable, collapsible.
        self.lap_widget = _LapWidget(self.plot, self._export_laps, self._reset_laps)
        self._lap_pos: tuple[int, int] | None = None  # remembered top-left, if moved
        self.lap_widget.show()

    def get_config(self) -> dict:
        return {
            "topics": self._topics,
            "follow": self.follow_cb.isChecked(),
            "yaw_offset_deg": self.yaw_spin.value(),
            "show_walls": self.walls_cb.isChecked(),
            "laps_collapsed": self.lap_widget.collapsed,
            "lap_pos": list(self._lap_pos) if self._lap_pos else None,
        }

    def apply_config(self, config: dict) -> None:
        self._topics.update(config.get("topics", {}))
        self.follow_cb.setChecked(bool(config.get("follow", False)))
        self.yaw_spin.setValue(float(config.get("yaw_offset_deg", 0.0)))
        self.walls_cb.setChecked(bool(config.get("show_walls", True)))
        self.lap_widget.set_collapsed(bool(config.get("laps_collapsed", False)))
        pos = config.get("lap_pos")
        if pos and len(pos) == 2:
            self._lap_pos = (int(pos[0]), int(pos[1]))
        for key, topic in self._topics.items():
            if topic:
                self.subscribe(topic, _TYPES.get(key, ""))

    def clear(self) -> None:
        self._trail = []
        self._laps.reset()
        self.lap_widget.update_stats(self._laps, time.time())
        for curve in (self.left_curve, self.right_curve, self.traj_curve, self.trail_curve):
            curve.setData([], [])
        for scatter in (self.blue_scatter, self.yellow_scatter, self.orange_scatter, self.car_scatter):
            scatter.clear()

    # --- toolbar / lap actions ---------------------------------------------
    def _on_walls_toggled(self, on: bool) -> None:
        self.left_curve.setVisible(on)
        self.right_curve.setVisible(on)
        if not on:  # drop the stale boundary so nothing lingers behind the hide
            self.left_curve.setData([], [])
            self.right_curve.setData([], [])

    def _reset_laps(self) -> None:
        self._laps.reset()
        self.lap_widget.update_stats(self._laps, time.time())

    def _export_laps(self) -> None:
        if not self._laps.laps:
            QMessageBox.information(self, "Export lap times", "No completed laps to export yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export lap times", "lap_times.txt", "Text files (*.txt)"
        )
        if not path:
            return
        laps = self._laps.laps
        best = self._laps.best
        lines = ["FSAE Track View — lap times", f"Total laps: {len(laps)}", ""]
        for i, t in enumerate(laps, 1):
            mark = "  *best" if t == best else ""
            lines.append(f"Lap {i:>3}: {_fmt_lap(t)}{mark}")
        lines += ["", f"Best: {_fmt_lap(best)}", f"Mean: {_fmt_lap(sum(laps) / len(laps))}"]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.lap_widget._summary.setText("saved")  # brief acknowledgement

    def _position_lap_widget(self) -> None:
        """Place the overlay: remembered spot if the user moved it, else top-right."""
        self.lap_widget.adjustSize()
        if self._lap_pos is not None:
            self.lap_widget.move(*self._lap_pos)
        else:
            self.lap_widget.move(max(0, self.plot.width() - self.lap_widget.width() - 12), 10)
        self.lap_widget.clamp_into_parent()
        self.lap_widget.raise_()

    def on_tick(self) -> None:
        hub = self.ctx.hub

        if self.walls_cb.isChecked():
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
            self._laps.update(cx, cy, time.time())

        det = hub.latest(self._topics["detections_topic"])
        if det:
            yaw = _car_yaw(car["orientation"]) if (car and "orientation" in car) else 0.0
            yaw += self._yaw_offset
            ox, oy = car_xy if car_xy else (0.0, 0.0)
            self._draw_detections(det, ox, oy, yaw)

        if self.follow_cb.isChecked() and car_xy:
            self.plot.setXRange(car_xy[0] - 15, car_xy[0] + 15, padding=0)
            self.plot.setYRange(car_xy[1] - 15, car_xy[1] + 15, padding=0)

        # keep the lap overlay updated and pinned inside the (resizable) plot
        self.lap_widget.update_stats(self._laps, time.time())
        if self.lap_widget._drag_from is None:
            if self.lap_widget._moved:
                self._lap_pos = (self.lap_widget.x(), self.lap_widget.y())
            self._position_lap_widget()

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
