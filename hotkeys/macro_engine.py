from __future__ import annotations

import threading
import time
from typing import Dict, Iterable, Optional

from pynput import keyboard, mouse

from .config_loader import MacroAction, MacroDefinition, Profile
from .window_manager import WindowInfo
from .yaml_utils import update_yaml_scalar_in_place


SPECIAL_KEY_CANONICAL = {
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "control": "ctrl",
    "alt_l": "alt",
    "alt_r": "alt",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "cmd",
    "win": "cmd",
    "super": "cmd",
    "numlock": "num_lock",
    # On some layouts, pynput reports numpad digits as plain '1'..'9'.
    # Canonicalize numpad digits to the digit itself so combos like alt+num_pad1 still match alt+1.
    "numpad0": "0",
    "numpad1": "1",
    "numpad2": "2",
    "numpad3": "3",
    "numpad4": "4",
    "numpad5": "5",
    "numpad6": "6",
    "numpad7": "7",
    "numpad8": "8",
    "numpad9": "9",
    "num_pad0": "0",
    "num_pad1": "1",
    "num_pad2": "2",
    "num_pad3": "3",
    "num_pad4": "4",
    "num_pad5": "5",
    "num_pad6": "6",
    "num_pad7": "7",
    "num_pad8": "8",
    "num_pad9": "9",
}


def _canonical_key_name(name: str) -> str:
    name = name.lower()
    return SPECIAL_KEY_CANONICAL.get(name, name)


def key_to_name(key: keyboard.Key | keyboard.KeyCode) -> str:
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char.lower()
        if key.vk is not None:
            return f"vk_{key.vk}"
    if isinstance(key, keyboard.Key):
        if key.name:
            return key.name.lower()
    return str(key)


def _matches_trigger(config_key: str, event_key: keyboard.Key | keyboard.KeyCode) -> bool:
    return _matches_trigger_name(config_key, key_to_name(event_key))


def _matches_trigger_name(config_key: str, event_name: str) -> bool:
    event_name = _canonical_key_name(event_name)
    config_name = _canonical_key_name(config_key)
    if event_name == config_name:
        return True
    if event_name.startswith("f") and config_name == event_name:
        return True
    return False


def normalize_combo(combo: str) -> str:
    parts = [_canonical_key_name(p.strip()) for p in combo.lower().split("+") if p.strip()]
    if not parts:
        return ""
    # sort for stable matching (alt+num_pad0 == num_pad0+alt)
    return "+".join(sorted(parts))


def canonical_key_name(name: str) -> str:
    return _canonical_key_name(name)


