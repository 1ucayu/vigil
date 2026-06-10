"""End-to-end tests for the contract-first invariant generation pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vigil.core.config import VerificationConfig
from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    HierarchyLevel,
    StateInvariant,
    Transition,
)
from vigil.models.guard import GuardAdmissionStatus
from vigil.neuro.invariant_generation_pipeline import generate_contract_invariants
from vigil.symbolic.decision_engine import DecisionEngine
from vigil.symbolic.fsm_checker import VerifyReason, VerifyResult

PKG = "com.vigil.clock"


def _state(state_id: str, screens: list[str]) -> AbstractState:
    return AbstractState(
        state_id=state_id,
        name=state_id,
        fingerprint=f"fp_{state_id}",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=screens,
    )


def _screen(screen_id: str, ms: int) -> dict[str, Any]:
    return {
        "screen_id": screen_id,
        "interactable_elements": [
            {
                "element_id": "e1",
                "resource_id": f"{PKG}:id/remaining_ms",
                "text": str(ms),
                "class_name": "android.widget.TextView",
            }
        ],
    }


def _clock_fsm() -> tuple[AppFSM, dict[str, Any]]:
    fsm = AppFSM(app_package=PKG)
    fsm.add_state(_state("timer_done", ["a", "b"]))
    fsm.add_state(_state("timer_running", ["r"]))
    fsm.initial_state = "timer_running"
    fsm.add_transition(
        Transition(
            source="timer_running",
            target="timer_done",
            action={"type": "click", "target": "e_x"},
            confidence=0.9,
        )
    )
    raw_screens = {"a": _screen("a", 0), "b": _screen("b", 0), "r": _screen("r", 5000)}
    return fsm, raw_screens


# ---------------------------------------------------------------------------
# Deterministic end-to-end + serialization
# ---------------------------------------------------------------------------


def test_deterministic_pipeline_attaches_invariants() -> None:
    fsm, raw_screens = _clock_fsm()
    report = generate_contract_invariants(fsm, raw_screens, invariant_source="deterministic")

    done = fsm.states["timer_done"]
    running = fsm.states["timer_running"]
    assert f"value({PKG}:id/remaining_ms) == 0" in [s.expr for s in done.invariant_specs]
    assert running.invariant_specs == []
    # multi-visit timer_done carries stronger evidence_count.
    done_spec = next(s for s in done.invariant_specs if s.expr.endswith("== 0"))
    assert done_spec.evidence_count == 2
    assert {row["state_id"] for row in report} == {"timer_done", "timer_running"}
    running_row = next(row for row in report if row["state_id"] == "timer_running")
    assert running_row["invariants_admitted"] == []
    assert running_row["effect_hints"][0]["why_not_runtime_state_invariant"] == (
        "insufficient_evidence"
    )


def test_serialization_preserves_schema_v4_nested_only(tmp_path: Path) -> None:
    fsm, raw_screens = _clock_fsm()
    generate_contract_invariants(fsm, raw_screens, invariant_source="deterministic")

    path = tmp_path / "fsm.json"
    fsm.serialize(path)
    data = json.loads(path.read_text())
    assert data["schema_version"] == "4"
    state_payload = data["states"]["timer_done"]
    # Nested invariant_specs present, no flat mirror.
    assert state_payload["invariant_specs"]
    assert all(
        {"expr", "confidence", "source", "evidence_count"} <= set(s)
        for s in state_payload["invariant_specs"]
    )
    assert "state_invariants" not in state_payload
    assert "invariant_confidence" not in state_payload

    reloaded = AppFSM.deserialize(path)
    assert [s.expr for s in reloaded.states["timer_done"].invariant_specs] == [
        s.expr for s in fsm.states["timer_done"].invariant_specs
    ]


def test_merge_is_non_destructive_and_dedups() -> None:
    fsm, raw_screens = _clock_fsm()
    # Pre-seed timer_done: one duplicate-to-be and one unrelated pre-existing invariant.
    fsm.states["timer_done"].invariant_specs = [
        StateInvariant(
            expr=f"value({PKG}:id/remaining_ms) == 0", confidence=0.99, source="prebuilt"
        ),
        StateInvariant(
            expr='read(com.x:id/keep, text) == "Keep"', confidence=0.4, source="prebuilt"
        ),
    ]
    generate_contract_invariants(fsm, raw_screens, invariant_source="deterministic")

    specs = fsm.states["timer_done"].invariant_specs
    exprs = [s.expr for s in specs]
    # No duplicate of the shared expr.
    assert exprs.count(f"value({PKG}:id/remaining_ms) == 0") == 1
    # Pre-existing unrelated invariant preserved untouched (source/confidence intact).
    keep = next(s for s in specs if "keep" in s.expr.lower())
    assert keep.source == "prebuilt" and keep.confidence == 0.4
    # The pre-seeded duplicate keeps its original metadata (merge skips, never overwrites).
    dup = next(s for s in specs if s.expr == f"value({PKG}:id/remaining_ms) == 0")
    assert dup.source == "prebuilt"


def test_single_visit_title_is_not_attached_as_runtime_invariant() -> None:
    fsm = AppFSM(app_package="com.vigil.chat")
    fsm.add_state(_state("thread", ["alice"]))
    fsm.initial_state = "thread"
    raw_screens = {
        "alice": {
            "screen_id": "alice",
            "interactable_elements": [
                {
                    "element_id": "title",
                    "resource_id": "com.vigil.chat:id/title",
                    "text": "Alice",
                    "class_name": "android.widget.TextView",
                }
            ],
        }
    }

    report = generate_contract_invariants(fsm, raw_screens, invariant_source="deterministic")

    assert fsm.states["thread"].invariant_specs == []
    row = report[0]
    assert row["invariants_admitted"] == []
    assert row["invariants_attached"] == []
    assert row["effect_hints"][0]["lowered_expr"] == (
        'read(com.vigil.chat:id/title, text) == "Alice"'
    )
    assert row["effect_hints"][0]["why_not_runtime_state_invariant"] == "insufficient_evidence"


# ---------------------------------------------------------------------------
# Guard policy preservation: high-risk without admitted guard stays UNCERTAIN
# ---------------------------------------------------------------------------


def test_high_risk_without_guard_remains_uncertain_after_invariants() -> None:
    fsm = AppFSM(app_package="com.vigil.bank")
    fsm.add_state(_state("transfer_confirm", ["c"]))
    fsm.add_state(_state("transfer_success", ["s"]))
    fsm.initial_state = "transfer_confirm"
    action = {"type": "click", "target": "e_confirm", "target_text": "Confirm"}
    fsm.add_transition(
        Transition(
            source="transfer_confirm",
            target="transfer_success",
            action=action,
            confidence=0.95,
            requires_guard=True,  # high-risk: must have an admitted guard
            guard=None,
            guard_admission_status=GuardAdmissionStatus.PENDING,
        )
    )
    raw_screens = {
        "c": {
            "screen_id": "c",
            "interactable_elements": [
                {"element_id": "bal", "resource_id": "com.vigil.bank:id/balance", "text": "100000"}
            ],
        },
        "s": {
            "screen_id": "s",
            "interactable_elements": [
                {
                    "element_id": "t",
                    "resource_id": "com.vigil.bank:id/title",
                    "text": "Transfer successful",
                }
            ],
        },
    }

    engine = DecisionEngine(fsm, VerificationConfig())
    before = engine.verify_by_state("transfer_confirm", action)
    assert before.result is VerifyResult.UNCERTAIN
    assert before.reason is VerifyReason.GUARD_POLICY_UNSATISFIED

    # Running invariant generation must not touch guard fields or change the verdict.
    generate_contract_invariants(fsm, raw_screens, invariant_source="deterministic")
    transition = fsm.transitions[0]
    assert transition.requires_guard is True
    assert transition.guard is None

    engine_after = DecisionEngine(fsm, VerificationConfig())
    after = engine_after.verify_by_state("transfer_confirm", action)
    assert after.result is VerifyResult.UNCERTAIN
    assert after.reason is VerifyReason.GUARD_POLICY_UNSATISFIED
    # Single-visit balance evidence is retained as a hint, not as a hard post-arrival gate.
    assert fsm.states["transfer_confirm"].invariant_specs == []


# ---------------------------------------------------------------------------
# Packet transition-guard candidates: target-only predicate rejected (reuse path)
# ---------------------------------------------------------------------------


class _FixedPacketLlm:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, _system: str, _user: str) -> str:
        return self.response

    def generate_with_images(self, _system, _text, _images, _labels=None) -> str:
        return self.response


def test_target_only_guard_predicate_rejected_via_existing_admission() -> None:
    fsm = AppFSM(app_package="com.app")
    fsm.add_state(_state("A", ["sa"]))
    fsm.add_state(_state("B", ["sb"]))
    fsm.initial_state = "A"
    fsm.add_transition(
        Transition(
            source="A", target="B", action={"type": "click", "target": "e_go"}, confidence=0.9
        )
    )
    raw_screens = {
        "sa": {
            "screen_id": "sa",
            "interactable_elements": [
                {
                    "element_id": "e_go",
                    "resource_id": "com.app:id/go",
                    "text": "Go",
                    "is_clickable": True,
                }
            ],
        },
        "sb": {
            "screen_id": "sb",
            "interactable_elements": [
                {"element_id": "tf", "resource_id": "com.app:id/target_only_field", "text": "Z"}
            ],
        },
    }
    packet = json.dumps(
        {
            "state_invariant_candidates": [],
            "transition_guard_candidates": [
                {
                    "source_state_id": "A",
                    "target_state_id": "B",
                    "canonical_action_key": "",
                    "contract": {
                        "kind": "item_binding",
                        "required": True,
                        "risk_level": "medium",
                        "predicates": [
                            {
                                "predicate_type": "read",
                                "element": "target_only_field",
                                "property": "text",
                                "operator": "==",
                                "expected": {"kind": "literal", "value": "Z"},
                            }
                        ],
                    },
                }
            ],
            "effect_invariant_hints": [],
            "rejected_candidates": [],
        }
    )

    report = generate_contract_invariants(
        fsm,
        raw_screens,
        invariant_source="llm",
        llm=_FixedPacketLlm(packet),
        use_images=False,
    )
    guard_rows = [row for state in report for row in state["guard_candidates"]]
    assert guard_rows, "expected the packet guard candidate to be admitted for the report"
    assert any(row["admitted"] is False for row in guard_rows)
    # The guard pipeline still owns Transition.guard — the invariant pass never attaches it.
    assert fsm.transitions[0].guard is None


# ---------------------------------------------------------------------------
# Finding 1: deterministic synthesis uses runtime value (== text) semantics
# ---------------------------------------------------------------------------


def test_synthesizer_uses_text_not_raw_value_semantics() -> None:
    from vigil.neuro.invariant_contract_synthesizer import synthesize_invariant_candidates
    from vigil.neuro.invariant_evidence import build_invariant_evidence

    rid = "com.app:id/status_title"
    fsm = AppFSM(app_package="com.app")
    fsm.add_state(_state("s", ["o1", "o2"]))
    fsm.initial_state = "s"

    def screen(sid: str) -> dict[str, Any]:
        return {
            "screen_id": sid,
            "elements": [
                {
                    "element_id": "e",
                    "resource_id": rid,
                    "text": "Visible",
                    "value": "42",
                    "class_name": "android.widget.TextView",
                }
            ],
        }

    raw = {"o1": screen("o1"), "o2": screen("o2")}
    ev = build_invariant_evidence(fsm, fsm.states["s"], raw)
    exprs = [c.expr for c in synthesize_invariant_candidates(ev).state_invariant_candidates]
    # Runtime value() == text "Visible" (non-numeric): no numeric value_domain candidate is
    # synthesized from the raw 'value' field; the stable label uses text instead.
    assert not any(e.startswith("value(") for e in exprs)
    assert f'read({rid}, text) == "Visible"' in exprs


# ---------------------------------------------------------------------------
# Finding 2: packet guard candidates never bind to an arbitrary sibling transition
# ---------------------------------------------------------------------------


def _two_edge_fsm() -> tuple[AppFSM, dict[str, Any]]:
    fsm = AppFSM(app_package="com.app")
    fsm.add_state(_state("A", ["sa"]))
    fsm.add_state(_state("B", ["sb"]))
    fsm.initial_state = "A"
    fsm.add_transition(
        Transition(
            source="A",
            target="B",
            action={"type": "click", "target": "e1", "target_text": "First"},
            confidence=0.9,
        )
    )
    fsm.add_transition(
        Transition(
            source="A",
            target="B",
            action={"type": "click", "target": "e2", "target_text": "Second"},
            confidence=0.9,
        )
    )
    raw_screens = {
        "sa": {
            "screen_id": "sa",
            "interactable_elements": [
                {
                    "element_id": "e1",
                    "resource_id": "com.app:id/first",
                    "text": "First",
                    "is_clickable": True,
                },
                {
                    "element_id": "e2",
                    "resource_id": "com.app:id/second",
                    "text": "Second",
                    "is_clickable": True,
                },
            ],
        },
        "sb": {
            "screen_id": "sb",
            "interactable_elements": [
                {"element_id": "x", "resource_id": "com.app:id/x", "text": "X"}
            ],
        },
    }
    return fsm, raw_screens


def _guard_packet(source: str, target: str, cak: str, element: str = "x") -> str:
    return json.dumps(
        {
            "state_invariant_candidates": [],
            "transition_guard_candidates": [
                {
                    "source_state_id": source,
                    "target_state_id": target,
                    "canonical_action_key": cak,
                    "contract": {
                        "kind": "item_binding",
                        "required": True,
                        "risk_level": "medium",
                        "predicates": [
                            {
                                "predicate_type": "read",
                                "element": element,
                                "property": "text",
                                "operator": "==",
                                "expected": {"kind": "literal", "value": "X"},
                            }
                        ],
                    },
                }
            ],
            "effect_invariant_hints": [],
            "rejected_candidates": [],
        }
    )


def test_ambiguous_guard_candidate_when_siblings_and_empty_key() -> None:
    fsm, raw = _two_edge_fsm()
    report = generate_contract_invariants(
        fsm,
        raw,
        invariant_source="llm",
        llm=_FixedPacketLlm(_guard_packet("A", "B", "")),
        use_images=False,
    )
    rows = [r for st in report for r in st["guard_candidates"]]
    assert rows
    assert all(r["admitted"] is False for r in rows)
    assert any(r["status"] == "ambiguous" for r in rows)
    # Ambiguous rows short-circuit before admission — no guard verdict was fabricated.
    for r in rows:
        if r["status"] == "ambiguous":
            assert "guard" not in r and "rejected_predicates" not in r
    assert all(t.guard is None for t in fsm.transitions)


def test_keyed_candidate_is_non_ambiguous_match() -> None:
    # End-to-end: a non-empty canonical_action_key matching a sibling yields a non-ambiguous
    # match that runs admission. (WHICH sibling bound is asserted directly in
    # test_find_transition_binds_to_correct_sibling_by_key.)
    fsm, raw = _two_edge_fsm()
    from vigil.neuro.invariant_evidence import canonical_action_key_str

    second_cak = canonical_action_key_str(fsm.transitions[1].action)
    report = generate_contract_invariants(
        fsm,
        raw,
        invariant_source="llm",
        llm=_FixedPacketLlm(_guard_packet("A", "B", second_cak)),
        use_images=False,
    )
    matched = [
        r
        for st in report
        for r in st["guard_candidates"]
        if r["status"] not in ("ambiguous", "no_matching_transition")
    ]
    # The candidate matched a specific transition and ran admission (element 'x' is a
    # target-only field at A, so the guard is rejected — but it was not ambiguous).
    assert matched
    assert all(r["admitted"] is False for r in matched)
    assert all("rejected_predicates" in r for r in matched)


def test_find_transition_binds_to_correct_sibling_by_key() -> None:
    # Discriminating check: a candidate key binds to the SPECIFIC sibling it names, never an
    # arbitrary first match; empty/mismatched keys among siblings are ambiguous (not bound).
    from vigil.models.invariant_candidate import TransitionGuardCandidate
    from vigil.neuro.invariant_evidence import canonical_action_key_str
    from vigil.neuro.invariant_generation_pipeline import _find_transition

    fsm, _ = _two_edge_fsm()
    for idx in (0, 1):
        cak = canonical_action_key_str(fsm.transitions[idx].action)
        cand = TransitionGuardCandidate(
            source_state_id="A", target_state_id="B", canonical_action_key=cak
        )
        match = _find_transition(fsm, cand)
        assert match.status == "match"
        assert match.index == idx
        assert match.transition is fsm.transitions[idx]

    empty = _find_transition(
        fsm,
        TransitionGuardCandidate(source_state_id="A", target_state_id="B", canonical_action_key=""),
    )
    assert empty.status == "ambiguous" and empty.index is None

    mismatched = _find_transition(
        fsm,
        TransitionGuardCandidate(
            source_state_id="A", target_state_id="B", canonical_action_key='{"type": "nope"}'
        ),
    )
    assert mismatched.status == "ambiguous" and mismatched.index is None


def test_find_transition_tolerates_reformatted_key() -> None:
    # A structurally-identical but cosmetically-reformatted key (reordered keys / whitespace)
    # still binds to the right sibling — recall improvement that never mis-binds.
    from vigil.models.invariant_candidate import TransitionGuardCandidate
    from vigil.neuro.invariant_evidence import canonical_action_key_str
    from vigil.neuro.invariant_generation_pipeline import _find_transition

    fsm, _ = _two_edge_fsm()
    exact = canonical_action_key_str(fsm.transitions[1].action)
    reformatted = json.dumps(json.loads(exact), sort_keys=True, separators=(", ", ": "))
    assert reformatted != exact  # genuinely different bytes, same structure
    match = _find_transition(
        fsm,
        TransitionGuardCandidate(
            source_state_id="A", target_state_id="B", canonical_action_key=reformatted
        ),
    )
    assert match.status == "match"
    assert match.index == 1


# ---------------------------------------------------------------------------
# Finding 3: invariant_source="audit" is strict (never synthesizes, never silent-fails)
# ---------------------------------------------------------------------------


def test_audit_missing_packet_path_attaches_nothing() -> None:
    fsm, raw = _clock_fsm()
    report = generate_contract_invariants(
        fsm, raw, invariant_source="audit", llm_audit_report=[{"state_id": "timer_done"}]
    )
    assert all(s.invariant_specs == [] for s in fsm.states.values())
    done = next(r for r in report if r["state_id"] == "timer_done")
    assert done["source"] == "audit"
    assert done["fallback_reason"] and "missing" in done["fallback_reason"]
    assert done["invariants_attached"] == []
    assert done["invariants_admitted"] == []


def test_audit_unreadable_packet_attaches_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    for path in (missing, bad):
        fsm, raw = _clock_fsm()
        report = generate_contract_invariants(
            fsm,
            raw,
            invariant_source="audit",
            llm_audit_report=[{"state_id": "timer_done", "packet_audit_path": str(path)}],
        )
        assert fsm.states["timer_done"].invariant_specs == []
        done = next(r for r in report if r["state_id"] == "timer_done")
        assert done["source"] == "audit"
        assert "unreadable" in done["fallback_reason"]
        assert done["packet_audit_path"] == str(path)


def test_audit_valid_empty_packet_distinct_from_failure(tmp_path: Path) -> None:
    fsm, raw = _clock_fsm()
    audit_file = tmp_path / "pkt.json"
    audit_file.write_text(json.dumps({"state_id": "timer_done", "packet": {}}), encoding="utf-8")
    report = generate_contract_invariants(
        fsm,
        raw,
        invariant_source="audit",
        llm_audit_report=[{"state_id": "timer_done", "packet_audit_path": str(audit_file)}],
    )
    assert fsm.states["timer_done"].invariant_specs == []
    done = next(r for r in report if r["state_id"] == "timer_done")
    assert done["source"] == "audit"
    # A legitimate empty packet is NOT a load failure: no fallback_reason.
    assert done["fallback_reason"] == ""
