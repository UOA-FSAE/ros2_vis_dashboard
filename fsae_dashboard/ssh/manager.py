"""SSH control plane: tunnel the bridge port and (optionally) launch it remotely.

asyncssh needs an asyncio loop, so we run one on a dedicated daemon thread and
marshal coroutines onto it with run_coroutine_threadsafe. The data plane then
connects to a forwarded localhost port, which sidesteps DDS discovery / Wi-Fi
multicast entirely and encrypts everything over the SSH channel.

The whole module is optional: if asyncssh isn't installed, SSHManager.available
is False and the app falls back to a direct connection.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Callable

try:
    import asyncssh
except ImportError:  # pragma: no cover - optional dependency
    asyncssh = None


@dataclass
class SSHConfig:
    host: str = ""
    port: int = 22
    username: str = ""
    password: str | None = None
    key_path: str | None = None
    # local:remote port forwards, e.g. {9090: 9090, 8765: 8765}
    forwards: dict[int, int] = field(default_factory=lambda: {9090: 9090})
    # command run before the launch command (e.g. source the workspace)
    setup_command: str = "source /opt/ros/humble/setup.bash"


class SSHManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._conn = None
        self._listeners: list = []

    @property
    def available(self) -> bool:
        return asyncssh is not None

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # --- event loop plumbing ----------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, name="ssh-loop", daemon=True
            )
            self._thread.start()
        return self._loop

    def _run(self, coro, timeout: float = 30.0):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    # --- public API --------------------------------------------------------
    def connect(self, cfg: SSHConfig) -> None:
        if not self.available:
            raise RuntimeError("asyncssh is not installed. Run: pip install asyncssh")
        self._run(self._connect(cfg))

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._run(self._disconnect(), timeout=10)
            except Exception:  # noqa: BLE001
                pass

    def run_command_streaming(self, command: str, on_line: Callable[[str], None]) -> None:
        """Run a long-lived remote command, streaming stdout/stderr line by line."""
        self._run(self._stream(command, on_line), timeout=10)

    # --- coroutines --------------------------------------------------------
    async def _connect(self, cfg: SSHConfig) -> None:
        opts: dict = {
            "host": cfg.host,
            "port": cfg.port,
            "username": cfg.username,
            "known_hosts": None,  # convenient at the track; harden for production
        }
        if cfg.key_path:
            opts["client_keys"] = [cfg.key_path]
        if cfg.password:
            opts["password"] = cfg.password
        self._conn = await asyncssh.connect(**opts)
        for local_port, remote_port in cfg.forwards.items():
            listener = await self._conn.forward_local_port(
                "127.0.0.1", local_port, "127.0.0.1", remote_port
            )
            self._listeners.append(listener)

    async def _disconnect(self) -> None:
        for listener in self._listeners:
            try:
                listener.close()
            except Exception:  # noqa: BLE001
                pass
        self._listeners.clear()
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None

    async def _stream(self, command: str, on_line: Callable[[str], None]) -> None:
        assert self._conn is not None
        proc = await self._conn.create_process(command, term_type="xterm")

        async def pump(stream):
            async for line in stream:
                on_line(line.rstrip("\n"))

        asyncio.ensure_future(pump(proc.stdout))
        asyncio.ensure_future(pump(proc.stderr))
