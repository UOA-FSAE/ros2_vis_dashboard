"""Record dialog: choose rosbag (many topics) or JSON (one topic) and start.

Produces a settings dict consumed by MainWindow.start_recording:

    {format: "rosbag", out_dir, topics: [name, ...]}
    {format: "json",   out_dir, topic, msg_type, max_records, max_bytes}
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from fsae_dashboard.recording.rosbag_recorder import detect_ros_setup


class RecordDialog(QDialog):
    def __init__(self, topics, out_dir: str | None = None, parent=None):
        """topics: iterable of TopicInfo (has .name and .type)."""
        super().__init__(parent)
        self.setWindowTitle("Record telemetry")
        self.resize(460, 560)
        self._types = {ti.name: ti.type for ti in topics}
        names = sorted(self._types)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(["rosbag (multiple topics)", "JSON (single topic)"])
        form.addRow("Format", self.format)

        dir_row = QHBoxLayout()
        self.out_dir = QLineEdit(out_dir or os.getcwd())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self.out_dir)
        dir_row.addWidget(browse)
        form.addRow("Output folder", dir_row)
        layout.addLayout(form)

        if not names:
            layout.addWidget(QLabel("No topics discovered yet — connect and "
                                    "refresh topics first."))

        # rosbag: setup command + multi-select checklist
        self.bag_group = QGroupBox("Topics to record")
        bag_layout = QVBoxLayout(self.bag_group)
        setup_form = QFormLayout()
        self.ros_setup = QLineEdit(detect_ros_setup())
        self.ros_setup.setPlaceholderText("source /opt/ros/<distro>/setup.bash")
        self.ros_setup.setToolTip(
            "Sourced before running ros2 (a GUI app usually isn't launched from "
            "a ROS-sourced shell). Append your workspace overlay for custom "
            "message types, e.g.  … && source ~/ws/install/setup.bash"
        )
        setup_form.addRow("ROS setup", self.ros_setup)
        bag_layout.addLayout(setup_form)
        self.topic_list = QListWidget()
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.topic_list.addItem(item)
        bag_layout.addWidget(self.topic_list)
        layout.addWidget(self.bag_group)

        # json: single topic + rollover cap
        self.json_group = QGroupBox("JSON options")
        jform = QFormLayout(self.json_group)
        self.topic_combo = QComboBox()
        self.topic_combo.addItems(names)
        self.max_records = QSpinBox()
        self.max_records.setRange(1, 100_000_000)
        self.max_records.setValue(50_000)
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 100_000)
        self.max_mb.setValue(100)
        jform.addRow("Topic", self.topic_combo)
        jform.addRow("Max records / file", self.max_records)
        jform.addRow("Max MB / file", self.max_mb)
        layout.addWidget(self.json_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Start recording")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.format.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        is_bag = self.format.currentIndex() == 0
        self.bag_group.setVisible(is_bag)
        self.json_group.setVisible(not is_bag)

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir.text())
        if path:
            self.out_dir.setText(path)

    def settings(self) -> dict:
        out_dir = self.out_dir.text().strip() or os.getcwd()
        if self.format.currentIndex() == 0:
            checked = [
                self.topic_list.item(i).text()
                for i in range(self.topic_list.count())
                if self.topic_list.item(i).checkState() == Qt.Checked
            ]
            return {
                "format": "rosbag",
                "out_dir": out_dir,
                "topics": checked,
                "setup_command": self.ros_setup.text().strip(),
            }
        topic = self.topic_combo.currentText()
        return {
            "format": "json",
            "out_dir": out_dir,
            "topic": topic,
            "msg_type": self._types.get(topic, ""),
            "max_records": self.max_records.value(),
            "max_bytes": self.max_mb.value() * 1024 * 1024,
        }
