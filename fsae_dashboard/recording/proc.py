"""Shared plumbing for the ROS 2 CLI subprocesses (bag record / bag play).

A GUI app is usually *not* launched from a ROS-sourced shell, so both commands
run through ``bash -lc`` and optionally ``source`` a ROS setup file first. We
stream stderr so a failure ("ros2: command not found", workspace not sourced)
can be surfaced in the UI instead of vanishing into the terminal, and we shut
down with SIGINT because ros2 only finalises cleanly on Ctrl-C.
"""
from __future__ import annotations

import glob
import os
import shlex
import signal
import subprocess
import threading
from collections import deque


def detect_ros_setup() -> str:
    """Best-guess ``source <setup.bash>`` to make ``ros2`` available, or "".

    Prefers the distro named by $ROS_DISTRO, then any ``/opt/ros/*/setup.bash``.
    Returned as a shell command so callers can prepend it before ``ros2``.
    """
    candidates: list[str] = []
    distro = os.environ.get("ROS_DISTRO")
    if distro:
        candidates.append(f"/opt/ros/{distro}/setup.bash")
    candidates += sorted(glob.glob("/opt/ros/*/setup.bash"), reverse=True)
    for path in candidates:
        if os.path.exists(path):
            return f"source {shlex.quote(path)}"
    return ""


class RosProcess:
    """A managed ``bash -lc`` child running a (sourced) ros2 command."""

    def __init__(self, setup_command: str):
        self.setup_command = setup_command
        self._proc: subprocess.Popen | None = None
        self._err: deque[str] = deque(maxlen=100)

    def _launch(self, command: str) -> None:
        inner = command
        if self.setup_command.strip():
            inner = f"{self.setup_command} && {command}"
        self._proc = subprocess.Popen(
            ["bash", "-lc", inner],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._drain, name="ros-proc-stderr", daemon=True).start()

    def _drain(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                self._err.append(line)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self) -> int | None:
        """Exit code once finished, else None (still running / never started)."""
        return self._proc.poll() if self._proc is not None else None

    def error_text(self) -> str:
        return "".join(self._err).strip()
