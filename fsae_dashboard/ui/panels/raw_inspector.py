"""Raw message inspector — the ``ros2 topic echo`` replacement."""
from __future__ import annotations

import json
import time

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from fsae_dashboard.ui.panels.base import Panel, register_panel


def _truncate_bytes(obj, limit=32):
    """Shorten long byte/int arrays so image blobs don't flood the view."""
    if isinstance(obj, dict):
        return {k: _truncate_bytes(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        if len(obj) > limit and all(isinstance(x, (int, float)) for x in obj):
            return obj[:limit] + [f"...(+{len(obj) - limit} more)"]
        return [_truncate_bytes(x, limit) for x in obj[:limit]] + (
            [f"...(+{len(obj) - limit} more)"] if len(obj) > limit else []
        )
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    return obj


@register_panel
class RawInspectorPanel(Panel):
    panel_type = "raw_inspector"
    display_name = "Raw Message Inspector"

    def build_ui(self) -> None:
        self._topic = ""
        self._last_update = 0.0
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
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")
        root.addWidget(self.view)
        self.topic_combo.currentTextChanged.connect(self._on_topic_changed)
        self._reload_topics()

    def _reload_topics(self) -> None:
        current = self.topic_combo.currentText()
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        for ti in self.ctx.subs.list_topics():
            self.topic_combo.addItem(ti.name, ti.type)
        if current:
            self.topic_combo.setCurrentText(current)
        self.topic_combo.blockSignals(False)

    def _on_topic_changed(self, topic: str) -> None:
        if self._topic:
            self.release(self._topic)
        self._topic = topic
        if topic:
            self.subscribe(topic, self.topic_combo.currentData() or self.ctx.subs.type_of(topic))

    def get_config(self) -> dict:
        return {"topic": self._topic}

    def apply_config(self, config: dict) -> None:
        topic = config.get("topic", "")
        if topic:
            self.topic_combo.setCurrentText(topic)

    def on_tick(self) -> None:
        now = time.time()
        if now - self._last_update < 0.1 or not self._topic:  # 10 Hz text refresh
            return
        self._last_update = now
        msg = self.ctx.hub.latest(self._topic)
        if msg is None:
            return
        hz = self.ctx.hub.rate(self._topic)
        text = json.dumps(_truncate_bytes(msg), indent=2, default=str)
        self.view.setPlainText(f"# {self._topic}  ({hz:.0f} Hz)\n{text}")
