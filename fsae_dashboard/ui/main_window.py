"""Main window: dockable panels, Add-Panel menu, config save/load, render loop.

Panels are wrapped in QDockWidgets so they can be dragged, floated (tear-off to
a second monitor), tabbed, and resized. A dashboard config records each panel's
logical description *and* the exact Qt dock geometry, so "custom windows for
different telemetry" round-trips through a single YAML file.
"""
from __future__ import annotations

import subprocess
import uuid

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
)

from fsae_dashboard import config as cfgmod
from fsae_dashboard.data.hub import DataHub
from fsae_dashboard.data.subscriptions import SubscriptionManager
from fsae_dashboard.ssh import SSHConfig, SSHManager
from fsae_dashboard.transport import build_transport
from fsae_dashboard.ui.connection_dialog import ConnectionDialog
from fsae_dashboard.ui.context import AppContext
from fsae_dashboard.ui.panels import create_panel, panel_types

RENDER_HZ = 30
_BRIDGE_LAUNCH = "ros2 launch rosbridge_server rosbridge_websocket_launch.xml"


class _Dock(QDockWidget):
    """Dock that reports when the user closes it, so we can dispose the panel."""

    closed = Signal(str)  # panel_id

    def __init__(self, title: str, panel_id: str):
        super().__init__(title)
        self.panel_id = panel_id
        self.setObjectName(panel_id)  # required for save/restoreState
        self.setAttribute(Qt.WA_DeleteOnClose, True)

    def closeEvent(self, event):  # noqa: N802 (Qt override)
        self.closed.emit(self.panel_id)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FSAE Telemetry Dashboard")
        self.resize(1500, 900)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.AnimatedDocks
            | QMainWindow.GroupedDragging
        )

        self.hub = DataHub()
        self.subs = SubscriptionManager(self.hub)
        self.ctx = AppContext(hub=self.hub, subs=self.subs)
        self.ssh = SSHManager()

        self._panels: dict[str, object] = {}
        self._docks: dict[str, _Dock] = {}
        self._current_path: str | None = None
        self._last_connection: dict = {}
        self._local_bridge: subprocess.Popen | None = None

        self.subs.status_changed.connect(self._on_status)

        self._build_menus()
        self._status = QLabel("Disconnected")
        self.statusBar().addPermanentWidget(self._status)

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / RENDER_HZ))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # --- menus -------------------------------------------------------------
    def _build_menus(self) -> None:
        conn = self.menuBar().addMenu("&Connection")
        act_connect = QAction("Connect…", self)
        act_connect.triggered.connect(self.open_connection_dialog)
        act_disconnect = QAction("Disconnect", self)
        act_disconnect.triggered.connect(self.disconnect)
        conn.addAction(act_connect)
        conn.addAction(act_disconnect)

        add = self.menuBar().addMenu("&Add Panel")
        for cls in panel_types():
            act = QAction(cls.display_name, self)
            act.triggered.connect(lambda _=False, c=cls: self.add_panel(c.panel_type))
            add.addAction(act)

        layout = self.menuBar().addMenu("&Layout")
        for name, slot in [
            ("New", self.new_layout),
            ("Open…", self.open_layout),
            ("Save", self.save_layout),
            ("Save As…", self.save_layout_as),
        ]:
            act = QAction(name, self)
            act.triggered.connect(slot)
            layout.addAction(act)

    # --- panels ------------------------------------------------------------
    def add_panel(self, panel_type: str, panel_id: str | None = None,
                  config: dict | None = None, area=Qt.RightDockWidgetArea) -> str:
        panel_id = panel_id or f"{panel_type}_{uuid.uuid4().hex[:8]}"
        panel = create_panel(panel_type, self.ctx, panel_id, config)
        dock = _Dock(panel.display_name, panel_id)
        dock.setWidget(panel)
        dock.closed.connect(self._on_dock_closed)
        self.addDockWidget(area, dock)
        self._panels[panel_id] = panel
        self._docks[panel_id] = dock
        return panel_id

    def _on_dock_closed(self, panel_id: str) -> None:
        panel = self._panels.pop(panel_id, None)
        self._docks.pop(panel_id, None)
        if panel is not None:
            try:
                panel.dispose()
            except Exception:  # noqa: BLE001
                pass

    def _clear_panels(self) -> None:
        for panel_id in list(self._panels):
            dock = self._docks.get(panel_id)
            panel = self._panels.get(panel_id)
            if panel is not None:
                try:
                    panel.dispose()
                except Exception:  # noqa: BLE001
                    pass
            if dock is not None:
                self.removeDockWidget(dock)
                dock.deleteLater()
        self._panels.clear()
        self._docks.clear()

    # --- render loop -------------------------------------------------------
    def _tick(self) -> None:
        for panel in list(self._panels.values()):
            try:
                panel.on_tick()
            except Exception:  # noqa: BLE001 - one bad panel must not stall the loop
                pass

    # --- connection --------------------------------------------------------
    def open_connection_dialog(self) -> None:
        dlg = ConnectionDialog(self._last_connection, self)
        if dlg.exec():
            self.connect_with(dlg.settings())

    def connect_with(self, settings: dict) -> None:
        self._last_connection = settings
        try:
            transport_settings = dict(settings)
            ssh = settings.get("ssh")
            if ssh and settings.get("transport") == "rosbridge":
                self._start_ssh(ssh)
                transport_settings = {
                    "transport": "rosbridge",
                    "host": "127.0.0.1",
                    "port": ssh.get("local_port", 9090),
                }
            elif settings.get("launch_local_bridge"):
                self._start_local_bridge(settings.get("port", 9090))
            transport = build_transport(transport_settings)
            self.subs.set_transport(transport)
            QTimer.singleShot(800, self._refresh_topics)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Connection failed", str(exc))

    def _start_ssh(self, ssh: dict) -> None:
        cfg = SSHConfig(
            host=ssh["host"],
            port=ssh.get("port", 22),
            username=ssh.get("username", ""),
            password=ssh.get("password"),
            key_path=ssh.get("key_path"),
            forwards={ssh.get("local_port", 9090): ssh.get("remote_port", 9090)},
        )
        self.ssh.connect(cfg)
        if ssh.get("launch_bridge"):
            cmd = f"bash -lc '{cfg.setup_command}; {_BRIDGE_LAUNCH}'"
            self.ssh.run_command_streaming(cmd, lambda line: None)

    def _start_local_bridge(self, port: int) -> None:
        """Launch rosbridge on this machine as a child process.

        Used by "local" mode when the dashboard runs on the same machine that
        publishes the topics. The process inherits the current environment, so
        the app must be launched from a ROS-sourced shell.
        """
        if self._local_bridge is not None and self._local_bridge.poll() is None:
            return  # already running from a previous connect
        cmd = f"{_BRIDGE_LAUNCH} port:={int(port)}"
        self._local_bridge = subprocess.Popen(
            ["bash", "-lc", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stop_local_bridge(self) -> None:
        proc = self._local_bridge
        self._local_bridge = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    def disconnect(self) -> None:
        self.subs.set_transport(None)
        if self.ssh.connected:
            self.ssh.disconnect()
        self._stop_local_bridge()
        self._status.setText("Disconnected")

    def _refresh_topics(self) -> None:
        self.subs.list_topics()

    def _on_status(self, connected: bool, detail: str) -> None:
        self._status.setText(f"Connected: {detail}" if connected else f"Disconnected ({detail})")
        if connected:
            QTimer.singleShot(300, self._refresh_topics)

    # --- config persistence ------------------------------------------------
    def dump_config(self) -> dict:
        cfg = cfgmod.empty_config()
        cfg["connection"] = self._last_connection
        cfg["panels"] = [
            {
                "id": pid,
                "type": panel.panel_type,
                "title": self._docks[pid].windowTitle(),
                "config": panel.get_config(),
            }
            for pid, panel in self._panels.items()
        ]
        cfg["layout_state"] = cfgmod.encode_blob(self.saveState().data())
        cfg["geometry"] = cfgmod.encode_blob(self.saveGeometry().data())
        return cfg

    def load_config_dict(self, cfg: dict) -> None:
        self._clear_panels()
        for entry in cfg.get("panels", []):
            self.add_panel(entry["type"], entry.get("id"), entry.get("config"))
        state = cfgmod.decode_blob(cfg.get("layout_state", ""))
        geom = cfgmod.decode_blob(cfg.get("geometry", ""))
        if geom:
            self.restoreGeometry(QByteArray(geom))
        if state:
            self.restoreState(QByteArray(state))
        self._last_connection = cfg.get("connection", {})

    def new_layout(self) -> None:
        self._clear_panels()
        self._current_path = None
        self.build_default_layout()

    def open_layout(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open dashboard config", str(cfgmod.config_dir()), "YAML (*.yaml *.yml)"
        )
        if path:
            self.load_config_dict(cfgmod.load_config(path))
            self._current_path = path

    def save_layout(self) -> None:
        if not self._current_path:
            self.save_layout_as()
            return
        cfgmod.save_config(self._current_path, self.dump_config())
        self.statusBar().showMessage(f"Saved {self._current_path}", 3000)

    def save_layout_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save dashboard config", str(cfgmod.default_layout_path()), "YAML (*.yaml *.yml)"
        )
        if path:
            self._current_path = path
            self.save_layout()

    # --- default first-run layout -----------------------------------------
    def build_default_layout(self) -> None:
        tv = self.add_panel("trackview", area=Qt.LeftDockWidgetArea)
        ts = self.add_panel("timeseries", area=Qt.RightDockWidgetArea,
                            config={"history_s": 30, "series": [
                                {"topic": "/fsae/control/cmd_vel", "type": "ackermann_msgs/AckermannDriveStamped",
                                 "field": "drive.speed", "label": "speed"},
                                {"topic": "/fsae/control/cmd_vel", "type": "ackermann_msgs/AckermannDriveStamped",
                                 "field": "drive.steering_angle", "label": "steer"},
                            ]})
        stats = self.add_panel("stat_tiles", area=Qt.RightDockWidgetArea, config={"tiles": [
            {"topic": "/fsae/mission/as_status", "type": "std_msgs/UInt8", "field": "data", "label": "AS state", "fmt": "{:.0f}"},
            {"topic": "/fsae/hardware/battery_state", "type": "sensor_msgs/BatteryState", "field": "voltage", "label": "HV battery (V)", "fmt": "{:.1f}"},
        ]})
        browser = self.add_panel("topic_browser", area=Qt.BottomDockWidgetArea)
        self.splitDockWidget(self._docks[ts], self._docks[stats], Qt.Vertical)

    # --- shutdown ----------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802
        try:
            self.subs.shutdown()
            if self.ssh.connected:
                self.ssh.disconnect()
            self._stop_local_bridge()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
