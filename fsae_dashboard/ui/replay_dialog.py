"""Replay dialog: pick where bags live, choose one, and play it back.

Produces a settings dict consumed by MainWindow.start_replay:

    {bag_path, bags_dir, rate, loop, setup_command}
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from fsae_dashboard.recording.bag_player import list_bags
from fsae_dashboard.recording.proc import detect_ros_setup


class ReplayDialog(QDialog):
    def __init__(self, bags_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Replay rosbag")
        self.resize(480, 520)

        layout = QVBoxLayout(self)

        # where bags are stored (remembered across sessions)
        dir_row = QHBoxLayout()
        self.bags_dir = QLineEdit(bags_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self.bags_dir)
        dir_row.addWidget(browse)
        form = QFormLayout()
        form.addRow("Bags folder", dir_row)
        layout.addLayout(form)

        layout.addWidget(QLabel("Recorded bags:"))
        self.bag_list = QListWidget()
        self.bag_list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.bag_list)

        opts = QFormLayout()
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 20.0)
        self.rate.setSingleStep(0.1)
        self.rate.setValue(1.0)
        self.rate.setSuffix("×")
        self.loop = QCheckBox("Loop")
        self.ros_setup = QLineEdit(detect_ros_setup())
        self.ros_setup.setPlaceholderText("source /opt/ros/<distro>/setup.bash")
        self.ros_setup.setToolTip(
            "Sourced before running ros2. Append your workspace overlay for "
            "custom message types, e.g.  … && source ~/ws/install/setup.bash"
        )
        opts.addRow("Playback rate", self.rate)
        opts.addRow("", self.loop)
        opts.addRow("ROS setup", self.ros_setup)
        layout.addLayout(opts)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Play")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.bags_dir.textChanged.connect(self._reload)
        self.bag_list.currentRowChanged.connect(self._sync_ok)
        self._reload()

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Bags folder", self.bags_dir.text())
        if path:
            self.bags_dir.setText(path)

    def _reload(self) -> None:
        self.bag_list.clear()
        self._bags = list_bags(self.bags_dir.text().strip())
        for bag in self._bags:
            self.bag_list.addItem(os.path.basename(bag.rstrip("/")))
        if self._bags:
            self.bag_list.setCurrentRow(0)
        else:
            self.bag_list.addItem("(no rosbags found in this folder)")
            self.bag_list.item(0).setFlags(Qt.NoItemFlags)
        self._sync_ok()

    def _sync_ok(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(bool(self._bags) and 0 <= self.bag_list.currentRow() < len(self._bags))

    def selected_bag(self) -> str | None:
        row = self.bag_list.currentRow()
        if 0 <= row < len(self._bags):
            return self._bags[row]
        return None

    def settings(self) -> dict:
        return {
            "bag_path": self.selected_bag(),
            "bags_dir": self.bags_dir.text().strip(),
            "rate": self.rate.value(),
            "loop": self.loop.isChecked(),
            "setup_command": self.ros_setup.text().strip(),
        }
