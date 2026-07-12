"""Application entry point."""
from __future__ import annotations

import argparse
import sys

import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

from fsae_dashboard import config as cfgmod
from fsae_dashboard.ui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="FSAE telemetry dashboard")
    parser.add_argument("--config", help="dashboard config (.yaml) to load on start")
    parser.add_argument("--mock", action="store_true", help="connect to the built-in simulator")
    args = parser.parse_args()

    pg.setConfigOptions(antialias=True, useOpenGL=False)

    app = QApplication(sys.argv)
    app.setApplicationName("FSAE Dashboard")

    win = MainWindow()

    if args.config:
        win.load_config_dict(cfgmod.load_config(args.config))
    else:
        win.build_default_layout()

    win.show()

    if args.mock:
        win.connect_with({"transport": "mock"})

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
