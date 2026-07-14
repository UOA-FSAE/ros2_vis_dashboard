"""Top-down 2D track view — cones, boundaries, planned path, car pose.

This is the practical replacement for an embedded RViz: everything a driverless
debug session actually looks at, rendered by pyqtgraph at 60 fps. Each layer is
bound to a topic (defaults match the /fsae stack) and drawn only when data is
present, so it degrades gracefully on a half-connected stack.
"""
from __future__ import annotations

import base64
import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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

# Selectable cone-detection sources for the Track View. Both publish
# fsae_interfaces/ConeDetection, so the rendering is identical; only the source
# differs. The picker is editable and also auto-fills any live ConeDetection
# topic discovered on the bridge, so these are just convenient presets.
#   (topic, human label)
_CONE_SOURCES = [
    ("/fsae/perception/cone_detection", "Raw ZED camera"),
    ("/cone_detection_fused", "LiDAR + camera fusion"),
]

# The lidar_fusion node does not (yet) publish fused cones as a ConeDetection
# topic — only debug PointCloud2 clouds. This optional overlay renders that
# cloud client-side so the fusion output is visible in the dashboard without
# touching the autonomous stack. Points are in the velodyne (car-body) frame;
# we place them with the same car-pose transform used for camera detections.
_FUSION_CLOUD_SOURCES = [
    ("/lidar_fusion/cone_points", "Kept cone points (above ground)"),
    ("/lidar_fusion/ground_points", "Discarded ground points"),
]
_FUSION_CLOUD_TYPE = "sensor_msgs/PointCloud2"
_FUSION_CLOUD_THROTTLE_MS = 200  # clouds are chunky; cap the wire rate
_FUSION_HOVER_MAX = 300  # cap points registered for hover, to keep it snappy

# PointCloud2 field datatype -> (numpy dtype char, byte size).
_PC2_DT = {1: ("i1", 1), 2: ("u1", 1), 3: ("i2", 2), 4: ("u2", 2),
           5: ("i4", 4), 6: ("u4", 4), 7: ("f4", 4), 8: ("f8", 8)}


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


def _cloud_bytes(data) -> bytes:
    """PointCloud2 ``data`` arrives base64 (rosbridge JSON), bytes, or an int list."""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            return b""
    if isinstance(data, list):
        return bytes(b & 0xFF for b in data)
    return b""


