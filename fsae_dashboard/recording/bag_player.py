"""Replay a recorded rosbag2 via ``ros2 bag play``.

Publishes the bag's messages back into the *local* ROS graph, so — exactly like
recording — it needs ROS 2 on this machine. To watch the replay in the
dashboard, connect in **local** mode (to a local rosbridge): the replayed topics
appear like any live ones. Over a remote rosbridge link there is no local graph
to publish into, so replay is a local-mode feature.
"""
from __future__ import annotations

import glob
import os
import shlex

from fsae_dashboard.recording.proc import RosProcess, detect_ros_setup

__all__ = ["BagPlayer", "list_bags"]


def list_bags(folder: str) -> list[str]:
    """Return rosbag2 directories at/under *folder*.

    A rosbag2 is a directory containing ``metadata.yaml``. *folder* itself may be
    a bag, or a parent holding many (as produced by our recorder).
    """
    if not folder or not os.path.isdir(folder):
        return []
    found: list[str] = []
    if os.path.exists(os.path.join(folder, "metadata.yaml")):
        found.append(folder)
    for entry in sorted(glob.glob(os.path.join(folder, "*"))):
        if os.path.isdir(entry) and os.path.exists(os.path.join(entry, "metadata.yaml")):
            found.append(entry)
    return found


class BagPlayer(RosProcess):
    def __init__(
        self,
        bag_path: str,
        setup_command: str | None = None,
        rate: float = 1.0,
        loop: bool = False,
    ):
        super().__init__(detect_ros_setup() if setup_command is None else setup_command)
        self.bag_path = bag_path
        self.rate = float(rate)
        self.loop = bool(loop)

    def start(self) -> None:
        if not self.bag_path or not os.path.isdir(self.bag_path):
            raise ValueError(f"Not a rosbag directory: {self.bag_path}")
        if not os.path.exists(os.path.join(self.bag_path, "metadata.yaml")):
            raise ValueError(f"No metadata.yaml in {self.bag_path} — not a rosbag2.")
        opts = f"--rate {self.rate:g}"
        if self.loop:
            opts += " --loop"
        self._launch(f"ros2 bag play {shlex.quote(self.bag_path)} {opts}")

    @property
    def status(self) -> str:
        tag = f"{self.rate:g}×" + (" loop" if self.loop else "")
        return f"replay {os.path.basename(self.bag_path)} ({tag})"
