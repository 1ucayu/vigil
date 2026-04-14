"""Widget-type guard template table for DSL generation.

Maps Android widget class names to suggested safety and correctness guard
templates. Used by DslGenerator to provide structured hints to the LLM,
reducing open-ended generation failures and in_state() fallbacks.
"""

from __future__ import annotations

WIDGET_GUARD_TEMPLATES: dict[str, dict[str, str | None]] = {
    "Switch": {
        "safety": "read({alias}, is_enabled) == true",
        "correctness": "read({alias}, is_checked) == $intent.{var}",
    },
    "ToggleButton": {
        "safety": "read({alias}, is_enabled) == true",
        "correctness": "read({alias}, is_checked) == $intent.{var}",
    },
    "EditText": {
        "safety": "read({alias}, is_editable) == true",
        "correctness": "value({alias}) == $intent.{var}",
    },
    "AutoCompleteTextView": {
        "safety": "read({alias}, is_editable) == true",
        "correctness": "value({alias}) == $intent.{var}",
    },
    "RecyclerView": {
        "safety": "count({alias}) >= 1",
        "correctness": "action(target_text) == $intent.{var}",
    },
    "ListView": {
        "safety": "count({alias}) >= 1",
        "correctness": "action(target_text) == $intent.{var}",
    },
    "Button": {
        "safety": "read({alias}, is_clickable) == true",
        "correctness": "action(target_text) == $intent.{var}",
    },
    "ImageButton": {
        "safety": "read({alias}, is_clickable) == true",
        "correctness": None,
    },
    "FloatingActionButton": {
        "safety": "read({alias}, is_clickable) == true",
        "correctness": None,
    },
    "TimePicker": {
        "safety": None,
        "correctness": "value({alias}) == $intent.{var}",
    },
    "DatePicker": {
        "safety": None,
        "correctness": "value({alias}) == $intent.{var}",
    },
    "Spinner": {
        "safety": "count({alias}) >= 1",
        "correctness": "action(target_text) == $intent.{var}",
    },
    "CheckBox": {
        "safety": "read({alias}, is_enabled) == true",
        "correctness": "read({alias}, is_checked) == $intent.{var}",
    },
    "RadioButton": {
        "safety": "read({alias}, is_enabled) == true",
        "correctness": "read({alias}, is_checked) == $intent.{var}",
    },
    "SeekBar": {
        "safety": "read({alias}, is_enabled) == true",
        "correctness": "value({alias}) == $intent.{var}",
    },
    "TabLayout": {
        "safety": None,
        "correctness": "action(target_text) == $intent.{var}",
    },
    "BottomNavigationView": {
        "safety": None,
        "correctness": "action(target_text) == $intent.{var}",
    },
    "TextView": {
        "safety": None,
        "correctness": "action(target_text) == $intent.{var}",
    },
}


def get_guard_template(class_name: str) -> dict[str, str | None] | None:
    """Look up guard template by Android widget class name.

    Handles both short names ("Switch") and fully qualified names
    ("android.widget.Switch").
    """
    short_name = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name
    return WIDGET_GUARD_TEMPLATES.get(short_name)
