"""Record chosen topics to a rosbag2 via ``ros2 bag record``.

Drives the real ROS 2 CLI (sourcing ROS first — see :mod:`.proc`). It records
from the *local* ROS graph, so it fits the local-mode case: the dashboard
running on the machine that publishes the topics. Over a remote rosbridge link
there is no local graph to record; use JSON recording there.
"""
from __future__ import annotations

import os
import shlex
import time

from fsae_dashboard.recording.proc import RosProcess, detect_ros_setup

__all__ = ["RosbagRecorder", "detect_ros_setup"]


class RosbagRecorder(RosProcess):
    def __init__(
        self,
        topics: list[str],
        out_dir: str | None = None,
        setup_command: str | None = None,
    ):
        # None -> auto-detect; "" -> caller explicitly wants no sourcing.
        super().__init__(detect_ros_setup() if setup_command is None else setup_command)
        self.topics = list(topics)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = out_dir or os.getcwd()
        # ros2 bag record -o requires a path that does not yet exist; it creates it.
        self.bag_path = os.path.join(base, f"rosbag_{stamp}")

    def start(self) -> None:
        if not self.topics:
            raise ValueError("Select at least one topic to record a rosbag.")
        topics = " ".join(shlex.quote(t) for t in self.topics)
        self._launch(f"ros2 bag record -o {shlex.quote(self.bag_path)} {topics}")

    @property
    def status(self) -> str:
        return f"rosbag {len(self.topics)} topics → {os.path.basename(self.bag_path)}"
