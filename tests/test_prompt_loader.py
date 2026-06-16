"""Tests for the system-prompt loader."""

from __future__ import annotations

import pytest

from vigil.system_prompt import PROMPT_DIR, load_system_prompt


def test_loads_guard_generation_prompt():
    text = load_system_prompt("transition_guard_generation.spec")
    assert isinstance(text, str)
    assert text.strip()
    # Encodes the transition-guard-only generation contract.
    assert "GuardContract" in text
    assert "pre-action guard" in text
    assert "Produce one `GuardContract` candidate" in text
    assert "Do not emit DSL text directly" in text
    assert "must not reference target-only UI" in text
    assert "binding_requirements" not in text
    assert "semantic-completeness" in text
    assert "$intent" in text
    assert "$bind" not in text
    for forbidden in ("post" + "condition", "P" + "si", "effect" + "_requirements"):
        assert forbidden not in text


def test_prompt_dir_points_at_package():
    assert PROMPT_DIR.name == "system_prompt"
    assert (PROMPT_DIR / "transition_guard_generation.spec").is_file()


def test_specs_are_policy_not_schema_authority():
    guard = load_system_prompt("transition_guard_generation.spec")
    invariant = load_system_prompt("invariant_guard_generation.spec")
    # Policy markers: the structured schema is the shape authority, not the spec.
    assert "structured-output schema" in guard
    assert "structured-output schema" in invariant
    # The old JSON answer-template blocks are gone.
    assert '"semantic_binding_incomplete": false,' not in guard
    assert '"admission_target": "runtime_state_invariant|metadata_only|reject"' not in invariant
    assert "effect_invariant_hints" not in invariant
    assert "rejected_candidates" not in invariant
    assert "precondition" not in guard.lower()


def test_missing_prompt_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_system_prompt("does_not_exist.spec")


@pytest.mark.parametrize("bad", ["", ".", "..", "sub/dir.md", "a\\b.md"])
def test_invalid_prompt_name_rejected(bad):
    with pytest.raises(ValueError):
        load_system_prompt(bad)
