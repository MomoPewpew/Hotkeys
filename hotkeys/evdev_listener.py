from __future__ import annotations

import threading
from typing import Callable, Optional

from evdev import InputDevice, categorize, ecodes


def code_to_name(code: int) -> str:
    """
    Convert an evdev key code to a readable name.
    Falls back to code_{number} when unmapped.
    """
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code)
    if name:
        return name.lower()
    return f"code_{code}"


def start_evdev_listener(
    device_path: str,
    on_key: Callable[[int, str, bool], None],
    stop_event: threading.Event,
) -> threading.Thread:
    """
    Start a daemon thread that reads EV_KEY events from the given device and
    invokes on_key(code, name, pressed).
    """

    def _run() -> None:
        try:
            dev = InputDevice(device_path)
        except Exception as exc:  # pragma: no cover - peripheral error path
            print(f"[evdev] cannot open {device_path}: {exc}")
            return
        print(f"[evdev] listening on {device_path}")
        for event in dev.read_loop():
            if stop_event.is_set():
                break
            if event.type != ecodes.EV_KEY:
                continue
            key_event = categorize(event)
            name = code_to_name(key_event.scancode)
            pressed = key_event.keystate == key_event.key_down
            on_key(key_event.scancode, name, pressed)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread

