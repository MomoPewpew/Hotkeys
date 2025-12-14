from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class WindowInfo:
    window_id: str
    title: str
    wm_class: str


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _parse_xprop_value(output: str, key: str) -> Optional[str]:
    for line in output.splitlines():
        if not line.startswith(key):
            continue
        if "=" not in line:
            continue
        _, value = line.split("=", 1)
        return value.strip().strip('"')
    return None


def get_active_window() -> Optional[WindowInfo]:
    # Get active window id
    active_raw = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    match = re.search(r"window id # (0x[0-9a-fA-F]+)", active_raw)
    if not match:
        return None
    win_id = match.group(1)

    # Get window properties
    props = _run(["xprop", "-id", win_id, "WM_CLASS", "WM_NAME"])
    if not props:
        return None
    wm_class_raw = _parse_xprop_value(props, "WM_CLASS")
    title = _parse_xprop_value(props, "WM_NAME") or ""
    wm_class = wm_class_raw or ""
    return WindowInfo(window_id=win_id, title=title, wm_class=wm_class)

