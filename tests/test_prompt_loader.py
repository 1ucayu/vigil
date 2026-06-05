"""Tests for the system-prompt loader."""

from __future__ import annotations

import pytest

from vigil.system_prompt import PROMPT_DIR, load_system_prompt


def test_loads_guard_contract_generation_prompt():
    text = load_system_prompt("guard_contract_generation.md")
    assert isinstance(text, str)
    assert text.strip()
    # Encodes the contract-first, Hoare-framed design.
    assert "GuardContract" in text or "guard contract" in text.lower()
    assert "$intent" in text
    assert "$bind" in text


def test_prompt_dir_points_at_package():
    assert PROMPT_DIR.name == "system_prompt"
    assert (PROMPT_DIR / "guard_contract_generation.md").is_file()


def test_missing_prompt_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_system_prompt("does_not_exist.md")


@pytest.mark.parametrize("bad", ["", ".", "..", "sub/dir.md", "a\\b.md"])
def test_invalid_prompt_name_rejected(bad):
    with pytest.raises(ValueError):
        load_system_prompt(bad)
