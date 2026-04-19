from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class MacroAction:
    type: str
    key: Optional[str] = None
    button: Optional[str] = None
    delay_ms: Optional[int] = None


@dataclass
class MacroTrigger:
    key: str
    behavior: str  # once | toggle_loop | hold_loop


@dataclass
class MacroDefinition:
    name: str
    trigger: MacroTrigger
    actions: List[MacroAction]
    actions_cycle: Optional[List[List[MacroAction]]] = None


@dataclass
class ProfileMatch:
    wm_class_contains: List[str]
    title_contains: List[str]


@dataclass
class Profile:
    name: str
    match: ProfileMatch
    macros: List[MacroDefinition]
    macro_profiles: Dict[str, List[MacroDefinition]]
    macro_profile_cycle_hotkey: Optional[str]
    selected_macro_profile: Optional[str]
    macro_profile_order: List[str]
    source_path: Path
    _raw: Dict[str, Any]

    def effective_macros(self) -> List[MacroDefinition]:
        macros = list(self.macros)
        if self.selected_macro_profile:
            macros.extend(self.macro_profiles.get(self.selected_macro_profile, []))
        return macros


def _parse_action(raw: Dict[str, Any]) -> MacroAction:
    action_type = raw.get("type")
    if not action_type:
        raise ValueError("Action missing 'type'")
    if action_type == "delay":
        delay = int(raw.get("ms", 0))
        if delay < 0:
            raise ValueError("Delay must be non-negative")
        return MacroAction(type="delay", delay_ms=delay)
    if action_type == "tap_key":
        key = raw.get("key")
        if not key:
            raise ValueError("tap_key action missing 'key'")
        return MacroAction(type="tap_key", key=str(key))
    if action_type in {"key_down", "key_up"}:
        key = raw.get("key")
        if not key:
            raise ValueError(f"{action_type} action missing 'key'")
        return MacroAction(type=action_type, key=str(key))
    if action_type == "tap_mouse":
        button = raw.get("button")
        if button not in {"left", "right", "middle"}:
            raise ValueError("tap_mouse action requires button: left/right/middle")
        return MacroAction(type="tap_mouse", button=str(button))
    if action_type in {"mouse_down", "mouse_up"}:
        button = raw.get("button")
        if button not in {"left", "right", "middle"}:
            raise ValueError(f"{action_type} action requires button: left/right/middle")
        return MacroAction(type=action_type, button=str(button))
    raise ValueError(f"Unsupported action type: {action_type}")


def _parse_macro(raw: Dict[str, Any]) -> MacroDefinition:
    name = raw.get("name") or "unnamed"
    trigger_raw = raw.get("trigger") or {}
    trigger_key = trigger_raw.get("key")
    behavior = trigger_raw.get("behavior", "once")
    if not trigger_key:
        raise ValueError(f"Macro '{name}' missing trigger.key")
    if behavior not in {"once", "toggle_loop", "hold_loop", "cycle"}:
        raise ValueError(f"Macro '{name}' has invalid behavior '{behavior}'")
    actions_cycle: Optional[List[List[MacroAction]]] = None
    actions: List[MacroAction] = []
    if behavior == "cycle":
        # Accept actions_cycle: [ [..], [..] ] or actions-1/actions-2 keys
        if "actions_cycle" in raw:
            raw_list = raw.get("actions_cycle") or []
            actions_cycle = [[_parse_action(a) for a in group] for group in raw_list]
        else:
            grouped: List[tuple[int, List[MacroAction]]] = []
            for key, value in raw.items():
                if key.startswith("actions-"):
                    try:
                        idx = int(key.split("-", 1)[1])
                    except ValueError:
                        continue
                    grouped.append((idx, [_parse_action(a) for a in (value or [])]))
            grouped.sort(key=lambda t: t[0])
            if grouped:
                actions_cycle = [grp for _, grp in grouped]
        if not actions_cycle:
            raise ValueError(f"Macro '{name}' with behavior=cycle needs actions_cycle or actions-N")
    else:
        actions_raw = raw.get("actions") or []
        actions = [_parse_action(action) for action in actions_raw]
        if not actions:
            raise ValueError(f"Macro '{name}' must define at least one action")
    return MacroDefinition(
        name=name,
        trigger=MacroTrigger(key=str(trigger_key), behavior=behavior),
        actions=actions,
        actions_cycle=actions_cycle,
    )


def _parse_profile(data: Dict[str, Any], source_path: Path) -> Profile:
    name = data.get("name") or "unnamed profile"
    match_raw = data.get("match") or {}
    wm_class_contains = [str(v) for v in match_raw.get("wm_class_contains", [])]
    title_contains = [str(v) for v in match_raw.get("title_contains", [])]
    macros_raw = data.get("macros") or []
    macros = [_parse_macro(m) for m in macros_raw]
    macro_profiles: Dict[str, List[MacroDefinition]] = {}
    for key, value in data.items():
        if not key.startswith("macros_"):
            continue
        profile_name = key[len("macros_") :].strip()
        if not profile_name:
            continue
        macro_profiles[profile_name] = [_parse_macro(m) for m in (value or [])]

    macro_profile_order = [str(v) for v in (data.get("macro_profile_order") or [])]
    if not macro_profile_order:
        macro_profile_order = sorted(macro_profiles.keys())
    else:
        # keep only valid entries, append any missing
        macro_profile_order = [p for p in macro_profile_order if p in macro_profiles]
        for p in sorted(macro_profiles.keys()):
            if p not in macro_profile_order:
                macro_profile_order.append(p)

    cycle_hotkey = data.get("macro_profile_cycle_hotkey")
    selected = data.get("selected_macro_profile")
    if selected not in macro_profiles:
        selected = macro_profile_order[0] if macro_profile_order else None
    return Profile(
        name=name,
        match=ProfileMatch(
            wm_class_contains=wm_class_contains,
            title_contains=title_contains,
        ),
        macros=macros,
        macro_profiles=macro_profiles,
        macro_profile_cycle_hotkey=str(cycle_hotkey) if cycle_hotkey else None,
        selected_macro_profile=str(selected) if selected else None,
        macro_profile_order=macro_profile_order,
        source_path=source_path,
        _raw=dict(data),
    )


def load_profiles(config_dir: Path) -> List[Profile]:
    profiles: List[Profile] = []
    for path in sorted(Path(config_dir).glob("*.yml")):
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        profiles.append(_parse_profile(data, path))
    return profiles

