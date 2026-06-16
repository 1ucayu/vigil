"""Tests for deterministic state-invariant admission (evidence-replay gate).

Covers the contract-first invariant admission path plus the four fidelity-app gold-like
examples. The admitted expression is the resource-id-lowered form of the candidate (the
gold dotted names like ``payment.total_amount`` are the canonical/alias form; the
generated pipeline emits real ``resource_id``s resolved from the registry).
"""

from __future__ import annotations

from typing import Any

from vigil.models.invariant_candidate import StateInvariantCandidate
from vigil.neuro.guard_registry import build_widget_registry_from_screen
from vigil.neuro.invariant_admission import admit_state_invariant_candidate, screen_context_from_raw
from vigil.neuro.invariant_evidence import InvariantEvidence

MARKET = "com.vigil.market"
BANK = "com.vigil.bank"
CHAT = "com.vigil.chat"
CLOCK = "com.vigil.clock"


def _obs(screen_id: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a raw trace screen dict from compact element specs."""
    items: list[dict[str, Any]] = []
    for index, element in enumerate(elements):
        item: dict[str, Any] = {
            "element_id": element.get("element_id", f"e{index}"),
            "resource_id": element.get("resource_id", ""),
            "text": element.get("text", ""),
            "class_name": element.get("class_name", "android.widget.TextView"),
        }
        for key in ("is_enabled", "is_checked", "is_clickable", "is_editable", "value"):
            if key in element:
                item[key] = element[key]
        items.append(item)
    return {"screen_id": screen_id, "interactable_elements": items}


def _evidence(state_id: str, observations: list[dict[str, Any]]) -> InvariantEvidence:
    registry = build_widget_registry_from_screen(state_id, observations[0])
    return InvariantEvidence(
        target_state_id=state_id,
        arrival_registry=registry,
        observations=observations,
        observation_count=len(observations),
    )


def _cand(expr: str, **kw: Any) -> StateInvariantCandidate:
    return StateInvariantCandidate(expr=expr, source=kw.pop("source", "llm"), **kw)


# ---------------------------------------------------------------------------
# Accept: stable executable facts
# ---------------------------------------------------------------------------


def test_numeric_range_invariant_admitted_multi_visit() -> None:
    ev = _evidence(
        "payment_confirm",
        [
            _obs("a", [{"resource_id": f"{MARKET}:id/total_amount", "text": "42"}]),
            _obs("b", [{"resource_id": f"{MARKET}:id/total_amount", "text": "58"}]),
        ],
    )
    result = admit_state_invariant_candidate(_cand("value(total_amount) >= 0"), ev)
    assert result.admitted is True
    assert result.classification == "runtime_state_invariant"
    assert result.invariant is not None
    assert result.invariant.expr == f"value({MARKET}:id/total_amount) >= 0"
    assert result.invariant.evidence_count == 2
    assert result.invariant.confidence > 0.5  # multi-visit is stronger


def test_stable_bool_fact_admitted() -> None:
    ev = _evidence(
        "form",
        [
            _obs(
                "a",
                [
                    {
                        "resource_id": "com.app:id/submit",
                        "text": "Submit",
                        "is_clickable": True,
                        "is_enabled": True,
                    }
                ],
            ),
            _obs(
                "b",
                [
                    {
                        "resource_id": "com.app:id/submit",
                        "text": "Submit",
                        "is_clickable": True,
                        "is_enabled": True,
                    }
                ],
            ),
        ],
    )
    result = admit_state_invariant_candidate(_cand("read(submit, is_enabled) == true"), ev)
    assert result.admitted is True
    assert result.invariant is not None
    assert result.invariant.expr == "read(com.app:id/submit, is_enabled) == true"


def test_single_quoted_string_literal_admitted_and_canonicalized() -> None:
    rid = "com.app:id/screen_marker"
    ev = _evidence(
        "home",
        [
            _obs("a", [{"resource_id": rid, "text": "screen:home"}]),
            _obs("b", [{"resource_id": rid, "text": "screen:home"}]),
        ],
    )

    result = admit_state_invariant_candidate(
        _cand("read(com.app:id/screen_marker, text) == 'screen:home'"),
        ev,
    )

    assert result.admitted is True
    assert result.invariant is not None
    assert result.invariant.expr == 'read(com.app:id/screen_marker, text) == "screen:home"'


def test_candidate_using_resource_id_directly_admitted() -> None:
    rid = f"{CLOCK}:id/elapsed_ms"
    ev = _evidence(
        "stopwatch_idle",
        [
            _obs("a", [{"resource_id": rid, "text": "0"}]),
            _obs("b", [{"resource_id": rid, "text": "0"}]),
        ],
    )
    result = admit_state_invariant_candidate(_cand(f"value({rid}) == 0"), ev)
    assert result.admitted is True
    assert result.invariant is not None
    assert result.invariant.expr == f"value({rid}) == 0"


# ---------------------------------------------------------------------------
# Route to effect hint: intent / action dependent (NOT runtime invariants)
# ---------------------------------------------------------------------------


def test_intent_dependent_candidate_becomes_hint_not_invariant() -> None:
    ev = _evidence("thread", [_obs("a", [{"resource_id": f"{CHAT}:id/title", "text": "Alice"}])])
    result = admit_state_invariant_candidate(_cand("contains(title, $intent.contact_name)"), ev)
    assert result.admitted is False
    assert result.classification == "effect_hint"
    assert result.hint_reason == "depends_on_intent"
    assert result.invariant is None


def test_intent_on_read_rhs_becomes_hint() -> None:
    ev = _evidence(
        "transfer_confirm", [_obs("a", [{"resource_id": f"{BANK}:id/recipient", "text": "Bob"}])]
    )
    result = admit_state_invariant_candidate(
        _cand("read(recipient, text) == $intent.recipient_name"), ev
    )
    assert result.classification == "effect_hint"
    assert result.hint_reason == "depends_on_intent"


def test_action_dependent_candidate_becomes_hint() -> None:
    ev = _evidence("s", [_obs("a", [{"resource_id": "com.app:id/x", "text": "y"}])])
    result = admit_state_invariant_candidate(_cand('action(type) == "click"'), ev)
    assert result.classification == "effect_hint"
    assert result.hint_reason == "depends_on_action"


def test_in_state_predicate_becomes_hint_unsupported() -> None:
    ev = _evidence("s", [_obs("a", [{"resource_id": "com.app:id/x", "text": "y"}])])
    result = admit_state_invariant_candidate(_cand("in_state(checkout)"), ev)
    assert result.classification == "effect_hint"
    assert result.hint_reason == "unsupported_predicate"


# ---------------------------------------------------------------------------
# Reject: static-only / volatile / compound / no evidence
# ---------------------------------------------------------------------------


def test_static_only_candidate_rejected() -> None:
    # Element absent from the runtime registry (e.g. only an APK-prior string) -> rejected.
    ev = _evidence("s", [_obs("a", [{"resource_id": "com.app:id/present", "text": "x"}])])
    result = admit_state_invariant_candidate(
        _cand('read(com.app:id/absent_static_only, text) == "Transfer"'), ev
    )
    assert result.admitted is False
    assert result.classification == "rejected"
    assert "not runtime-resolvable" in result.reason


def test_volatile_value_rejected() -> None:
    ev = _evidence(
        "timer_running",
        [
            _obs("a", [{"resource_id": f"{CLOCK}:id/remaining_ms", "text": "0"}]),
            _obs("b", [{"resource_id": f"{CLOCK}:id/remaining_ms", "text": "5000"}]),
        ],
    )
    result = admit_state_invariant_candidate(_cand("value(remaining_ms) == 0"), ev)
    assert result.admitted is False
    assert result.classification == "rejected"
    assert "not supported by all observations" in result.reason


def test_compound_expression_rejected() -> None:
    ev = _evidence("s", [_obs("a", [{"resource_id": "com.app:id/x", "text": "1"}])])
    result = admit_state_invariant_candidate(_cand('value(x) >= 0 && read(x, text) == "1"'), ev)
    assert result.admitted is False
    assert result.classification == "rejected"
    assert "single" in result.reason


def test_no_observations_rejected() -> None:
    reg = build_widget_registry_from_screen(
        "s", _obs("a", [{"resource_id": "com.app:id/x", "text": "0"}])
    )
    ev = InvariantEvidence(target_state_id="s", arrival_registry=reg, observations=[])
    result = admit_state_invariant_candidate(_cand("value(x) >= 0"), ev)
    assert result.admitted is False
    assert "no runtime observations" in result.reason


def test_single_observation_candidate_becomes_hint_not_runtime_invariant() -> None:
    ev = _evidence(
        "thread",
        [_obs("a", [{"resource_id": f"{CHAT}:id/title", "text": "Alice"}])],
    )
    result = admit_state_invariant_candidate(_cand('read(title, text) == "Alice"'), ev)
    assert result.admitted is False
    assert result.classification == "effect_hint"
    assert result.hint_reason == "insufficient_evidence"
    assert result.invariant is None
    assert result.lowered_expr == f'read({CHAT}:id/title, text) == "Alice"'


def test_element_present_in_only_some_observations_rejected() -> None:
    ev = _evidence(
        "s",
        [
            _obs("a", [{"resource_id": "com.app:id/x", "text": "0"}]),
            _obs("b", [{"resource_id": "com.app:id/other", "text": "0"}]),  # x missing here
        ],
    )
    result = admit_state_invariant_candidate(_cand("value(x) >= 0"), ev)
    assert result.admitted is False
    assert result.classification == "rejected"


def test_duplicate_resource_id_rejected_before_runtime_gate() -> None:
    rid = "com.app:id/row_title"
    screen_a = {
        "screen_id": "a",
        "elements": [
            {
                "element_id": "r1",
                "resource_id": rid,
                "text": "Expected",
                "class_name": "android.widget.TextView",
            },
            {
                "element_id": "r2",
                "resource_id": rid,
                "text": "Other",
                "class_name": "android.widget.TextView",
            },
        ],
    }
    screen_b = {
        "screen_id": "b",
        "elements": [
            {
                "element_id": "r1",
                "resource_id": rid,
                "text": "Expected",
                "class_name": "android.widget.TextView",
            },
            {
                "element_id": "r2",
                "resource_id": rid,
                "text": "Other",
                "class_name": "android.widget.TextView",
            },
        ],
    }
    ev = _evidence(
        "list",
        [screen_a, screen_b],
    )
    assert screen_context_from_raw(screen_a).elements[rid]["text"] == "Other"
    result = admit_state_invariant_candidate(_cand('read(row_title, text) == "Expected"'), ev)
    assert result.admitted is False
    assert result.classification == "rejected"
    assert "not unique" in result.reason


# ---------------------------------------------------------------------------
# Runtime parity: value == text semantics; is_enabled default True
# ---------------------------------------------------------------------------


def test_value_uses_runtime_text_semantics_not_raw_value() -> None:
    # Runtime DecisionEngine._build_screen_context sets value = e.text. A {text, value}
    # split must replay with value == text, so a raw value="42" cannot admit value(field)==42.
    obs = [
        _obs("a", [{"resource_id": "com.app:id/field", "text": "Visible", "value": "42"}]),
        _obs("b", [{"resource_id": "com.app:id/field", "text": "Visible", "value": "42"}]),
    ]
    ev = _evidence("s", obs)
    rid = "com.app:id/field"
    assert screen_context_from_raw(obs[0]).elements[rid]["value"] == "Visible"
    # Numeric candidate must NOT admit — runtime would read "Visible" and go UNKNOWN.
    assert admit_state_invariant_candidate(_cand("value(field) >= 0"), ev).admitted is False
    # The text candidate reflects runtime value == text semantics.
    good = admit_state_invariant_candidate(_cand('value(field) == "Visible"'), ev)
    assert good.admitted is True
    assert good.invariant is not None
    assert good.invariant.expr == f'value({rid}) == "Visible"'


def test_missing_is_enabled_replays_like_runtime_default_true() -> None:
    # UIElement.is_enabled defaults True; a compact observation omitting it must replay as
    # enabled so read(field, is_enabled) == true is consistent with a live RawScreen.
    obs = [
        _obs("a", [{"resource_id": "com.app:id/field", "text": "X", "is_clickable": True}]),
        _obs("b", [{"resource_id": "com.app:id/field", "text": "X", "is_clickable": True}]),
    ]
    assert screen_context_from_raw(obs[0]).elements["com.app:id/field"]["is_enabled"] is True
    ev = _evidence("s", obs)
    enabled = admit_state_invariant_candidate(_cand("read(field, is_enabled) == true"), ev)
    assert enabled.admitted is True
    # The inverse must NOT be admitted — it would DENY at runtime (default True).
    disabled = admit_state_invariant_candidate(_cand("read(field, is_enabled) == false"), ev)
    assert disabled.admitted is False


def test_screen_context_omits_item_count_for_runtime_parity() -> None:
    # Runtime DecisionEngine._build_screen_context never sets item_count and only sets
    # children_count from real children; offline replay must match, or a count()-via-
    # item_count invariant would admit offline yet read UNKNOWN at runtime.
    ctx = screen_context_from_raw(
        {
            "elements": [
                {
                    "element_id": "e",
                    "resource_id": "com.app:id/list",
                    "item_count": 3,
                    "children_count": 3,
                }
            ]
        }
    )
    props = ctx.elements["com.app:id/list"]
    assert "item_count" not in props
    assert "children_count" not in props


# ---------------------------------------------------------------------------
# Four fidelity-app gold-like examples
# ---------------------------------------------------------------------------


def test_market_gold_like_examples() -> None:
    confirm = _evidence(
        "payment_confirm",
        [
            _obs("a", [{"resource_id": f"{MARKET}:id/total_amount", "text": "129"}]),
            _obs("b", [{"resource_id": f"{MARKET}:id/total_amount", "text": "129"}]),
        ],
    )
    r1 = admit_state_invariant_candidate(_cand("value(total_amount) >= 0"), confirm)
    assert r1.admitted and r1.invariant.expr == f"value({MARKET}:id/total_amount) >= 0"

    success = _evidence(
        "payment_success",
        [
            _obs("a", [{"resource_id": f"{MARKET}:id/title", "text": "Payment successful"}]),
            _obs("b", [{"resource_id": f"{MARKET}:id/title", "text": "Payment successful"}]),
        ],
    )
    r2 = admit_state_invariant_candidate(_cand('contains(title, "Payment successful")'), success)
    assert r2.admitted
    assert r2.invariant.expr == f'value({MARKET}:id/title) contains "Payment successful"'


def test_bank_gold_like_examples() -> None:
    confirm = _evidence(
        "transfer_confirm",
        [
            _obs("a", [{"resource_id": f"{BANK}:id/balance", "text": "100000"}]),
            _obs("b", [{"resource_id": f"{BANK}:id/balance", "text": "99500"}]),
        ],
    )
    r1 = admit_state_invariant_candidate(_cand("value(balance) >= 0"), confirm)
    assert r1.admitted and r1.invariant.expr == f"value({BANK}:id/balance) >= 0"

    success = _evidence(
        "transfer_success",
        [
            _obs("a", [{"resource_id": f"{BANK}:id/title", "text": "Transfer successful"}]),
            _obs("b", [{"resource_id": f"{BANK}:id/title", "text": "Transfer successful"}]),
        ],
    )
    r2 = admit_state_invariant_candidate(_cand('contains(title, "Transfer successful")'), success)
    assert r2.admitted
    assert r2.invariant.expr == f'value({BANK}:id/title) contains "Transfer successful"'


def test_chat_thread_intent_title_is_hint_not_invariant() -> None:
    ev = _evidence("thread", [_obs("a", [{"resource_id": f"{CHAT}:id/title", "text": "Alice"}])])
    result = admit_state_invariant_candidate(_cand("contains(title, $intent.contact_name)"), ev)
    assert result.classification == "effect_hint"
    assert result.hint_reason == "depends_on_intent"
    assert result.invariant is None


def test_clock_timer_stopwatch_numeric_invariants() -> None:
    done = _evidence(
        "timer_done",
        [
            _obs("a", [{"resource_id": f"{CLOCK}:id/remaining_ms", "text": "0"}]),
            _obs("b", [{"resource_id": f"{CLOCK}:id/remaining_ms", "text": "0"}]),
        ],
    )
    r_done = admit_state_invariant_candidate(_cand("value(remaining_ms) == 0"), done)
    assert r_done.admitted and r_done.invariant.expr == f"value({CLOCK}:id/remaining_ms) == 0"

    for state, text in (("timer_running", "5000"), ("timer_paused", "3000")):
        ev = _evidence(
            state,
            [
                _obs("a", [{"resource_id": f"{CLOCK}:id/remaining_ms", "text": text}]),
                _obs("b", [{"resource_id": f"{CLOCK}:id/remaining_ms", "text": "1000"}]),
            ],
        )
        r = admit_state_invariant_candidate(_cand("value(remaining_ms) >= 0"), ev)
        assert r.admitted and r.invariant.expr == f"value({CLOCK}:id/remaining_ms) >= 0"

    idle = _evidence(
        "stopwatch_idle",
        [
            _obs("a", [{"resource_id": f"{CLOCK}:id/elapsed_ms", "text": "0"}]),
            _obs("b", [{"resource_id": f"{CLOCK}:id/elapsed_ms", "text": "0"}]),
        ],
    )
    r_idle = admit_state_invariant_candidate(_cand("value(elapsed_ms) == 0"), idle)
    assert r_idle.admitted and r_idle.invariant.expr == f"value({CLOCK}:id/elapsed_ms) == 0"
