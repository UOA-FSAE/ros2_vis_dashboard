"""Real-time multi-series line plot — the core telemetry view."""
from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fsae_dashboard.ui.field_picker import FieldPickerDialog
from fsae_dashboard.ui.panels.base import Panel, register_panel

# a colour-blind-friendly cycle
_COLORS = ["#4C9AFF", "#FFB000", "#57D9A3", "#FF6B6B", "#B980F0", "#00C7E6", "#F76707"]


@register_panel
class TimeSeriesPanel(Panel):
    panel_type = "timeseries"
    display_name = "Time Series Plot"

    def build_ui(self) -> None:
        self._series: list[dict] = []  # {topic, type, field, label, curve}
        self._history_s = 30

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Field")
        add_btn.clicked.connect(self._add_field)
        rm_btn = QPushButton("- Remove")
        rm_btn.clicked.connect(self._remove_selected)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(rm_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(QWidget())
        self._history_spin = QSpinBox()
        self._history_spin.setRange(2, 600)
        self._history_spin.setValue(self._history_s)
        self._history_spin.setSuffix(" s")
        self._history_spin.valueChanged.connect(self._set_history)
        toolbar.addWidget(self._history_spin)
        root.addLayout(toolbar)

        body = QHBoxLayout()
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#1a1a1a")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "time", units="s")
        self.legend = self.plot.addLegend(offset=(10, 10))
        body.addWidget(self.plot, stretch=4)

        self.series_list = QListWidget()
        self.series_list.setMaximumWidth(180)
        body.addWidget(self.series_list, stretch=1)
        root.addLayout(body)

    # --- config ------------------------------------------------------------
    def get_config(self) -> dict:
        return {
            "history_s": self._history_s,
            "series": [
                {"topic": s["topic"], "type": s["type"], "field": s["field"], "label": s["label"]}
                for s in self._series
            ],
        }

    def apply_config(self, config: dict) -> None:
        self._set_history(int(config.get("history_s", 30)))
        self._history_spin.setValue(self._history_s)
        for entry in config.get("series", []):
            self._add_series(
                entry["topic"], entry.get("type", ""), entry["field"], entry.get("label", "")
            )

    def _set_history(self, seconds: int) -> None:
        self._history_s = seconds

    # --- series management -------------------------------------------------
    def _add_field(self) -> None:
        dlg = FieldPickerDialog(self.ctx, self)
        if dlg.exec() and (result := dlg.result_field()):
            topic, msg_type, field, label = result
            self._add_series(topic, msg_type, field, label)

    def _add_series(self, topic: str, msg_type: str, field: str, label: str) -> None:
        color = _COLORS[len(self._series) % len(_COLORS)]
        curve = self.plot.plot(pen=pg.mkPen(color, width=2), name=label or field)
        self._series.append(
            {"topic": topic, "type": msg_type, "field": field, "label": label, "curve": curve}
        )
        item = QListWidgetItem(f"● {label or field}")
        item.setForeground(pg.mkColor(color))
        self.series_list.addItem(item)
        self.subscribe(topic, msg_type)

    def _remove_selected(self) -> None:
        row = self.series_list.currentRow()
        if row < 0 or row >= len(self._series):
            return
        series = self._series.pop(row)
        self.plot.removeItem(series["curve"])
        try:
            self.legend.removeItem(series["label"] or series["field"])
        except Exception:  # noqa: BLE001
            pass
        self.series_list.takeItem(row)
        # release topic only if no other series needs it
        if not any(s["topic"] == series["topic"] for s in self._series):
            self.release(series["topic"])

    # --- render ------------------------------------------------------------
    def on_tick(self) -> None:
        now = time.time()
        for s in self._series:
            t, v = self.ctx.hub.series_snapshot(s["topic"], s["field"])
            if len(t):
                s["curve"].setData(t - now, v)
        self.plot.setXRange(-self._history_s, 0, padding=0.0)
