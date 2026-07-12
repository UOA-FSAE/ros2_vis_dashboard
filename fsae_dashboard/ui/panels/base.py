"""Base class + registry for dockable telemetry panels.

Every panel is a self-contained widget that:
  * declares a stable ``panel_type`` (used in saved configs) and ``display_name``
  * serialises its state via get_config()/apply_config() (the "custom window")
  * repaints from the DataHub on on_tick(), called centrally at ~30 Hz
  * manages its own subscriptions (released on dispose())

New panel types register with @register_panel and immediately appear in the
"Add Panel" menu — this is the extension point for new telemetry views.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from fsae_dashboard.ui.context import AppContext

_REGISTRY: dict[str, type["Panel"]] = {}


def register_panel(cls: type["Panel"]) -> type["Panel"]:
    _REGISTRY[cls.panel_type] = cls
    return cls


def panel_types() -> list[type["Panel"]]:
    return sorted(_REGISTRY.values(), key=lambda c: c.display_name)


def create_panel(panel_type: str, ctx: AppContext, panel_id: str, config: dict | None) -> "Panel":
    cls = _REGISTRY[panel_type]
    return cls(ctx, panel_id, config or {})


class Panel(QWidget):
    #: stable identifier stored in configs — never rename once shipped
    panel_type: str = "base"
    #: label shown in the Add Panel menu
    display_name: str = "Panel"

    def __init__(self, ctx: AppContext, panel_id: str, config: dict):
        super().__init__()
        self.ctx = ctx
        self.panel_id = panel_id
        self._subscribed: set[str] = set()
        self.build_ui()
        self.apply_config(config)

    # --- to override -------------------------------------------------------
    def build_ui(self) -> None:
        """Construct child widgets. Called once before apply_config."""

    def get_config(self) -> dict[str, Any]:
        """Return a JSON/YAML-serialisable description of this panel's state."""
        return {}

    def apply_config(self, config: dict) -> None:
        """Restore state produced by get_config()."""

    def on_tick(self) -> None:
        """Repaint from the DataHub. Called on the UI thread at the render rate."""

    # --- subscription helpers ---------------------------------------------
    def subscribe(self, topic: str, msg_type: str = "", throttle_rate: int = 0) -> None:
        if topic and topic not in self._subscribed:
            self.ctx.subs.subscribe(topic, msg_type, throttle_rate)
            self._subscribed.add(topic)

    def release(self, topic: str) -> None:
        if topic in self._subscribed:
            self.ctx.subs.release(topic)
            self._subscribed.discard(topic)

    def release_all(self) -> None:
        for topic in list(self._subscribed):
            self.release(topic)

    def dispose(self) -> None:
        """Called when the panel is closed. Releases subscriptions."""
        self.release_all()
