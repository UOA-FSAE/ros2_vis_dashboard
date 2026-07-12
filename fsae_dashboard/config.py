"""Configuration persistence: connection profiles + dashboard layouts.

A dashboard config is a plain dict serialised to YAML so it is diffable and
hand-editable. It contains:

    version:      schema version
    connection:   last-used connection settings (see transport / ssh modules)
    panels:       list of {id, type, title, config} — the "custom windows"
    layout_state: base64 QMainWindow.saveState() blob (exact dock geometry)
    geometry:     base64 QMainWindow.saveGeometry() blob (window size/pos)

Panels are described logically (type + config) *and* positionally (the Qt
state blob). On load we recreate the panels first, then restore the blob so Qt
snaps them back into place.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

APP_NAME = "fsae-dashboard"
CONFIG_VERSION = 1


def config_dir() -> Path:
    d = Path(user_config_dir(APP_NAME, appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def profiles_path() -> Path:
    """Where connection profiles live (separate from dashboard layouts)."""
    return config_dir() / "profiles.yaml"


def default_layout_path() -> Path:
    return config_dir() / "layout.yaml"


def empty_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "connection": {},
        "panels": [],
        "layout_state": "",
        "geometry": "",
    }


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cfg = empty_config()
    cfg.update(data)
    return cfg


def save_config(path: str | Path, cfg: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False, default_flow_style=False)


def encode_blob(data: bytes | bytearray | memoryview) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def decode_blob(text: str) -> bytes:
    if not text:
        return b""
    return base64.b64decode(text.encode("ascii"))


# --- connection profiles (named, reusable) ---------------------------------

def load_profiles() -> dict[str, dict[str, Any]]:
    p = profiles_path()
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_profiles(profiles: dict[str, dict[str, Any]]) -> None:
    p = profiles_path()
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(profiles, fh, sort_keys=True)
