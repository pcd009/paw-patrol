"""Load config.yaml once. Every adapter/service should read settings from here
instead of hardcoding thresholds, so swapping to real hardware = editing one file."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


@functools.lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get(*path: str, default: Any = None) -> Any:
    """get('thresholds', 'gait', 'stationary_max')"""
    node: Any = load_config()
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
