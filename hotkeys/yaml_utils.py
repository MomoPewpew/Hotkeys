from __future__ import annotations

from typing import Any, Dict

import yaml


def yaml_dump(data: Dict[str, Any]) -> str:
    # Keep it readable and stable-ish. This won't preserve comments.
    return yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )

