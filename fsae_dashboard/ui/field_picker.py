"""Dialog to choose a topic + numeric field to plot.

Picks a topic from live discovery, briefly subscribes to sample a message, then
lists its numeric leaf fields (see data.fields). Returns (topic, type, field).
"""
from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
)

from fsae_dashboard.data.fields import flatten_numeric_fields
from fsae_dashboard.ui.context import AppContext


class FieldPickerDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Add field")
        self.resize(420, 460)
        self._sampled_topic: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.topic_combo = QComboBox()
        ctx.subs.topics_changed.connect(self._populate_topics)
        topics = ctx.subs.list_topics()
        for ti in topics:
            self.topic_combo.addItem(ti.name, ti.type)
        form.addRow("Topic", self.topic_combo)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("(optional) legend label")
        form.addRow("Label", self.label_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel("Numeric fields (sampled live):"))
        self.field_list = QListWidget()
        layout.addWidget(self.field_list)

        self.manual_edit = QLineEdit()
        self.manual_edit.setPlaceholderText("or type a field path, e.g. drive.speed")
        layout.addWidget(self.manual_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.topic_combo.currentIndexChanged.connect(self._sample_topic)
        if topics:
            self._sample_topic()

    def _populate_topics(self, topics) -> None:
        current = self.topic_combo.currentText()
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        for ti in topics:
            self.topic_combo.addItem(ti.name, ti.type)
        if current:
            self.topic_combo.setCurrentText(current)
        self.topic_combo.blockSignals(False)
        if not current and self.topic_combo.count() > 0:
            self._sample_topic()

    def _sample_topic(self) -> None:
        topic = self.topic_combo.currentText()
        msg_type = self.topic_combo.currentData() or ""
        if not topic:
            return
        self.field_list.clear()
        # Subscribe briefly to capture one message.
        self.ctx.subs.subscribe(topic, msg_type)
        deadline = time.time() + 2.0
        msg = None
        while time.time() < deadline:
            msg = self.ctx.hub.latest(topic)
            if msg:
                break
            time.sleep(0.02)
        self.ctx.subs.release(topic)
        if msg:
            for path in flatten_numeric_fields(msg):
                self.field_list.addItem(path)
        else:
            self.field_list.addItem("(no message received — enter path manually)")

    def result_field(self) -> tuple[str, str, str, str] | None:
        """Return (topic, msg_type, field_path, label) or None."""
        topic = self.topic_combo.currentText()
        msg_type = self.topic_combo.currentData() or ""
        field = self.manual_edit.text().strip()
        if not field and self.field_list.currentItem():
            text = self.field_list.currentItem().text()
            if not text.startswith("("):
                field = text
        if not topic or not field:
            return None
        label = self.label_edit.text().strip() or f"{topic.rsplit('/', 1)[-1]}.{field}"
        return topic, msg_type, field, label
