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


def prefs_path() -> Path:
    """Small global preferences, distinct from per-layout dashboard configs."""
    return config_dir() / "prefs.yaml"


def load_prefs() -> dict[str, Any]:
    p = prefs_path()
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_prefs(prefs: dict[str, Any]) -> None:
    with prefs_path().open("w", encoding="utf-8") as fh:
        yaml.safe_dump(prefs, fh, sort_keys=True)


def default_bags_dir() -> str:
    """Where rosbags are recorded to / replayed from. User-chosen, remembered."""
    return load_prefs().get("bags_dir") or str(Path.home() / "fsae_bags")


def set_bags_dir(path: str) -> None:
    prefs = load_prefs()
    prefs["bags_dir"] = path
    save_prefs(prefs)


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


# --- layout profiles (named, selectable dashboard layouts) -----------------

def _repo_root() -> Path:
    """Repo root, where the shipped example layouts (general_layout.yaml) live."""
    return Path(__file__).resolve().parent.parent


# Built-in layout profiles shipped with the repo (name -> yaml path). Offered in
# the Layout > Profiles selector whenever the backing file is present.
BUILTIN_LAYOUT_PROFILES: dict[str, Path] = {
    "Tommylaptop": _repo_root() / "general_layout.yaml",
}


def layouts_dir() -> Path:
    """User-saved layout profiles live here, one YAML per profile."""
    d = config_dir() / "layouts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_layout_profiles() -> dict[str, Path]:
    """Ordered name -> path for every selectable layout profile.

    Built-in (repo-shipped) profiles come first, then user-saved profiles
    discovered in ``layouts_dir()`` by filename stem. A built-in name takes
    precedence, so it stays stable even if a user file happens to share its name.
    """
    profiles: dict[str, Path] = {}
    for name, path in BUILTIN_LAYOUT_PROFILES.items():
        if path.exists():
            profiles[name] = path
    for p in sorted(layouts_dir().glob("*.yaml")):
        profiles.setdefault(p.stem, p)
    return profiles


def save_layout_profile(name: str, cfg: dict[str, Any]) -> Path:
    """Persist ``cfg`` as a user layout profile named ``name``; return its path."""
    path = layouts_dir() / f"{name}.yaml"
    save_config(path, cfg)
    return path


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
