"""Tests for the opt-in, offline-only legacy audit migration utility.

The migration repairs only known *structural* schema-alias drift and records every change in
``normalization_warnings``. It invents nothing and is never part of active generation.
"""

from __future__ import annotations

import json
from pathlib import Path

from vigil.models.guard import GuardKind
from vigil.neuro.legacy_audit_migration import (
    migrate_legacy_guard_audit,
    migrate_legacy_invariant_audit,
    normalize_contract_payload,
    parse_legacy_json,
)


def test_parse_legacy_json_handles_fences() -> None:
    assert parse_legacy_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_legacy_json('prose {"a": 2} trailer') == {"a": 2}
    assert parse_legacy_json("not json") is None


def test_normalize_renames_slot_and_type_aliases() -> None:
    warnings: list[str] = []
    out = normalize_contract_payload(
        {"required_slots": [{"slot": "amount", "type": "number"}]}, warnings
    )
    assert out["required_slots"][0]["name"] == "amount"
    assert out["required_slots"][0]["slot_type"] == "number"
    assert any("slot" in w for w in warnings)
    assert any("type" in w for w in warnings)


def test_normalize_wraps_string_binding_requirements() -> None:
    warnings: list[str] = []
    out = normalize_contract_payload({"binding_requirements": ["selected_payee"]}, warnings)
    assert out["binding_requirements"][0] == {"name": "selected_payee"}
    assert any("string" in w for w in warnings)


def test_migrate_legacy_guard_audit_repairs_precondition_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "precondition": {
                    "kind": "item_binding",
                    "required_slots": [{"slot": "contact", "type": "string"}],
                    "binding_requirements": ["selected_row"],
                    "predicates": [],
                },
                "raw_responses": ["{...}"],
            }
        ),
        encoding="utf-8",
    )
    candidate = migrate_legacy_guard_audit(path)
    assert candidate.parsed_ok is True
    assert candidate.contract.kind is GuardKind.ITEM_BINDING
    assert candidate.contract.required_slots[0].name == "contact"
    assert candidate.contract.binding_requirements[0].name == "selected_row"
    assert candidate.schema_constraint_mode == "legacy_audit_migration"
    # Every structural change is recorded.
    assert any("precondition" in w for w in candidate.normalization_warnings)
    assert any("slot" in w for w in candidate.normalization_warnings)


def test_migrate_invents_nothing_for_unrepairable_payload(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    # 'kind' is not a valid enum value and there is nothing structural to repair.
    path.write_text(json.dumps({"contract": {"kind": "totally_made_up_kind"}}), encoding="utf-8")
    candidate = migrate_legacy_guard_audit(path)
    assert candidate.parsed_ok is False
    assert "validation failed" in candidate.rejection_reason


def test_migrate_legacy_invariant_audit(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    path.write_text(
        json.dumps(
            {
                "packet": {
                    "state_invariant_candidates": [
                        {
                            "expr": 'read(t, text) == "X"',
                            "admission_target": "runtime_state_invariant",
                        }
                    ],
                    "transition_guard_candidates": [
                        {
                            "source_state_id": "A",
                            "target_state_id": "B",
                            "contract": {
                                "kind": "navigation",
                                "required_slots": [{"slot": "v", "type": "string"}],
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    candidate = migrate_legacy_invariant_audit(path)
    assert candidate.parsed_ok is True
    assert candidate.packet.state_invariant_candidates[0].expr == 'read(t, text) == "X"'
    tgc = candidate.packet.transition_guard_candidates[0]
    assert tgc.contract.required_slots[0].name == "v"
    assert any("slot" in w for w in candidate.normalization_warnings)
