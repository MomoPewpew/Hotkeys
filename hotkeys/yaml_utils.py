from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def yaml_dump(data: Dict[str, Any]) -> str:
    # Readable dump, but does not preserve comments/formatting.
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def update_yaml_scalar_in_place(path: Path, key: str, value: str) -> None:
    """
    Update a top-level `key: value` scalar in-place, preserving the rest of the file
    formatting/comments as much as possible.

    If the key does not exist, it will be inserted near macro_profile_cycle_hotkey if present,
    otherwise after the initial header block.
    """
    text = path.read_text(encoding="utf-8")
    # Match: beginning of line, optional spaces, key, colon, optional space, then value.
    pattern = re.compile(rf"^(?P<indent>\s*){re.escape(key)}\s*:\s*(?P<val>.*)$", re.MULTILINE)
    m = pattern.search(text)
    if m:
        indent = m.group("indent") or ""
        replacement = f"{indent}{key}: {value}"
        new_lines = []
        replaced = False
        for line in text.splitlines(keepends=True):
            if pattern.match(line):
                if not replaced:
                    new_lines.append(replacement + ("\n" if line.endswith("\n") else ""))
                    replaced = True
                else:
                    # drop duplicate key lines
                    continue
            else:
                new_lines.append(line)
        path.write_text("".join(new_lines), encoding="utf-8")
        return

    # Insert key if missing
    lines = text.splitlines(keepends=True)
    # If we somehow have duplicates but regex didn't match (formatting edge cases),
    # remove all existing instances before inserting a clean one.
    cleaned = []
    for line in lines:
        if line.lstrip().startswith(f"{key}:"):
            continue
        cleaned.append(line)
    lines = cleaned
    insert_at: Optional[int] = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("macro_profile_cycle_hotkey"):
            insert_at = i + 1
            break
    if insert_at is None:
        # after first non-comment/non-empty block (usually name/match)
        for i, line in enumerate(lines):
            if line.strip() == "" or line.lstrip().startswith("#"):
                continue
            # insert after this line
            insert_at = i + 1
            break
    if insert_at is None:
        insert_at = len(lines)
    lines.insert(insert_at, f"{key}: {value}\n")
    path.write_text("".join(lines), encoding="utf-8")

