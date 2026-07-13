"""Connection dialog: local, direct rosbridge, rosbridge over SSH, or mock.

Produces a settings dict consumed by app wiring:

    {transport: "mock"}
    {transport: "rosbridge", host, port}
    {transport: "rosbridge", host: "127.0.0.1", port: <local>, ssh: {...}}
    {transport: "rosbridge", host: "127.0.0.1", port, mode: "local",
     launch_local_bridge: bool}

"local" mode is for running the dashboard on the same machine that publishes
the ROS topics: it connects straight to a rosbridge on 127.0.0.1, so there is
no remote host to enter and no SSH tunnel. When SSH is enabled instead, the
data plane connects to a forwarded localhost port, so "host/port" are the
*bridge* endpoint as seen from the Jetson.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


class ConnectionDialog(QDialog):
    def __init__(self, last: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to car")
        self.resize(420, 520)
        last = last or {}
        ssh = last.get("ssh", {})

        layout = QVBoxLayout(self)

        # transport kind. "local" and "rosbridge" both build a rosbridge
        # transport; "mode" in the saved settings distinguishes them so the
        # dialog reopens on the same tab.
        kind_box = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems(["local", "rosbridge", "mock"])
        self.kind.setCurrentText(last.get("mode", last.get("transport", "local")))
        kind_box.addRow("Data source", self.kind)
        layout.addLayout(kind_box)

        # local endpoint (bridge on this machine)
        self.local_group = QGroupBox("Local (this machine)")
        lform = QFormLayout(self.local_group)
        self.local_bridge_port = QSpinBox()
        self.local_bridge_port.setRange(1, 65535)
        self.local_bridge_port.setValue(int(last.get("port", 9090)))
        self.launch_local = QCheckBox("Launch rosbridge locally on connect")
        self.launch_local.setChecked(bool(last.get("launch_local_bridge", False)))
        lform.addRow("Bridge port", self.local_bridge_port)
        lform.addRow("", self.launch_local)
        layout.addWidget(self.local_group)

        # remote bridge endpoint
        self.bridge_group = QGroupBox("Bridge (rosbridge / foxglove)")
        bform = QFormLayout(self.bridge_group)
        self.host = QLineEdit(last.get("host", "192.168.1.10"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(last.get("port", 9090)))
        bform.addRow("Bridge host", self.host)
        bform.addRow("Bridge port", self.port)
        layout.addWidget(self.bridge_group)

        # ssh tunnel
        self.ssh_group = QGroupBox("SSH tunnel to Jetson")
        self.ssh_group.setCheckable(True)
        self.ssh_group.setChecked(bool(ssh))
        sform = QFormLayout(self.ssh_group)
        self.ssh_host = QLineEdit(ssh.get("host", ""))
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(int(ssh.get("port", 22)))
        self.ssh_user = QLineEdit(ssh.get("username", "jetson"))
        self.ssh_pass = QLineEdit(ssh.get("password", ""))
        self.ssh_pass.setEchoMode(QLineEdit.Password)
        self.ssh_key = QLineEdit(ssh.get("key_path", ""))
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(int(ssh.get("local_port", 9090)))
        self.launch_bridge = QCheckBox("Launch bridge on connect")
        self.launch_bridge.setChecked(ssh.get("launch_bridge", False))
        sform.addRow("SSH host/IP", self.ssh_host)
        sform.addRow("SSH port", self.ssh_port)
        sform.addRow("Username", self.ssh_user)
        sform.addRow("Password", self.ssh_pass)
        sform.addRow("Key file", self.ssh_key)
        sform.addRow("Local port", self.local_port)
        sform.addRow("", self.launch_bridge)
        layout.addWidget(self.ssh_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.kind.currentTextChanged.connect(self._sync_enabled)
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        kind = self.kind.currentText()
        self.local_group.setVisible(kind == "local")
        self.bridge_group.setVisible(kind == "rosbridge")
        self.ssh_group.setVisible(kind == "rosbridge")

    def settings(self) -> dict:
        kind = self.kind.currentText()
        if kind == "mock":
            return {"transport": "mock"}
        if kind == "local":
            out: dict = {
                "transport": "rosbridge",
                "host": "127.0.0.1",
                "port": self.local_bridge_port.value(),
                "mode": "local",
            }
            if self.launch_local.isChecked():
                out["launch_local_bridge"] = True
            return out
        out = {
            "transport": "rosbridge",
            "host": self.host.text().strip(),
            "port": self.port.value(),
        }
        if self.ssh_group.isChecked():
            out["ssh"] = {
                "host": self.ssh_host.text().strip(),
                "port": self.ssh_port.value(),
                "username": self.ssh_user.text().strip(),
                "password": self.ssh_pass.text() or None,
                "key_path": self.ssh_key.text().strip() or None,
                "local_port": self.local_port.value(),
                "remote_port": self.port.value(),
                "launch_bridge": self.launch_bridge.isChecked(),
            }
        return out
