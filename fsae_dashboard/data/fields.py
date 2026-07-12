"""Message field introspection.

ROS messages arrive as nested dicts. To plot "the speed", the user picks a
dotted field path like ``drive.speed`` or ``linear_acceleration.x``. These
helpers enumerate the numeric leaves of a sample message (for the field picker)
and extract a value by path (for the render loop).

Path grammar:
    a.b.c      nested dict keys
    a.3.x      integer index into a list
    a.count    number of elements in a list (pseudo-field)
"""
from __future__ import annotations

from typing import Any, Iterator

_MAX_LIST_SAMPLE = 4  # how deep to index into lists when enumerating fields


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float, bool)) and not isinstance(v, str)


def flatten_numeric_fields(msg: Any, prefix: str = "") -> list[str]:
    """Return sorted dotted paths of numeric leaves in *msg*."""
    return sorted(set(_walk(msg, prefix)))


def _walk(node: Any, prefix: str) -> Iterator[str]:
    if _is_number(node):
        if prefix:
            yield prefix
        return
    if isinstance(node, dict):
        for key, val in node.items():
            child = f"{prefix}.{key}" if prefix else key
            yield from _walk(val, child)
    elif isinstance(node, list):
        if node:
            # expose length as a plottable pseudo-field
            yield f"{prefix}.count" if prefix else "count"
        for i, val in enumerate(node[:_MAX_LIST_SAMPLE]):
            child = f"{prefix}.{i}" if prefix else str(i)
            yield from _walk(val, child)


def get_field(msg: Any, path: str) -> float | None:
    """Extract a numeric value at *path*, or None if missing/non-numeric."""
    parts = path.split(".")
    cur: Any = msg
    for i, part in enumerate(parts):
        if part == "count" and isinstance(cur, list):
            return float(len(cur))
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    if _is_number(cur):
        return float(cur)
    return None
