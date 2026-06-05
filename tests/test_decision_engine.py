"""Tests for vigil.symbolic.decision_engine — combined Tier 1 + Tier 2."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.state import RawScreen, UIElement
from vigil.symbolic.decision_engine import DecisionEngine
from vigil.symbolic.dsl_evaluator import IntentContext, ScreenContext
from vigil.symbolic.fsm_checker import VerifyReason, VerifyResult


@pytest.fixture
def guarded_fsm() -> AppFSM:
    """FSM with a DSL guard on s1→s2 and no guard on s2→s3."""
    fsm = AppFSM(app_package="com.test.app")

    s1 = AbstractState(
        state_id="s1",
        name="MainSettings",
        fingerprint="fp_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        activity_name="com.test.app.Main",
    )
    s2 = AbstractState(
        state_id="s2",
        name="WiFiSettings",
        fingerprint="fp_wifi",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s1",
        activity_name="com.test.app.Main",
    )
    s3 = AbstractState(
        state_id="s3",
        name="WiFiDetail",
        fingerprint="fp_wifi_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s2",
        activity_name="com.test.app.Main",
    )

    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.add_state(s3)
    fsm.initial_state = "s1"

    t1 = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "e_0001"},
        guard='read(wifi_item, text) != ""',
        confidence=0.95,
        observed_count=10,
    )
    t2 = Transition(
        source="s2",
        target="s3",
        action={"type": "click", "target": "e_0002"},
        confidence=0.85,
        observed_count=5,
    )

    fsm.add_transition(t1)
    fsm.add_transition(t2)
    return fsm


class TestTier1Only:
    """Tests where only Tier 1 (structural FSM check) is exercised."""

    def test_tier1_allow_no_guard(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        # s2→s3 has no guard — Tier 2 skipped
        out = engine.verify_by_state("s2", {"type": "click", "target": "e_0002"})
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID

    def test_tier1_deny(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        out = engine.verify_by_state("s1", {"type": "scroll_up"})
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.TRANSITION_INVALID

    def test_tier1_blocks_before_tier2(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        # s3 has no outgoing transitions — Tier 1 denies, Tier 2 never runs
        out = engine.verify_by_state("s3", {"type": "click"})
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.TRANSITION_INVALID

    def test_no_evaluator(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm, grammar_path="/nonexistent/grammar.lark")
        assert engine._evaluator is None
        # s2→s3 (no guard) — Tier 1 ALLOW, Tier 2 skipped
        out = engine.verify_by_state("s2", {"type": "click", "target": "e_0002"})
        assert out.result == VerifyResult.ALLOW

    def test_no_evaluator_with_guard(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm, grammar_path="/nonexistent/grammar.lark")
        # s1→s2 has guard, but evaluator is None — Tier 2 skipped
        out = engine.verify_by_state("s1", {"type": "click", "target": "e_0001"})
        assert out.result == VerifyResult.ALLOW


class TestTier2Guard:
    """Tests where Tier 2 DSL guard evaluation runs."""

    def test_tier2_guard_pass(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        ctx = ScreenContext(elements={"wifi_item": {"text": "HKU_WiFi"}})
        out = engine.verify_by_state("s1", {"type": "click", "target": "e_0001"}, screen_ctx=ctx)
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID

    def test_tier2_guard_fail(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        ctx = ScreenContext(elements={"wifi_item": {"text": ""}})
        out = engine.verify_by_state("s1", {"type": "click", "target": "e_0001"}, screen_ctx=ctx)
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.GUARD_FAILED
        assert "guard failed" in out.details.lower()

    def test_tier2_guard_missing_element(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        ctx = ScreenContext(elements={})
        out = engine.verify_by_state("s1", {"type": "click", "target": "e_0001"}, screen_ctx=ctx)
        # Three-valued DSL: missing GUI element is UNKNOWN, not proven false.
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.GUARD_INCONCLUSIVE

    def test_tier2_guard_no_screen_ctx(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        # No screen_ctx → empty ScreenContext → guard cannot read element → UNKNOWN.
        out = engine.verify_by_state("s1", {"type": "click", "target": "e_0001"})
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.GUARD_INCONCLUSIVE


class TestActionContext:
    """Tests for action_pred guard evaluation with action_ctx."""

    @pytest.fixture
    def action_guarded_fsm(self) -> AppFSM:
        """FSM with an action_pred guard on s1→s2."""
        fsm = AppFSM(app_package="com.test.app")
        s1 = AbstractState(
            state_id="s1",
            name="Main",
            fingerprint="fp_main",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        s2 = AbstractState(
            state_id="s2",
            name="WiFi",
            fingerprint="fp_wifi",
            hierarchy_level=HierarchyLevel.FRAGMENT,
        )
        fsm.add_state(s1)
        fsm.add_state(s2)
        fsm.initial_state = "s1"
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click", "target": "e_0001"},
                guard='action(target_text) == "WiFi"',
                confidence=0.95,
                observed_count=10,
            )
        )
        return fsm

    def test_action_context_match(self, action_guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(action_guarded_fsm)
        action_ctx = {"action_type": "click", "target_text": "WiFi"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click", "target": "e_0001"},
            action_ctx=action_ctx,
        )
        assert out.result == VerifyResult.ALLOW

    def test_action_context_mismatch(self, action_guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(action_guarded_fsm)
        action_ctx = {"action_type": "click", "target_text": "Bluetooth"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click", "target": "e_0001"},
            action_ctx=action_ctx,
        )
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.GUARD_FAILED


class TestIntentBinding:
    """Tests for $intent.* variable resolution in guards."""

    @pytest.fixture
    def intent_guarded_fsm(self) -> AppFSM:
        """FSM with an intent-bound guard on s1→s2."""
        fsm = AppFSM(app_package="com.test.app")
        s1 = AbstractState(
            state_id="s1",
            name="Main",
            fingerprint="fp_main",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        s2 = AbstractState(
            state_id="s2",
            name="WiFi",
            fingerprint="fp_wifi",
            hierarchy_level=HierarchyLevel.FRAGMENT,
        )
        fsm.add_state(s1)
        fsm.add_state(s2)
        fsm.initial_state = "s1"
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click"},
                guard="action(target_text) == $intent.wifi_name",
                confidence=0.95,
                observed_count=10,
            )
        )
        return fsm

    def test_intent_binding_in_guard(self, intent_guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(intent_guarded_fsm)
        intent = IntentContext(variables={"wifi_name": "HKU"})
        action_ctx = {"target_text": "HKU"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click"},
            intent_ctx=intent,
            action_ctx=action_ctx,
        )
        assert out.result == VerifyResult.ALLOW

    def test_intent_binding_mismatch(self, intent_guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(intent_guarded_fsm)
        intent = IntentContext(variables={"wifi_name": "HKU"})
        action_ctx = {"target_text": "CityU"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click"},
            intent_ctx=intent,
            action_ctx=action_ctx,
        )
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.GUARD_FAILED


class TestVerifyWithScreen:
    """Tests for the full verify() path using RawScreen."""

    def test_verify_with_screen(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        screen = RawScreen(
            screen_id="scr_001",
            activity_name="com.test.app.Main",
            elements=[
                UIElement(
                    element_id="wifi_item",
                    class_name="android.widget.TextView",
                    text="HKU_WiFi",
                    is_clickable=True,
                    is_enabled=True,
                ),
            ],
        )
        # Mock fingerprint to match s1 (patch class method for Pydantic compat)
        with patch.object(RawScreen, "get_structural_fingerprint", return_value="fp_main"):
            # Use the canonical target id stored on the transition (Sigma identity).
            out = engine.verify(screen, {"type": "click", "target": "e_0001"})
        # Guard: read(wifi_item, text) != "" → "HKU_WiFi" != "" → True
        assert out.result == VerifyResult.ALLOW

    def test_verify_screen_guard_fail(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        screen = RawScreen(
            screen_id="scr_002",
            activity_name="com.test.app.Main",
            elements=[
                UIElement(
                    element_id="wifi_item",
                    class_name="android.widget.TextView",
                    text="",
                    is_clickable=True,
                    is_enabled=True,
                ),
            ],
        )
        with patch.object(RawScreen, "get_structural_fingerprint", return_value="fp_main"):
            out = engine.verify(screen, {"type": "click", "target": "e_0001"})
        # Guard: read(wifi_item, text) != "" → "" != "" → False
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.GUARD_FAILED

    def test_verify_screen_unknown_state(self, guarded_fsm: AppFSM) -> None:
        engine = DecisionEngine(guarded_fsm)
        screen = RawScreen(screen_id="scr_003")
        with patch.object(RawScreen, "get_structural_fingerprint", return_value="fp_unknown"):
            out = engine.verify(screen, {"type": "click"})
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.STATE_UNKNOWN


class TestBuildContextHelpers:
    """Tests for _build_screen_context and _build_action_context."""

    def test_build_screen_context(self) -> None:
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                UIElement(
                    element_id="e_001",
                    class_name="android.widget.TextView",
                    resource_id="com.app:id/title",
                    text="WiFi",
                    is_enabled=True,
                ),
                UIElement(
                    element_id="e_002",
                    class_name="android.widget.Switch",
                    text="",
                    is_checked=True,
                    is_enabled=True,
                ),
            ],
        )
        ctx = DecisionEngine._build_screen_context(screen)
        # Keyed by element_id
        assert ctx.elements["e_001"]["text"] == "WiFi"
        assert ctx.elements["e_002"]["is_checked"] is True
        # Also keyed by resource_id
        assert ctx.elements["com.app:id/title"]["text"] == "WiFi"

    def test_build_screen_context_with_children(self) -> None:
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                UIElement(
                    element_id="e_parent",
                    class_name="android.widget.RecyclerView",
                    is_scrollable=True,
                    children=["e_c1", "e_c2"],
                ),
                UIElement(
                    element_id="e_c1",
                    class_name="android.widget.TextView",
                    text="HKU_WiFi",
                ),
                UIElement(
                    element_id="e_c2",
                    class_name="android.widget.TextView",
                    text="eduroam",
                ),
            ],
        )
        ctx = DecisionEngine._build_screen_context(screen)
        parent = ctx.elements["e_parent"]
        assert parent["children_count"] == 2
        assert len(parent["children"]) == 2
        assert parent["children"][0]["text"] == "HKU_WiFi"

    def test_build_action_context(self) -> None:
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                UIElement(
                    element_id="e_001",
                    class_name="android.widget.TextView",
                    resource_id="com.app:id/wifi",
                    text="WiFi",
                    content_description="WiFi toggle",
                ),
            ],
        )
        action = {"type": "click", "target": "e_001"}
        ctx = DecisionEngine._build_action_context(action, screen)
        assert ctx["action_type"] == "click"
        assert ctx["target_text"] == "WiFi"
        assert ctx["target_resource_id"] == "com.app:id/wifi"
        assert ctx["target_content_desc"] == "WiFi toggle"

    def test_build_action_context_no_target(self) -> None:
        screen = RawScreen(screen_id="scr_001")
        action = {"type": "navigate_back"}
        ctx = DecisionEngine._build_action_context(action, screen)
        assert ctx["action_type"] == "navigate_back"
        assert "target_text" not in ctx

    def test_screen_context_has_synthesized_aliases(self) -> None:
        """Elements without resource_id get synthesized aliases like Switch_0."""
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                UIElement(
                    element_id="e_001",
                    class_name="android.widget.Switch",
                    text="",
                    is_clickable=True,
                    is_checkable=True,
                    is_checked=True,
                    is_enabled=True,
                ),
                UIElement(
                    element_id="e_002",
                    class_name="android.widget.Switch",
                    text="",
                    is_clickable=True,
                    is_checkable=True,
                    is_checked=False,
                    is_enabled=True,
                ),
                UIElement(
                    element_id="e_003",
                    class_name="android.widget.EditText",
                    text="hello",
                    is_clickable=True,
                    is_editable=True,
                    is_enabled=True,
                ),
            ],
        )
        ctx = DecisionEngine._build_screen_context(screen)
        # Synthesized aliases for elements without resource_id
        assert "Switch_0" in ctx.elements
        assert ctx.elements["Switch_0"]["is_checked"] is True
        assert "Switch_1" in ctx.elements
        assert ctx.elements["Switch_1"]["is_checked"] is False
        assert "EditText_0" in ctx.elements
        assert ctx.elements["EditText_0"]["text"] == "hello"
        # Original element_ids still work
        assert "e_001" in ctx.elements
        assert "e_002" in ctx.elements
        assert "e_003" in ctx.elements

    def test_screen_context_has_all_accessibility_properties(self) -> None:
        """ScreenContext must expose all AccessibilityNodeInfo properties."""
        screen = RawScreen(
            screen_id="scr_1",
            elements=[
                UIElement(
                    element_id="e_001",
                    class_name="android.widget.Switch",
                    resource_id="com.test:id/wifi_switch",
                    text="WiFi",
                    is_clickable=True,
                    is_checkable=True,
                    is_checked=True,
                    is_enabled=True,
                    is_editable=False,
                    is_scrollable=False,
                    is_focusable=True,
                ),
            ],
        )
        ctx = DecisionEngine._build_screen_context(screen)
        props = ctx.elements["com.test:id/wifi_switch"]

        assert props["is_clickable"] is True
        assert props["is_checkable"] is True
        assert props["is_checked"] is True
        assert props["is_enabled"] is True
        assert props["is_editable"] is False
        assert props["is_scrollable"] is False
        assert props["is_focusable"] is True
        assert props["text"] == "WiFi"
        assert props["value"] == "WiFi"
        assert props["class_name"] == "android.widget.Switch"


# ── Invariant integration and three-valued guard routing ─────────


class TestInvariantIntegration:
    def _fsm_with_invariant(self, invariant_expr: str) -> AppFSM:
        fsm = AppFSM(app_package="com.test.app")
        s1 = AbstractState(
            state_id="s1",
            name="Src",
            fingerprint="fp_src",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        s2 = AbstractState(
            state_id="s2",
            name="Dst",
            fingerprint="fp_dst",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            state_invariants=[invariant_expr],
        )
        fsm.add_state(s1)
        fsm.add_state(s2)
        fsm.add_transition(
            Transition(source="s1", target="s2", action={"type": "click"}, confidence=0.95)
        )
        fsm.initial_state = "s1"
        return fsm

    @staticmethod
    def _toolbar_screen(child_count: int) -> RawScreen:
        child_ids = [f"toolbar_child_{i}" for i in range(child_count)]
        return RawScreen(
            screen_id="target",
            elements=[
                UIElement(
                    element_id="toolbar",
                    class_name="android.view.ViewGroup",
                    children=child_ids,
                ),
                *[
                    UIElement(element_id=child_id, class_name="android.view.View")
                    for child_id in child_ids
                ],
            ],
        )

    def test_verify_by_state_does_not_run_successor_invariants(self) -> None:
        fsm = self._fsm_with_invariant("count(toolbar) == 1")
        engine = DecisionEngine(fsm)
        ctx = ScreenContext(elements={"toolbar": {"children_count": 0}})
        out = engine.verify_by_state("s1", {"type": "click"}, screen_ctx=ctx)
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID

    def test_verify_does_not_run_successor_invariants_on_pre_action_screen(self) -> None:
        fsm = self._fsm_with_invariant("count(toolbar) == 1")
        engine = DecisionEngine(fsm)
        pre_action_screen = self._toolbar_screen(child_count=0)
        with patch.object(RawScreen, "get_structural_fingerprint", return_value="fp_src"):
            out = engine.verify(pre_action_screen, {"type": "click"})
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID

    def test_post_arrival_invariant_proven_false_denies(self) -> None:
        fsm = self._fsm_with_invariant("count(toolbar) == 1")
        engine = DecisionEngine(fsm)
        out = engine.post_arrival_check("s2", self._toolbar_screen(child_count=2))
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.INVARIANT_FAILED

    def test_post_arrival_invariant_unknown_routes_to_uncertain(self) -> None:
        fsm = self._fsm_with_invariant("count(toolbar) == 1")
        engine = DecisionEngine(fsm)
        out = engine.post_arrival_check("s2", RawScreen(screen_id="target", elements=[]))
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.INVARIANT_INCONCLUSIVE

    def test_post_arrival_invariant_pass_preserves_allow(self) -> None:
        fsm = self._fsm_with_invariant("count(toolbar) == 1")
        engine = DecisionEngine(fsm)
        out = engine.post_arrival_check("s2", self._toolbar_screen(child_count=1))
        assert out.result == VerifyResult.ALLOW

    def test_guard_inconclusive_routes_to_uncertain(self) -> None:
        # Guard depends on missing $intent var → UNKNOWN → UNCERTAIN, not DENY.
        fsm = AppFSM(app_package="com.test.app")
        s1 = AbstractState(
            state_id="s1",
            name="A",
            fingerprint="fp_a",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        s2 = AbstractState(
            state_id="s2",
            name="B",
            fingerprint="fp_b",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        fsm.add_state(s1)
        fsm.add_state(s2)
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click"},
                guard="action(target_text) == $intent.wifi_name",
                confidence=0.95,
            )
        )
        engine = DecisionEngine(fsm)
        out = engine.verify_by_state(
            "s1",
            {"type": "click"},
            intent_ctx=IntentContext(variables={}),  # missing wifi_name
            action_ctx={"target_text": "X"},
        )
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.GUARD_INCONCLUSIVE


def test_verify_by_state_minimal_action_context_evaluates_input_text() -> None:
    """verify_by_state builds a minimal action context (no RawScreen) for action(input_text)."""
    from vigil.models.guard import GuardAdmissionStatus, RiskLevel

    fsm = AppFSM(app_package="com.test.app")
    fsm.add_state(
        AbstractState(
            state_id="s1",
            name="Compose",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
    )
    fsm.add_state(
        AbstractState(
            state_id="s2",
            name="Sent",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
    )
    fsm.initial_state = "s1"
    action = {"type": "input_text", "target": "e_msg", "text": "hello"}
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action=action,
            confidence=0.95,
            guard="action(input_text) == $intent.message_text",
            risk_level=RiskLevel.MEDIUM,
            guard_admission_status=GuardAdmissionStatus.ADMITTED,
        )
    )
    engine = DecisionEngine(fsm)

    # No action_ctx and no screen_ctx -> minimal context derived from proposed_action.
    out = engine.verify_by_state(
        "s1", dict(action), intent_ctx=IntentContext(variables={"message_text": "hello"})
    )
    assert out.result is VerifyResult.ALLOW

    out2 = engine.verify_by_state(
        "s1", dict(action), intent_ctx=IntentContext(variables={"message_text": "other"})
    )
    assert out2.result is VerifyResult.DENY
    assert out2.reason is VerifyReason.GUARD_FAILED
