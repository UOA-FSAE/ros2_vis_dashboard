"""FSAE autonomous telemetry dashboard.

A configurable, dockable real-time visualisation app for the UOA-FSAE ROS 2
stack. Connects to a rosbridge / foxglove WebSocket bridge on the car
(optionally through an SSH tunnel) so no local ROS install is required, and
lets the user compose custom telemetry windows and save the layout as a config
file.
"""

__version__ = "0.1.0"
