# config/__init__.py
# Phase 2 configuration loader

import json
import os
from typing import Dict, Any

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase2_config.json")

_DEFAULT_CONFIG = {
    "action_mode": "FLAG_ONLY",
    "min_samples": 100,
    "affect_trading": False,
    "accuracy_threshold_warn": 0.30,
    "accuracy_threshold_critical": 0.15,
    "flagging_interval_hours": 6,
}


def load_config() -> Dict[str, Any]:
    """Load Phase 2 config from JSON file, falling back to defaults."""
    try:
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"[CONFIG] Error loading config: {e}")
    
    return dict(_DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> bool:
    """Save Phase 2 config to JSON file."""
    try:
        with open(_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"[CONFIG] Error saving config: {e}")
        return False
