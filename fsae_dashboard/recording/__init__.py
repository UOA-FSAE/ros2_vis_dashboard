"""Recorders and replay that move telemetry between the live graph and disk.

Recording strategies (Record dialog):

* :class:`RosbagRecorder` shells out to ``ros2 bag record`` — a real, replayable
  rosbag2. Requires ROS 2 installed on this machine (the local-mode case).
* :class:`JsonRecorder` taps the DataHub message stream and writes JSON Lines.
  It works over *any* transport (rosbridge, local, mock) because it only needs
  the decoded dicts, and it rolls over to a new file at a size/record cap so a
  single interrupted file can never take the whole recording down.

Replay (Replay dialog):

* :class:`BagPlayer` shells out to ``ros2 bag play`` to publish a recorded bag
  back into the local ROS graph; :func:`list_bags` finds bags in a folder.
"""
from fsae_dashboard.recording.bag_player import BagPlayer, list_bags
from fsae_dashboard.recording.json_recorder import JsonRecorder
from fsae_dashboard.recording.proc import detect_ros_setup
from fsae_dashboard.recording.rosbag_recorder import RosbagRecorder

__all__ = [
    "JsonRecorder",
    "RosbagRecorder",
    "BagPlayer",
    "list_bags",
    "detect_ros_setup",
]
