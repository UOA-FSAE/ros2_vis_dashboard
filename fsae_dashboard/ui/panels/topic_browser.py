"""Topic browser — live list of topics with type, rate, and subscribe toggle.

Subscribing here makes data flow into the hub so other panels' field pickers can
sample it, and so the rate column is populated. It's the discovery-driven
counter to hard-coded topic names (see the zed/ prefix bug in the plan).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from fsae_dashboard.ui.panels.base import Panel, register_panel


@register_panel
class TopicBrowserPanel(Panel):
    panel_type = "topic_browser"
    display_name = "Topic Browser"

    def build_ui(self) -> None:
        self._rows: dict[str, int] = {}  # topic -> row
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        bar = QHBoxLayout()
        refresh = QPushButton("Refresh topics")
        refresh.clicked.connect(self.reload)
        bar.addWidget(refresh)
        bar.addStretch(1)
        root.addLayout(bar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Sub", "Topic", "Type", "Hz"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Let the dock shrink freely no matter how many rows: the table keeps a
        # tiny minimum size and scrolls, instead of forcing its content height
        # onto the dock and squeezing neighbouring panels.
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        self.table.setMinimumSize(60, 60)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.table.setTextElideMode(Qt.ElideRight)  # long type strings elide, don't widen
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Sub checkbox
        header.setSectionResizeMode(1, QHeaderView.Stretch)           # Topic
        header.setSectionResizeMode(2, QHeaderView.Interactive)       # Type (user-resizable)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Hz
        self.table.setColumnWidth(2, 200)
        root.addWidget(self.table)
        self.table.itemChanged.connect(self._on_item_changed)

    def get_config(self) -> dict:
        return {"subscribed": sorted(self._subscribed)}

    def apply_config(self, config: dict) -> None:
        self.reload()
        want = set(config.get("subscribed", []))
        for topic in want:
            self.subscribe(topic, self.ctx.subs.type_of(topic))

    def reload(self) -> None:
        topics = self.ctx.subs.list_topics()
        self.table.blockSignals(True)
        self.table.setRowCount(len(topics))
        self._rows.clear()
        for row, ti in enumerate(topics):
            self._rows[ti.name] = row
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked if ti.name in self._subscribed else Qt.Unchecked)
            check.setData(Qt.UserRole, ti.name)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(ti.name))
            self.table.setItem(row, 2, QTableWidgetItem(ti.type))
            self.table.setItem(row, 3, QTableWidgetItem("—"))
        self.table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        topic = item.data(Qt.UserRole)
        msg_type = self.ctx.subs.type_of(topic)
        if item.checkState() == Qt.Checked:
            self.subscribe(topic, msg_type)
        else:
            self.release(topic)

    def on_tick(self) -> None:
        for topic, row in self._rows.items():
            cell = self.table.item(row, 3)
            if cell is not None:
                hz = self.ctx.hub.rate(topic)
                cell.setText(f"{hz:.0f}" if hz else "—")
