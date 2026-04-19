from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path

from pynput import keyboard, mouse

from .config_loader import load_profiles
from .macro_engine import MacroEngine, key_to_name
from .window_manager import get_active_window
from .evdev_listener import start_evdev_listener


def _start_focus_monitor(engine: MacroEngine, stop_event: threading.Event) -> None:
    def _run() -> None:
        last_print = 0.0
        while not stop_event.is_set():
            win = get_active_window()
            now = time.time()
            if win and (now - last_print) >= 5.0:
                print(f"[focus] {win.title} ({win.wm_class})")
                last_print = now
            engine.update_active_window(win)
            stop_event.wait(1.0)

    threading.Thread(target=_run, daemon=True).start()


def _start_config_watcher(
    engine: MacroEngine,
    config_dir: Path,
    stop_event: threading.Event,
) -> None:
    """
    Poll the config directory for changes every ~2s.
    Reload profiles and update active window on change.
    """

    def signature() -> tuple[int, int]:
        mtimes = [
            int(p.stat().st_mtime)
            for p in config_dir.glob("*.yml")
            if p.is_file()
        ]
        return (len(mtimes), sum(mtimes) if mtimes else 0)

    def _run() -> None:
        last_sig = signature()
        while not stop_event.is_set():
            stop_event.wait(2.0)
            cur_sig = signature()
            if cur_sig != last_sig:
                last_sig = cur_sig
                try:
                    profiles = load_profiles(config_dir)
                    engine.set_profiles(profiles)
                    # re-evaluate active window with new profiles
                    engine.update_active_window(get_active_window())
                    print(f"[config] Reloaded {len(profiles)} profiles")
                except Exception as exc:  # pragma: no cover - runtime safety
                    print(f"[config] reload failed: {exc}")

    threading.Thread(target=_run, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Window-aware macro daemon.")
    parser.add_argument(
        "--config-dir",
        default="configs",
        help="Directory containing *.yml profiles.",
    )
    parser.add_argument(
        "--evdev-devices",
        default="",
        help="Comma-separated /dev/input/event* paths to listen for extra buttons (e.g., /dev/input/event6,/dev/input/event7).",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    profiles = load_profiles(config_dir)
    print(f"[init] Loaded {len(profiles)} profiles from {config_dir}")

    engine = MacroEngine(profiles)
    stop_event = threading.Event()
    _start_focus_monitor(engine, stop_event)
    _start_config_watcher(engine, config_dir, stop_event)

    pressed_keys: set[str] = set()

    # Start evdev listeners if provided
    evdev_paths = [p.strip() for p in args.evdev_devices.split(",") if p.strip()]
    evdev_threads = []
    for path in evdev_paths:
        evdev_threads.append(
            start_evdev_listener(
                device_path=path,
                on_key=lambda code, name, pressed: engine.handle_evdev_key(code, name, pressed),
                stop_event=stop_event,
            )
        )

    def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
        name = key_to_name(key)
        print(f"[input] key down: {name}")
        pressed_keys.add(name)
        # combo check (for macro profile cycling)
        active = engine.active_profile
        if active and active.macro_profile_cycle_hotkey and "+" in active.macro_profile_cycle_hotkey:
            combo = "+".join(sorted(pressed_keys))
            engine.handle_key_combo(combo)
        engine.handle_key_event(key, pressed=True)

    def on_release(key: keyboard.Key | keyboard.KeyCode) -> None:
        name = key_to_name(key)
        print(f"[input] key up: {name}")
        pressed_keys.discard(name)
        engine.handle_key_event(key, pressed=False)

    def on_click(x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        state = "down" if pressed else "up"
        print(f"[input] mouse {button.name} {state} at ({x},{y})")
        engine.handle_mouse_button(button.name, pressed)

    def on_scroll(x: int, y: int, dx: int, dy: int) -> None:
        print(f"[input] scroll dx={dx} dy={dy} at ({x},{y})")
        engine.handle_scroll(dx, dy)

    kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)

    def _shutdown(*_: object) -> None:
        stop_event.set()
        kb_listener.stop()
        mouse_listener.stop()
        print("[init] stopping...")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    kb_listener.start()
    mouse_listener.start()
    print("[init] listeners started. Press Ctrl+C to exit.")
    kb_listener.join()
    mouse_listener.join()
    for t in evdev_threads:
        t.join(timeout=0.1)


if __name__ == "__main__":
    main()

