"""Camera image panel — sensor_msgs/Image and CompressedImage."""
from __future__ import annotations

import base64
import time

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from fsae_dashboard.ui.panels.base import Panel, register_panel

_IMAGE_TYPES = ("sensor_msgs/Image", "sensor_msgs/CompressedImage")


def _as_bytes(data) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):  # base64 (rosbridge JSON / cbor fallback)
        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            return b""
    if isinstance(data, list):
        return bytes(b & 0xFF for b in data)
    return b""


@register_panel
class CameraPanel(Panel):
    panel_type = "camera"
    display_name = "Camera"

    def build_ui(self) -> None:
        self._topic = ""
        self._type = ""
        self._frames = 0
        self._fps_t0 = time.time()
        self._fps = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        bar = QHBoxLayout()
        self.topic_combo = QComboBox()
        self.topic_combo.setEditable(True)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._reload_topics)
        bar.addWidget(QLabel("Topic:"))
        bar.addWidget(self.topic_combo, stretch=1)
        bar.addWidget(refresh)
        root.addLayout(bar)

        self.image_label = QLabel("waiting for frames…")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(160, 120)
        self.image_label.setStyleSheet("background:#111;color:#888;")
        root.addWidget(self.image_label, stretch=1)

        self.status = QLabel("")
        root.addWidget(self.status)

        self.topic_combo.currentTextChanged.connect(self._on_topic_changed)
        self._reload_topics()

    def _reload_topics(self) -> None:
        current = self.topic_combo.currentText()
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        for ti in self.ctx.subs.list_topics():
            if any(k in ti.type for k in ("Image",)):
                self.topic_combo.addItem(ti.name, ti.type)
        if current:
            self.topic_combo.setCurrentText(current)
        self.topic_combo.blockSignals(False)

    def _on_topic_changed(self, topic: str) -> None:
        if self._topic:
            self.release(self._topic)
        self._topic = topic
        self._type = self.topic_combo.currentData() or self.ctx.subs.type_of(topic) or "sensor_msgs/Image"
        if topic:
            self.subscribe(topic, self._type)

    def get_config(self) -> dict:
        return {"topic": self._topic, "type": self._type}

    def apply_config(self, config: dict) -> None:
        topic = config.get("topic", "")
        if topic:
            self.topic_combo.setCurrentText(topic)

    def on_tick(self) -> None:
        if not self._topic:
            return
        msg = self.ctx.hub.latest(self._topic)
        if not msg:
            return
        pixmap = self._decode(msg)
        if pixmap is not None:
            self.image_label.setPixmap(
                pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self._frames += 1
            now = time.time()
            if now - self._fps_t0 >= 1.0:
                self._fps = self._frames / (now - self._fps_t0)
                self._frames = 0
                self._fps_t0 = now
            self.status.setText(f"{self._fps:4.1f} fps")

    def _decode(self, msg: dict) -> QPixmap | None:
        data = _as_bytes(msg.get("data"))
        if not data:
            return None
        if "format" in msg:  # CompressedImage (jpeg/png)
            img = QImage.fromData(data)
            return QPixmap.fromImage(img) if not img.isNull() else None
        # raw Image
        w, h = int(msg.get("width", 0)), int(msg.get("height", 0))
        enc = msg.get("encoding", "rgb8")
        if w <= 0 or h <= 0:
            return None
        arr = np.frombuffer(data, dtype=np.uint8)
        try:
            if enc in ("rgb8", "bgr8"):
                arr = arr[: w * h * 3].reshape(h, w, 3)
                if enc == "bgr8":
                    arr = arr[:, :, ::-1].copy()
                img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
            elif enc in ("mono8", "8UC1"):
                arr = arr[: w * h].reshape(h, w)
                img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
            else:
                return None
        except ValueError:
            return None
        return QPixmap.fromImage(img.copy())
