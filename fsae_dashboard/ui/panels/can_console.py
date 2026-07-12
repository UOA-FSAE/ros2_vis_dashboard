"""CAN bus console — scrolling decoded view of CANStamped frames."""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from fsae_dashboard.ui.panels.base import Panel, register_panel


@register_panel
class CanConsolePanel(Panel):
    panel_type = "can_console"
    display_name = "CAN Console"

    def build_ui(self) -> None:
        self._topic = ""
        # latest frame per CAN id: id -> (dlc, data, count)
        self._by_id: "OrderedDict[int, list]" = OrderedDict()
        self._filter: set[int] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        bar = QHBoxLayout()
        self.topic_combo = QComboBox()
        self.topic_combo.setEditable(True)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._reload_topics)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter ids (hex, comma-sep) e.g. 300,301")
        self.filter_edit.editingFinished.connect(self._update_filter)
        bar.addWidget(QLabel("Topic:"))
        bar.addWidget(self.topic_combo, stretch=1)
        bar.addWidget(refresh)
        bar.addWidget(self.filter_edit, stretch=1)
        root.addLayout(bar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["CAN ID", "DLC", "Data (hex)", "Count"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table)

        self.topic_combo.currentTextChanged.connect(self._on_topic_changed)
        self.ctx.subs.topics_changed.connect(self._populate_topics)
        self._reload_topics()

    def _reload_topics(self) -> None:
        self.ctx.subs.list_topics()

    def _populate_topics(self, topics) -> None:
        current = self.topic_combo.currentText()
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        for ti in topics:
            if "CAN" in ti.type:
                self.topic_combo.addItem(ti.name, ti.type)
        if current:
            self.topic_combo.setCurrentText(current)
        self.topic_combo.blockSignals(False)

    def _on_topic_changed(self, topic: str) -> None:
        if self._topic:
            self.release(self._topic)
        self._by_id.clear()
        self._topic = topic
        if topic:
            self.subscribe(topic, self.topic_combo.currentData() or "fsae_interfaces/CANStamped")

    def _update_filter(self) -> None:
        text = self.filter_edit.text().strip()
        self._filter = set()
        for token in text.split(","):
            token = token.strip()
            if token:
                try:
                    self._filter.add(int(token, 16))
                except ValueError:
                    pass

    def get_config(self) -> dict:
        return {"topic": self._topic, "filter": self.filter_edit.text()}

    def apply_config(self, config: dict) -> None:
        if config.get("filter"):
            self.filter_edit.setText(config["filter"])
            self._update_filter()
        topic = config.get("topic", "")
        if topic:
            self.topic_combo.setCurrentText(topic)

    def on_tick(self) -> None:
        if not self._topic:
            return
        msg = self.ctx.hub.latest(self._topic)
        if not msg:
            return
        can = msg.get("can", msg)  # CANStamped wraps a CAN; tolerate bare CAN
        can_id = int(can.get("id", 0))
        data = can.get("data", [])
        dlc = int(can.get("dlc", len(data)))
        prev = self._by_id.get(can_id)
        count = (prev[2] + 1) if prev else 1
        self._by_id[can_id] = [dlc, data, count]
        self._render()

    def _render(self) -> None:
        ids = [i for i in self._by_id if not self._filter or i in self._filter]
        self.table.setRowCount(len(ids))
        for row, can_id in enumerate(sorted(ids)):
            dlc, data, count = self._by_id[can_id]
            hex_data = " ".join(f"{int(b) & 0xFF:02X}" for b in data)
            self.table.setItem(row, 0, QTableWidgetItem(f"0x{can_id:03X}"))
            self.table.setItem(row, 1, QTableWidgetItem(str(dlc)))
            self.table.setItem(row, 2, QTableWidgetItem(hex_data))
            self.table.setItem(row, 3, QTableWidgetItem(str(count)))
