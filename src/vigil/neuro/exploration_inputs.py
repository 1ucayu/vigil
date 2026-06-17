"""Shared metadata for values synthesized by the exploration harness."""

from __future__ import annotations

EXPLORATION_SYNTHETIC_INPUT_VALUES: tuple[str, ...] = ("test123",)


def is_exploration_synthetic_input(value: object) -> bool:
    """Return true when ``value`` is a generic explorer placeholder, not task intent."""
    return isinstance(value, str) and value.strip() in EXPLORATION_SYNTHETIC_INPUT_VALUES
