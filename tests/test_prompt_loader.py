"""Tests for the system-prompt loader."""

from __future__ import annotations

import pytest

from vigil.system_prompt import PROMPT_DIR, load_system_prompt


def test_loads_guard_generation_prompt():
    text = load_system_prompt("transition_guard_generation.spec")
    assert isinstance(text, str)
    assert text.strip()
    # Encodes the spec-style, Hoare-framed guard-generation design.
    assert "[PROMPT]" in text
    assert "[RELY]" in text
    assert "[GUARANTEE]" in text
    assert "[SPECIFICATION]" in text
    assert "[RELY]:\n  Defines the inputs" in text
    assert "[GUARANTEE]:\n  Defines the required output contract" in text
    assert "[SPECIFICATION]:\n  Defines the preconditions" in text
    assert "[SPECIFICATION of ...]:" in text
    assert "GuardContract" in text
    assert "precondition" in text
    assert "postcondition" in text
    assert "Psi" in text
    assert "effect_requirements" in text
    assert "audit-only" in text
    assert "appears" in text
    assert "compatibility alias" in text
    assert "$intent" in text
    assert "$bind" in text


def test_prompt_dir_points_at_package():
    assert PROMPT_DIR.name == "system_prompt"
    assert (PROMPT_DIR / "transition_guard_generation.spec").is_file()


def test_missing_prompt_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_system_prompt("does_not_exist.spec")


@pytest.mark.parametrize("bad", ["", ".", "..", "sub/dir.md", "a\\b.md"])
def test_invalid_prompt_name_rejected(bad):
    with pytest.raises(ValueError):
        load_system_prompt(bad)
