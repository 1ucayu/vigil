"""Android platform priors loaded from YAML config.

Provides widget guard templates, dialog indicators, tab indicators,
and error patterns. All values are Android SDK / AndroidX / Material
Design standard components — NOT app-specific.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "android_platform.yaml"


@lru_cache(maxsize=1)
def _load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load and cache the platform priors config."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    with open(path) as f:  # noqa: PTH123
        return yaml.safe_load(f)


def get_widget_templates() -> dict[str, dict[str, str | None]]:
    """Get widget class -> guard template mapping."""
    return _load_config().get("widget_templates", {})


def get_guard_template(class_name: str) -> dict[str, str | None] | None:
    """Look up guard template by Android widget class name.

    Handles both short names ("Switch") and fully qualified names
    ("android.widget.Switch").
    """
    short_name = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name
    return get_widget_templates().get(short_name)


def get_dialog_indicators() -> dict[str, list[str]]:
    """Get dialog detection indicators (classes + resource_ids)."""
    return _load_config().get("dialog_indicators", {"classes": [], "resource_ids": []})


def get_tab_indicators() -> list[str]:
    """Get tab navigation indicator class names."""
    indicators = _load_config().get("tab_indicators", {})
    return indicators.get("classes", [])


def get_error_patterns() -> list[str]:
    """Get error state name patterns for removal."""
    return _load_config().get("error_patterns", [])