def _cloud_xy(msg: dict) -> np.ndarray:
    """Extract an Nx2 array of x,y from a sensor_msgs/PointCloud2 message.

    Reads the field offsets/datatypes from the message itself so it works for
    any layout (velodyne is x,y,z float32 + intensity/ring), and clamps to the
    payload length so a truncated frame degrades to fewer points, not a crash.
    """
    data = _cloud_bytes(msg.get("data"))
    step = int(msg.get("point_step", 0))
    n = int(msg.get("width", 0)) * int(msg.get("height", 1) or 1)
    if not data or step <= 0 or n <= 0:
        return np.empty((0, 2))
    fields = {f.get("name"): f for f in msg.get("fields", []) if isinstance(f, dict)}
    if "x" not in fields or "y" not in fields:
        return np.empty((0, 2))
    buf = np.frombuffer(data, dtype=np.uint8)
    n = min(n, buf.size // step)
    if n <= 0:
        return np.empty((0, 2))
    buf = buf[: n * step].reshape(n, step)
    endian = ">" if msg.get("is_bigendian") else "<"

    def column(field: dict) -> np.ndarray | None:
        dt = _PC2_DT.get(int(field.get("datatype", 0)))
        if dt is None:
            return None
        char, size = dt
        off = int(field.get("offset", 0))
        if off + size > step:
            return None
        chunk = np.ascontiguousarray(buf[:, off:off + size])
        return chunk.view(endian + char).reshape(n).astype(np.float64)

    x = column(fields["x"])
    y = column(fields["y"])
    if x is None or y is None:
        return np.empty((0, 2))
    return np.column_stack([x, y])


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

    _HOVER_IDLE = "Hover a point for 0.5 s to identify it."
    _HOVER_PX = 14  # cursor-to-point pixel radius counted as a hover hit

    def build_ui(self) -> None:
        self._topics = dict(_DEFAULTS)
        self._trail: list[tuple[float, float]] = []
        self._yaw_offset = 0.0  # radians, added to car heading for detections
        self._laps = _LapTimer()

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        # All controls live in a single "Options" popup to keep the view clean;
        # the toolbar itself is just the button that opens it.
        toolbar = QHBoxLayout()
        self.options_btn = QPushButton("⚙ Options")
        self.options_btn.setToolTip(
            "Track View preferences: follow car, cone walls, laptime,\n"
            "heading offset, and which topic feeds the cone detections."
        )
        self.options_btn.clicked.connect(self._open_options)
        toolbar.addWidget(self.options_btn)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        # --- preference controls (owned here, laid out in the Options popup) ---
        self._options_dialog: QDialog | None = None
        self.follow_cb = QCheckBox("Follow car")
        self.walls_cb = QCheckBox("Cone walls")
        self.walls_cb.setChecked(True)
        self.walls_cb.setToolTip(
            "Show the left/right track boundaries from /fsae/slam/*_track.\n"
            "Uncheck to hide them (e.g. when the sim isn't publishing walls)."
        )
        self.walls_cb.toggled.connect(self._on_walls_toggled)
        self.laptime_cb = QCheckBox("Enable laptime")
        self.laptime_cb.setChecked(True)
        self.laptime_cb.setToolTip(
            "Detect laps from the car pose and show the Cur/Last/Best overlay.\n"
            "Uncheck to hide the overlay and stop lap detection."
        )
        self.laptime_cb.toggled.connect(self._on_laptime_toggled)
        self.yaw_spin = QDoubleSpinBox()
        self.yaw_spin.setRange(-180.0, 180.0)
        self.yaw_spin.setSingleStep(5.0)
        self.yaw_spin.valueChanged.connect(
            lambda deg: setattr(self, "_yaw_offset", math.radians(deg))
        )

        # Cone-data source picker: presets for the two known detectors, editable,
        # and auto-filled with any live ConeDetection topic on the bridge.
        self.cone_source_combo = QComboBox()
        self.cone_source_combo.setEditable(True)
        self.cone_source_combo.setToolTip(
            "ROS topic feeding the cone-detection layer (fsae_interfaces/ConeDetection).\n"
            "Raw ZED camera vs. LiDAR+camera fusion. Live topics auto-fill; you can\n"
            "also type a custom topic."
        )
        self.cone_source_combo.blockSignals(True)
        for topic, label in _CONE_SOURCES:
            self.cone_source_combo.addItem(topic)
            self.cone_source_combo.setItemData(
                self.cone_source_combo.count() - 1, label, Qt.ToolTipRole
            )
        self.cone_source_combo.setCurrentText(self._topics["detections_topic"])
        self.cone_source_combo.blockSignals(False)
        self.cone_source_combo.currentTextChanged.connect(self._on_cone_source_changed)
        # keep the picker in sync with topics discovered on the bridge
        self.ctx.subs.topics_changed.connect(self._populate_cone_topics)
        self.ctx.subs.list_topics()

        # Optional LiDAR-fusion cloud overlay. Kept separate from self._topics so
        # it is only subscribed while enabled (clouds are heavy).
        self._fusion_topic = _FUSION_CLOUD_SOURCES[0][0]
        self.fusion_cb = QCheckBox("LiDAR fusion cloud")
        self.fusion_cb.setToolTip(
            "Overlay the lidar_fusion debug cloud (sensor_msgs/PointCloud2) as green\n"
            "points, placed with the car pose. The fusion node has no fused-cone\n"
            "ConeDetection topic yet, so this is the only way to see its output."
        )
        self.fusion_cb.toggled.connect(self._on_fusion_toggled)
        self.fusion_topic_combo = QComboBox()
        self.fusion_topic_combo.setEditable(True)
        self.fusion_topic_combo.setToolTip("Which lidar_fusion PointCloud2 topic to render.")
        self.fusion_topic_combo.blockSignals(True)
        for topic, label in _FUSION_CLOUD_SOURCES:
            self.fusion_topic_combo.addItem(topic)
            self.fusion_topic_combo.setItemData(
                self.fusion_topic_combo.count() - 1, label, Qt.ToolTipRole
            )
        self.fusion_topic_combo.setCurrentText(self._fusion_topic)
        self.fusion_topic_combo.blockSignals(False)
        self.fusion_topic_combo.currentTextChanged.connect(self._on_fusion_topic_changed)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#141414")
        self.plot.setAspectLocked(True)
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        root.addWidget(self.plot)

        self.left_curve = self.plot.plot(pen=pg.mkPen("#4C9AFF", width=2))
        self.right_curve = self.plot.plot(pen=pg.mkPen("#FFB000", width=2))
        self.traj_curve = self.plot.plot(pen=pg.mkPen("#57D9A3", width=2, style=pg.QtCore.Qt.DashLine))
        self.trail_curve = self.plot.plot(pen=pg.mkPen("#888888", width=1))

        # fusion cloud drawn under the cones so cone markers stay legible on top
        self.fusion_scatter = pg.ScatterPlotItem(
            size=5, pen=None, brush=pg.mkBrush(46, 204, 113, 160)
        )
        self.blue_scatter = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#4C9AFF"))
        self.yellow_scatter = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#FFB000"))
        self.small_orange_scatter = pg.ScatterPlotItem(size=8, brush=pg.mkBrush("#FF922B"))
        self.orange_scatter = pg.ScatterPlotItem(size=12, brush=pg.mkBrush("#F76707"))
        self.car_scatter = pg.ScatterPlotItem(size=16, brush=pg.mkBrush("#FF3B3B"), symbol="t")
        for item in (self.fusion_scatter, self.blue_scatter, self.yellow_scatter,
                     self.small_orange_scatter, self.orange_scatter, self.car_scatter):
            self.plot.addItem(item)

        # Lap-time readout floats over the plot: draggable, collapsible.
        self.lap_widget = _LapWidget(self.plot, self._export_laps, self._reset_laps)
        self._lap_pos: tuple[int, int] | None = None  # remembered top-left, if moved
        self.lap_widget.show()

        # Bottom readout: identifies the point under the cursor after a dwell.
        self.hover_label = QLabel(self._HOVER_IDLE)
        self.hover_label.setStyleSheet("color:#aaa; padding:2px 4px;")
        root.addWidget(self.hover_label)

        # Hover-to-identify: every drawn point is registered in _hover_points
        # each tick; a 0.5 s dwell over one shows its identity in hover_label.
        self._hover_points: list[tuple[float, float, str]] = []
        self._hover_pending: str | None = None
        self._hover_shown = False
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)  # dwell before revealing
        self._hover_timer.timeout.connect(self._on_hover_timeout)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def get_config(self) -> dict:
        return {
            "topics": self._topics,
            "follow": self.follow_cb.isChecked(),
            "yaw_offset_deg": self.yaw_spin.value(),
            "show_walls": self.walls_cb.isChecked(),
            "laptime_enabled": self.laptime_cb.isChecked(),
            "laps_collapsed": self.lap_widget.collapsed,
            "lap_pos": list(self._lap_pos) if self._lap_pos else None,
            "fusion_cloud": self.fusion_cb.isChecked(),
            "fusion_cloud_topic": self._fusion_topic,
        }

    def apply_config(self, config: dict) -> None:
        self._topics.update(config.get("topics", {}))
        self.follow_cb.setChecked(bool(config.get("follow", False)))
        self.yaw_spin.setValue(float(config.get("yaw_offset_deg", 0.0)))
        self.walls_cb.setChecked(bool(config.get("show_walls", True)))
        self.laptime_cb.setChecked(bool(config.get("laptime_enabled", True)))
        self.lap_widget.set_collapsed(bool(config.get("laps_collapsed", False)))
        pos = config.get("lap_pos")
        if pos and len(pos) == 2:
            self._lap_pos = (int(pos[0]), int(pos[1]))
        # reflect the configured cone source without re-triggering a subscribe
        # (the loop below establishes every topic, including this one)
        self.cone_source_combo.blockSignals(True)
        self.cone_source_combo.setCurrentText(self._topics.get("detections_topic", ""))
        self.cone_source_combo.blockSignals(False)
        for key, topic in self._topics.items():
            if topic:
                self.subscribe(topic, _TYPES.get(key, ""))
        # fusion cloud: set the topic first, then flip the checkbox so the
        # toggle handler subscribes to the right topic (if enabled in config)
        self._fusion_topic = config.get("fusion_cloud_topic", self._fusion_topic)
        self.fusion_topic_combo.blockSignals(True)
        self.fusion_topic_combo.setCurrentText(self._fusion_topic)
        self.fusion_topic_combo.blockSignals(False)
        self.fusion_cb.setChecked(bool(config.get("fusion_cloud", False)))

    def clear(self) -> None:
        self._trail = []
        self._laps.reset()
        self.lap_widget.update_stats(self._laps, time.time())
        for curve in (self.left_curve, self.right_curve, self.traj_curve, self.trail_curve):
            curve.setData([], [])
        for scatter in (self.fusion_scatter, self.blue_scatter, self.yellow_scatter,
                        self.small_orange_scatter, self.orange_scatter, self.car_scatter):
            scatter.clear()
        self._hover_points = []
        self._hover_pending = None
        self._hover_shown = False
        self._hover_timer.stop()
        self.hover_label.setText(self._HOVER_IDLE)

    # --- options popup / cone source --------------------------------------
    def _open_options(self) -> None:
        """Show the single preferences popup (built lazily, reused thereafter)."""
        if self._options_dialog is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Track View options")
            form = QFormLayout(dlg)
            form.addRow(self.follow_cb)
            form.addRow(self.walls_cb)
            form.addRow(self.laptime_cb)
            form.addRow("Heading offset °", self.yaw_spin)
            form.addRow("Cone data source", self.cone_source_combo)
            form.addRow(self.fusion_cb)
            form.addRow("Fusion cloud topic", self.fusion_topic_combo)
            self._options_dialog = dlg
        self.ctx.subs.list_topics()  # refresh discovered cone topics on open
        self._options_dialog.show()
        self._options_dialog.raise_()
        self._options_dialog.activateWindow()

    def _populate_cone_topics(self, topics) -> None:
        """Append any live ConeDetection topics not already offered as presets."""
        combo = self.cone_source_combo
        known = {combo.itemText(i) for i in range(combo.count())}
        current = combo.currentText()
        combo.blockSignals(True)
        for ti in topics:
            if "ConeDetection" in ti.type and ti.name not in known:
                combo.addItem(ti.name)
                known.add(ti.name)
        combo.setCurrentText(current)
        combo.blockSignals(False)

    def _on_cone_source_changed(self, topic: str) -> None:
        """Swap the detections subscription to the chosen cone-data topic."""
        topic = topic.strip()
        old = self._topics.get("detections_topic", "")
        if topic == old:
            return
        if old:
            self.release(old)
        self._topics["detections_topic"] = topic
        if topic:
            msg_type = self.ctx.subs.type_of(topic) or _TYPES["detections_topic"]
            self.subscribe(topic, msg_type)

    def _on_fusion_toggled(self, on: bool) -> None:
        """Subscribe to the fusion cloud only while the overlay is enabled."""
        if on and self._fusion_topic:
            self.subscribe(self._fusion_topic, _FUSION_CLOUD_TYPE, _FUSION_CLOUD_THROTTLE_MS)
        elif not on:
            if self._fusion_topic:
                self.release(self._fusion_topic)
            self.fusion_scatter.clear()

    def _on_fusion_topic_changed(self, topic: str) -> None:
        topic = topic.strip()
        old = self._fusion_topic
        if topic == old:
            return
        if self.fusion_cb.isChecked() and old:
            self.release(old)
        self._fusion_topic = topic
        if self.fusion_cb.isChecked() and topic:
            self.subscribe(topic, _FUSION_CLOUD_TYPE, _FUSION_CLOUD_THROTTLE_MS)

    # --- toolbar / lap actions ---------------------------------------------
    def _on_walls_toggled(self, on: bool) -> None:
        self.left_curve.setVisible(on)
        self.right_curve.setVisible(on)
        if not on:  # drop the stale boundary so nothing lingers behind the hide
            self.left_curve.setData([], [])
            self.right_curve.setData([], [])

    def _on_laptime_toggled(self, on: bool) -> None:
        self.lap_widget.setVisible(on)
        if on:
            self._position_lap_widget()

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
        self._hover_points = []  # rebuilt every tick from what we actually draw

        if self.walls_cb.isChecked():
            left = _points_from((hub.latest(self._topics["left_topic"]) or {}).get("cones"))
            if len(left):
                self.left_curve.setData(left[:, 0], left[:, 1])
                self._register_hover(left, "Left boundary cone")
            right = _points_from((hub.latest(self._topics["right_topic"]) or {}).get("cones"))
            if len(right):
                self.right_curve.setData(right[:, 0], right[:, 1])
                self._register_hover(right, "Right boundary cone")

        traj = _points_from((hub.latest(self._topics["trajectory_topic"]) or {}).get("poses"))
        if len(traj):
            self.traj_curve.setData(traj[:, 0], traj[:, 1])
            self._register_hover(traj, "Planned path point")

        car = hub.latest(self._topics["car_topic"])
        car_xy = None
        if car and "position" in car:
            cx, cy = car["position"].get("x", 0.0), car["position"].get("y", 0.0)
            car_xy = (cx, cy)
            self.car_scatter.setData([cx], [cy])
            heading = _car_yaw(car["orientation"]) if "orientation" in car else 0.0
            self._hover_points.append(
                (cx, cy, f"Car — position ({cx:.2f}, {cy:.2f}) m, "
                         f"heading {math.degrees(heading):.0f}°")
            )
            self._trail.append(car_xy)
            if len(self._trail) > 2000:
                self._trail = self._trail[-2000:]
            trail = np.array(self._trail)
            self.trail_curve.setData(trail[:, 0], trail[:, 1])
            if self.laptime_cb.isChecked():
                self._laps.update(cx, cy, time.time())

        # car-pose transform shared by camera detections and the fusion cloud
        yaw = _car_yaw(car["orientation"]) if (car and "orientation" in car) else 0.0
        yaw += self._yaw_offset
        ox, oy = car_xy if car_xy else (0.0, 0.0)

        det = hub.latest(self._topics["detections_topic"])
        if det:
            self._draw_detections(det, ox, oy, yaw)

        if self.fusion_cb.isChecked():
            self._draw_fusion_cloud(ox, oy, yaw)

        if self.follow_cb.isChecked() and car_xy:
            self.plot.setXRange(car_xy[0] - 15, car_xy[0] + 15, padding=0)
            self.plot.setYRange(car_xy[1] - 15, car_xy[1] + 15, padding=0)

        # keep the lap overlay updated and pinned inside the (resizable) plot
        if not self.laptime_cb.isChecked():
            return
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

        # (message field, display label, scatter). Cones arrive grouped by colour
        # in the ConeDetection message — the colour is the group, not a per-cone
        # code — so we label by group and index and report both frames.
        groups = (
            ("blue", "Blue cone", self.blue_scatter),
            ("yellow", "Yellow cone", self.yellow_scatter),
            ("small_orange", "Orange cone", self.small_orange_scatter),
            ("big_orange", "Big orange cone", self.orange_scatter),
        )
        for field, label, scatter in groups:
            local = _points_from(det.get(field))
            glob = to_global(local)
            if not len(glob):
                scatter.clear()
                continue
            scatter.setData(glob[:, 0], glob[:, 1])
            for i in range(len(glob)):
                gx, gy = float(glob[i, 0]), float(glob[i, 1])
                lx, ly = float(local[i, 0]), float(local[i, 1])
                self._hover_points.append(
                    (gx, gy, f"{label} #{i} — world ({gx:.2f}, {gy:.2f}) m "
                             f"· local ({lx:.2f}, {ly:.2f}) m")
                )

    def _draw_fusion_cloud(self, ox: float, oy: float, yaw: float) -> None:
        """Render the lidar_fusion PointCloud2 as points, placed by the car pose.

        The cloud is in the velodyne (car-body) frame, so we apply the same
        rotate-then-translate the camera detections use. It won't be pixel-exact
        (the static camera->velodyne offset is ignored), but it overlays the
        fusion output onto the cones so you can eyeball agreement.
        """
        cloud = self.ctx.hub.latest(self._fusion_topic)
        pts = _cloud_xy(cloud) if cloud else np.empty((0, 2))
        if not len(pts):
            self.fusion_scatter.clear()
            return
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)
        gx = ox + pts[:, 0] * cos_y - pts[:, 1] * sin_y
        gy = oy + pts[:, 0] * sin_y + pts[:, 1] * cos_y
        self.fusion_scatter.setData(gx, gy)
        # register a capped, evenly-sampled subset for hover so a dense cloud
        # doesn't bog down the per-move nearest search
        stride = max(1, len(gx) // _FUSION_HOVER_MAX)
        for i in range(0, len(gx), stride):
            fx, fy = float(gx[i]), float(gy[i])
            self._hover_points.append(
                (fx, fy, f"LiDAR fusion point — ({fx:.2f}, {fy:.2f}) m")
            )

    # --- hover-to-identify --------------------------------------------------
    def _register_hover(self, pts: np.ndarray, label: str) -> None:
        """Register an Nx2 array of world points under a single label."""
        for i in range(len(pts)):
            x, y = float(pts[i, 0]), float(pts[i, 1])
            self._hover_points.append((x, y, f"{label} #{i} — ({x:.2f}, {y:.2f}) m"))

    def _on_mouse_moved(self, scene_pos) -> None:
        hit = self._nearest_hover(scene_pos)
        if hit is None:
            self._hover_timer.stop()
            self._hover_pending = None
            if self._hover_shown:
                self.hover_label.setText(self._HOVER_IDLE)
                self._hover_shown = False
            return
        if hit == self._hover_pending:
            return  # same target: let the running dwell finish / stay shown
        self._hover_pending = hit
        self._hover_shown = False
        self._hover_timer.start()

    def _on_hover_timeout(self) -> None:
        if self._hover_pending is not None:
            self.hover_label.setText(self._hover_pending)
            self._hover_shown = True

    def _nearest_hover(self, scene_pos) -> str | None:
        """Return the label of the nearest registered point within _HOVER_PX px."""
        if not self._hover_points:
            return None
        vb = self.plot.plotItem.vb
        best_text, best_d2 = None, float("inf")
        for x, y, text in self._hover_points:
            sp = vb.mapViewToScene(QPointF(x, y))
            d2 = (sp.x() - scene_pos.x()) ** 2 + (sp.y() - scene_pos.y()) ** 2
            if d2 < best_d2:
                best_d2, best_text = d2, text
        return best_text if best_d2 <= self._HOVER_PX ** 2 else None