class MacroEngine:
    def __init__(self, profiles: Iterable[Profile]) -> None:
        self._profiles = list(profiles)
        self._active_profile: Optional[Profile] = None
        # map loop key -> (stop_event, expected_profile)
        self._running_loops: Dict[str, tuple[threading.Event, Profile]] = {}
        # one-shot / in-progress action lists that should abort on unfocus or stop
        self._in_flight: Dict[int, tuple[threading.Event, Profile]] = {}
        # cycle state per profile+macro
        self._cycle_state: Dict[str, int] = {}
        self._lock = threading.Lock()
        self.keyboard_controller = keyboard.Controller()
        self.mouse_controller = mouse.Controller()

    @property
    def active_profile(self) -> Optional[Profile]:
        return self._active_profile

    def set_profiles(self, profiles: Iterable[Profile]) -> None:
        """Replace loaded profiles (used by live config reload)."""
        with self._lock:
            old_profiles = list(self._profiles)
            self._profiles = list(profiles)
            self._cycle_state.clear()
        for profile in old_profiles:
            self._abort_profile(profile)

    def update_active_window(self, window: Optional[WindowInfo]) -> None:
        match = self._find_matching_profile(window) if window else None
        old: Optional[Profile] = None
        with self._lock:
            if match != self._active_profile:
                old = self._active_profile
                self._active_profile = match
                if match:
                    print(f"[profile] Active profile: {match.name}")
                else:
                    print("[profile] No matching profile")
        if old:
            self._abort_profile(old)

    def _find_matching_profile(self, window: WindowInfo) -> Optional[Profile]:
        title = window.title.lower()
        wm_class = window.wm_class.lower()
        for profile in self._profiles:
            m = profile.match
            title_ok = not m.title_contains or any(token.lower() in title for token in m.title_contains)
            class_ok = not m.wm_class_contains or any(token.lower() in wm_class for token in m.wm_class_contains)
            if title_ok and class_ok:
                return profile
        return None

    def handle_key_event(self, key: keyboard.Key | keyboard.KeyCode, pressed: bool) -> None:
        profile = self._active_profile
        if not profile:
            return
        for macro in profile.effective_macros():
            if _matches_trigger(macro.trigger.key, key):
                if macro.trigger.behavior == "once" and pressed:
                    threading.Thread(target=self._run_actions, args=(macro, profile), daemon=True).start()
                elif macro.trigger.behavior == "toggle_loop" and pressed:
                    self._toggle_loop(macro, profile)
                elif macro.trigger.behavior == "hold_loop":
                    if pressed:
                        self._start_loop_if_needed(macro, profile)
                    else:
                        self._stop_loop(macro, profile)
                elif macro.trigger.behavior == "cycle" and pressed:
                    self._run_cycle(macro, profile)

    def handle_mouse_button(self, button_name: str, pressed: bool) -> None:
        """
        Handle mouse button events from pynput (button_name like 'left', 'right', 'middle', 'button13').
        """
        profile = self._active_profile
        if not profile:
            return
        for macro in profile.effective_macros():
            if _matches_trigger_name(macro.trigger.key, button_name):
                if macro.trigger.behavior == "once" and pressed:
                    threading.Thread(target=self._run_actions, args=(macro, profile), daemon=True).start()
                elif macro.trigger.behavior == "toggle_loop" and pressed:
                    self._toggle_loop(macro, profile)
                elif macro.trigger.behavior == "hold_loop":
                    if pressed:
                        self._start_loop_if_needed(macro, profile)
                    else:
                        self._stop_loop(macro, profile)
                elif macro.trigger.behavior == "cycle" and pressed:
                    self._run_cycle(macro, profile)

    def handle_scroll(self, dx: int, dy: int) -> None:
        """
        Handle scroll wheel events; maps to scroll_left/right/up/down trigger names.
        """
        profile = self._active_profile
        if not profile:
            return
        tokens = []
        if dx > 0:
            tokens.append("scroll_right")
        elif dx < 0:
            tokens.append("scroll_left")
        if dy > 0:
            tokens.append("scroll_up")
        elif dy < 0:
            tokens.append("scroll_down")
        if not tokens:
            return
        for macro in profile.effective_macros():
            for t in tokens:
                if _matches_trigger_name(macro.trigger.key, t):
                    if macro.trigger.behavior == "once":
                        threading.Thread(target=self._run_actions, args=(macro, profile), daemon=True).start()
                    elif macro.trigger.behavior == "toggle_loop":
                        self._toggle_loop(macro, profile)
                    elif macro.trigger.behavior == "hold_loop":
                        # scroll is momentary; treat as press
                        self._start_loop_if_needed(macro, profile)
                        self._stop_loop(macro, profile)

    def handle_evdev_key(self, code: int, name: str, pressed: bool) -> None:
        """
        Handle raw evdev key/button events.
        name is a lowercase string from evdev (e.g., btn_task) or fallback code_280.
        """
        profile = self._active_profile
        if not profile:
            return
        for macro in profile.effective_macros():
            if _matches_trigger_name(macro.trigger.key, name) or _matches_trigger_name(
                macro.trigger.key, f"code_{code}"
            ):
                if macro.trigger.behavior == "once" and pressed:
                    threading.Thread(target=self._run_actions, args=(macro, profile), daemon=True).start()
                elif macro.trigger.behavior == "toggle_loop" and pressed:
                    self._toggle_loop(macro, profile)
                elif macro.trigger.behavior == "hold_loop":
                    if pressed:
                        self._start_loop_if_needed(macro, profile)
                    else:
                        self._stop_loop(macro, profile)
                elif macro.trigger.behavior == "cycle" and pressed:
                    self._run_cycle(macro, profile)

    def handle_key_combo(self, combo: str) -> None:
        """
        Handle a chord/combo string like 'alt+num_pad0'. Intended for macro profile cycling.
        """
        profile = self._active_profile
        if not profile or not profile.macro_profile_cycle_hotkey:
            return
        if normalize_combo(profile.macro_profile_cycle_hotkey) != normalize_combo(combo):
            return
        self.cycle_macro_profile(profile)

    def cycle_macro_profile(self, profile: Profile) -> None:
        if not profile.macro_profile_order:
            return
        current = profile.selected_macro_profile
        if current not in profile.macro_profile_order:
            next_name = profile.macro_profile_order[0]
        else:
            idx = profile.macro_profile_order.index(current)
            next_name = profile.macro_profile_order[(idx + 1) % len(profile.macro_profile_order)]
        profile.selected_macro_profile = next_name
        self._persist_selected_macro_profile(profile)
        print(f"[macro-profile] {profile.name}: {next_name}")

    def _persist_selected_macro_profile(self, profile: Profile) -> None:
        try:
            # Preserve the file's formatting by editing only the scalar line.
            update_yaml_scalar_in_place(
                path=profile.source_path,
                key="selected_macro_profile",
                value=profile.selected_macro_profile or "",
            )
        except Exception as exc:  # pragma: no cover
            print(f"[macro-profile] persist failed: {exc}")

    def _loop_key(self, profile: Profile, macro: MacroDefinition) -> str:
        return f"{id(profile)}:{macro.name}"

    def _toggle_loop(self, macro: MacroDefinition, profile: Profile) -> None:
        key = self._loop_key(profile, macro)
        with self._lock:
            running = key in self._running_loops
        if running:
            self._stop_loop(macro, profile)
        else:
            self._start_loop_if_needed(macro, profile)

    def _abort_profile(self, profile: Profile) -> None:
        """Immediately stop loops and in-flight action lists for this game profile."""
        to_stop: list[tuple[str, threading.Event]] = []
        with self._lock:
            for key, (stop_event, running_profile) in list(self._running_loops.items()):
                if running_profile is profile or running_profile.name == profile.name:
                    to_stop.append((key, stop_event))
            for stop_event, running_profile in list(self._in_flight.values()):
                if running_profile is profile or running_profile.name == profile.name:
                    stop_event.set()
        for key, stop_event in to_stop:
            with self._lock:
                self._running_loops.pop(key, None)
            stop_event.set()
            print(f"[macro] stop loop (unfocus): {key.split(':', 1)[-1]}")

    def _start_loop_if_needed(self, macro: MacroDefinition, profile: Profile) -> None:
        key = self._loop_key(profile, macro)
        with self._lock:
            if key in self._running_loops:
                return
            stop_event = threading.Event()
            self._running_loops[key] = (stop_event, profile)
        print(f"[macro] start loop: {macro.name}")
        threading.Thread(target=self._loop_runner, args=(macro, profile, stop_event), daemon=True).start()

    def _stop_loop(self, macro: MacroDefinition, profile: Profile) -> None:
        key = self._loop_key(profile, macro)
        with self._lock:
            entry = self._running_loops.pop(key, None)
            stop_event = entry[0] if entry else None
        if stop_event:
            stop_event.set()
            print(f"[macro] stop loop: {macro.name}")

    def _loop_runner(self, macro: MacroDefinition, profile: Profile, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if not self._is_profile_active(profile):
                break
            self._run_action_list(macro, profile, macro.actions, stop_event)
        time.sleep(0.01)

    def _is_profile_active(self, expected: Profile) -> bool:
        return self._active_profile is expected

    def _should_abort(self, expected_profile: Profile, stop_event: Optional[threading.Event]) -> bool:
        if stop_event is not None and stop_event.is_set():
            return True
        return not self._is_profile_active(expected_profile)

    def _wait(self, seconds: float, expected_profile: Profile, stop_event: Optional[threading.Event]) -> bool:
        """Sleep up to `seconds`. Returns False if aborted."""
        if seconds <= 0:
            return not self._should_abort(expected_profile, stop_event)
        deadline = time.monotonic() + seconds
        while True:
            if self._should_abort(expected_profile, stop_event):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return not self._should_abort(expected_profile, stop_event)
            slice_s = min(0.02, remaining)
            if stop_event is not None:
                if stop_event.wait(slice_s):
                    return False
            else:
                time.sleep(slice_s)

    def _run_cycle(self, macro: MacroDefinition, profile: Profile) -> None:
        if not macro.actions_cycle:
            return
        key = self._loop_key(profile, macro)
        with self._lock:
            idx = self._cycle_state.get(key, 0)
            next_idx = (idx + 1) % len(macro.actions_cycle)
            self._cycle_state[key] = next_idx
        actions = macro.actions_cycle[idx]
        self._run_actions(macro, profile, actions)

    def _run_actions(
        self,
        macro: MacroDefinition,
        expected_profile: Profile,
        actions: Optional[list[MacroAction]] = None,
    ) -> None:
        stop_event = threading.Event()
        token = id(stop_event)
        with self._lock:
            self._in_flight[token] = (stop_event, expected_profile)
        try:
            self._run_action_list(macro, expected_profile, actions or macro.actions, stop_event)
        finally:
            with self._lock:
                self._in_flight.pop(token, None)

    def _run_action_list(
        self,
        macro: MacroDefinition,
        expected_profile: Profile,
        actions: list[MacroAction],
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        if self._should_abort(expected_profile, stop_event):
            return
        print(f"[macro] run: {macro.name}")
        held_keys: set[str] = set()
        held_buttons: set[str] = set()
        aborted = False
        for action in actions:
            if self._should_abort(expected_profile, stop_event):
                aborted = True
                break
            if action.type == "delay":
                if not self._wait((action.delay_ms or 0) / 1000.0, expected_profile, stop_event):
                    aborted = True
                    break
            elif action.type == "tap_key":
                if action.key:
                    self._tap_key(action.key)
            elif action.type == "key_down":
                if action.key:
                    self._key_down(action.key)
                    held_keys.add(action.key)
            elif action.type == "key_up":
                if action.key:
                    self._key_up(action.key)
                    held_keys.discard(action.key)
            elif action.type == "tap_mouse":
                if action.button:
                    self._tap_mouse(action.button)
            elif action.type == "mouse_down":
                if action.button:
                    self._mouse_down(action.button)
                    held_buttons.add(action.button)
            elif action.type == "mouse_up":
                if action.button:
                    self._mouse_up(action.button)
                    held_buttons.discard(action.button)
        if aborted:
            print(f"[macro] abort: {macro.name}")
            for key_name in list(held_keys):
                self._key_up(key_name)
            for button_name in list(held_buttons):
                self._mouse_up(button_name)

    def _tap_key(self, key_name: str) -> None:
        key_obj = _keyname_to_keycode(key_name)
        if key_obj is None:
            print(f"[warn] unknown key: {key_name}")
            return
        self.keyboard_controller.press(key_obj)
        self.keyboard_controller.release(key_obj)

    def _tap_mouse(self, button_name: str) -> None:
        button = {
            "left": mouse.Button.left,
            "right": mouse.Button.right,
            "middle": mouse.Button.middle,
        }.get(button_name.lower())
        if not button:
            print(f"[warn] unknown mouse button: {button_name}")
            return
        self.mouse_controller.click(button)

    def _mouse_down(self, button_name: str) -> None:
        button = {
            "left": mouse.Button.left,
            "right": mouse.Button.right,
            "middle": mouse.Button.middle,
        }.get(button_name.lower())
        if not button:
            print(f"[warn] unknown mouse button: {button_name}")
            return
        self.mouse_controller.press(button)

    def _mouse_up(self, button_name: str) -> None:
        button = {
            "left": mouse.Button.left,
            "right": mouse.Button.right,
            "middle": mouse.Button.middle,
        }.get(button_name.lower())
        if not button:
            print(f"[warn] unknown mouse button: {button_name}")
            return
        self.mouse_controller.release(button)

    def _key_down(self, key_name: str) -> None:
        key_obj = _keyname_to_keycode(key_name)
        if key_obj is None:
            print(f"[warn] unknown key: {key_name}")
            return
        self.keyboard_controller.press(key_obj)

    def _key_up(self, key_name: str) -> None:
        key_obj = _keyname_to_keycode(key_name)
        if key_obj is None:
            print(f"[warn] unknown key: {key_name}")
            return
        self.keyboard_controller.release(key_obj)


def _keyname_to_keycode(name: str) -> Optional[keyboard.Key | keyboard.KeyCode]:
    name = name.lower()
    special = {
        "esc": keyboard.Key.esc,
        "escape": keyboard.Key.esc,
        "space": keyboard.Key.space,
        "enter": keyboard.Key.enter,
        "tab": keyboard.Key.tab,
        "shift": keyboard.Key.shift,
        "ctrl": keyboard.Key.ctrl,
        "alt": keyboard.Key.alt,
        "cmd": keyboard.Key.cmd,
        "super": keyboard.Key.cmd,
        "win": keyboard.Key.cmd,
        "up": keyboard.Key.up,
        "down": keyboard.Key.down,
        "left": keyboard.Key.left,
        "right": keyboard.Key.right,
    }
    if name in special:
        return special[name]
    if name.startswith("f"):
        try:
            num = int(name[1:])
            fkey = getattr(keyboard.Key, f"f{num}", None)
            if fkey:
                return fkey
        except ValueError:
            pass
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    return None

