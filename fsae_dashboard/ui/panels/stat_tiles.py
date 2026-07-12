"""Stat tile grid — big at-a-glance numeric/enum values."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from fsae_dashboard.data.fields import get_field
from fsae_dashboard.ui.field_picker import FieldPickerDialog
from fsae_dashboard.ui.panels.base import Panel, register_panel

# named AS states for the /fsae/mission/as_status enum (as_status_node ordering:
# 0 finished, 1 emergency, 2 ready, 3 driving, 4 off — see ARCHITECTURE.md)
_AS_STATES = {0: "FINISHED", 1: "EMERGENCY", 2: "READY", 3: "DRIVING", 4: "OFF"}


class _Tile(QFrame):
    def __init__(self, label: str):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame{background:#1e1e1e;border-radius:8px;} QLabel{color:#ddd;}")
        lay = QVBoxLayout(self)
        self.value = QLabel("—")
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setStyleSheet("font-size:26px;font-weight:600;color:#fff;")
        self.name = QLabel(label)
        self.name.setAlignment(Qt.AlignCenter)
        self.name.setStyleSheet("font-size:11px;color:#999;")
        lay.addWidget(self.value)
        lay.addWidget(self.name)

    def set_value(self, text: str, color: str = "#fff") -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size:26px;font-weight:600;color:{color};")


@register_panel
class StatTilesPanel(Panel):
    panel_type = "stat_tiles"
    display_name = "Stat Tiles"

    def build_ui(self) -> None:
        self._tiles: list[dict] = []  # {topic,type,field,label,fmt,tile}
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        bar = QHBoxLayout()
        add = QPushButton("+ Tile")
        add.clicked.connect(self._add_tile_dialog)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        bar.addWidget(add)
        bar.addWidget(clear)
        bar.addStretch(1)
        root.addLayout(bar)
        self.grid = QGridLayout()
        root.addLayout(self.grid)
        root.addStretch(1)

    def get_config(self) -> dict:
        return {
            "tiles": [
                {"topic": t["topic"], "type": t["type"], "field": t["field"],
                 "label": t["label"], "fmt": t["fmt"]}
                for t in self._tiles
            ]
        }

    def apply_config(self, config: dict) -> None:
        for entry in config.get("tiles", []):
            self._add_tile(entry["topic"], entry.get("type", ""), entry["field"],
                           entry.get("label", ""), entry.get("fmt", "{:.2f}"))

    def _add_tile_dialog(self) -> None:
        dlg = FieldPickerDialog(self.ctx, self)
        if dlg.exec() and (result := dlg.result_field()):
            topic, msg_type, field, label = result
            self._add_tile(topic, msg_type, field, label, "{:.2f}")

    def _add_tile(self, topic: str, msg_type: str, field: str, label: str, fmt: str) -> None:
        tile = _Tile(label or f"{topic}.{field}")
        n = len(self._tiles)
        self.grid.addWidget(tile, n // 3, n % 3)
        self._tiles.append(
            {"topic": topic, "type": msg_type, "field": field, "label": label, "fmt": fmt, "tile": tile}
        )
        self.subscribe(topic, msg_type)

    def _clear(self) -> None:
        for t in self._tiles:
            t["tile"].setParent(None)
        self.release_all()
        self._tiles.clear()

    def on_tick(self) -> None:
        for t in self._tiles:
            msg = self.ctx.hub.latest(t["topic"])
            if not msg:
                continue
            val = get_field(msg, t["field"])
            if val is None:
                continue
            if t["topic"].endswith("as_status"):
                t["tile"].set_value(_AS_STATES.get(int(val), str(int(val))), "#57D9A3")
            else:
                try:
                    t["tile"].set_value(t["fmt"].format(val))
                except (ValueError, KeyError):
                    t["tile"].set_value(str(val))
