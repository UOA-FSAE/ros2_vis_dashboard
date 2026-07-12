"""Panel registry. Importing this package registers all built-in panel types."""
from fsae_dashboard.ui.panels.base import (
    Panel,
    create_panel,
    panel_types,
    register_panel,
)

# Import for side effect: each module registers its panel via @register_panel.
from fsae_dashboard.ui.panels import (  # noqa: E402,F401
    can_console,
    camera,
    raw_inspector,
    stat_tiles,
    timeseries,
    topic_browser,
    trackview,
)

__all__ = ["Panel", "create_panel", "panel_types", "register_panel"]
