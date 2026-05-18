"""Tests for vigil.symbolic.intent_extractor — variable-guided slot filling."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.symbolic.decision_engine import DecisionEngine
from vigil.symbolic.dsl_evaluator import IntentContext
from vigil.symbolic.fsm_checker import VerifyReason, VerifyResult
from vigil.symbolic.intent_extractor import IntentExtractor


@pytest.fixture
def intent_fsm() -> AppFSM:
    """FSM with $intent.* guards for testing."""
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
    s3 = AbstractState(
        state_id="s3",
        name="WiFiDetail",
        fingerprint="fp_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
    )
    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.add_state(s3)
    fsm.initial_state = "s1"

    # s1→s2: one $intent variable
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click"},
            guard="action(target_text) == $intent.wifi_name",
            confidence=0.9,
        )
    )
    # s1→s3: two $intent variables in one guard
    fsm.add_transition(
        Transition(
            source="s1",
            target="s3",
            action={"type": "long_press"},
            guard="action(target_text) == $intent.target_setting && read(el, prop) == $intent.val",
            confidence=0.9,
        )
    )
    # s2→s3: no $intent variable
    fsm.add_transition(
        Transition(
            source="s2",
            target="s3",
            action={"type": "click"},
            guard="read(switch, is_checked) == true",
            confidence=0.9,
        )
    )
    # s2→s1: null guard
    fsm.add_transition(
        Transition(
            source="s2",
            target="s1",
            action={"type": "navigate_back"},
            guard=None,
            confidence=0.9,
        )
    )
    return fsm


class TestCollectRequiredVariables:
    """Test variable name extraction from guard strings."""

    def test_single_intent_variable(self, intent_fsm: AppFSM) -> None:
        """Guard with one $intent.wifi_name → {"wifi_name"}"""
        variables = IntentExtractor.collect_required_variables(intent_fsm, "s1")
        assert "wifi_name" in variables

    def test_multiple_variables_same_guard(self, intent_fsm: AppFSM) -> None:
        """Guard with two $intent vars → both collected."""
        variables = IntentExtractor.collect_required_variables(intent_fsm, "s1")
        assert "target_setting" in variables
        assert "val" in variables

    def test_all_variables_from_state(self, intent_fsm: AppFSM) -> None:
        """s1 has transitions with wifi_name, target_setting, val."""
        variables = IntentExtractor.collect_required_variables(intent_fsm, "s1")
        assert variables == {"wifi_name", "target_setting", "val"}

    def test_no_intent_variables(self, intent_fsm: AppFSM) -> None:
        """Guard: read(switch, is_checked) == true → empty set."""
        variables = IntentExtractor.collect_required_variables(intent_fsm, "s2")
        assert variables == set()

    def test_null_guard(self, intent_fsm: AppFSM) -> None:
        """Guard is None → empty set (s2→s1 has null guard)."""
        # s2 has a non-intent guard and a null guard — both yield no vars
        variables = IntentExtractor.collect_required_variables(intent_fsm, "s2")
        assert variables == set()

    def test_nonexistent_state(self, intent_fsm: AppFSM) -> None:
        """Nonexistent state → empty set."""
        variables = IntentExtractor.collect_required_variables(intent_fsm, "s999")
        assert variables == set()

    def test_collect_all_variables(self, intent_fsm: AppFSM) -> None:
        """collect_all_variables across entire FSM."""
        all_vars = IntentExtractor.collect_all_variables(intent_fsm)
        assert "s1" in all_vars
        assert all_vars["s1"] == {"wifi_name", "target_setting", "val"}
        # s2 has no intent vars — should not appear
        assert "s2" not in all_vars


class TestExtract:
    """Test the full extract() flow."""

    def test_extract_basic(self, intent_fsm: AppFSM) -> None:
        """Mock LLM returns {"wifi_name": "HKU"} for instruction."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        mock_llm.generate.return_value = '{"wifi_name": "HKU", "target_setting": "", "val": ""}'
        extractor._llm = mock_llm

        ctx = extractor.extract("Connect to HKU WiFi", "s1")
        assert ctx.variables["wifi_name"] == "HKU"
        mock_llm.generate.assert_called_once()

    def test_extract_no_variables_needed(self, intent_fsm: AppFSM) -> None:
        """State guards have no $intent.* → returns existing_context, no LLM call."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        extractor._llm = mock_llm

        existing = IntentContext(raw_instruction="test", variables={"foo": "bar"})
        ctx = extractor.extract("some instruction", "s2", existing_context=existing)
        assert ctx is existing
        mock_llm.generate.assert_not_called()

    def test_extract_incremental(self, intent_fsm: AppFSM) -> None:
        """existing_context has wifi_name → only extract target_setting and val."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        mock_llm.generate.return_value = '{"target_setting": "Display", "val": "50"}'
        extractor._llm = mock_llm

        existing = IntentContext(raw_instruction="old", variables={"wifi_name": "HKU"})
        ctx = extractor.extract("Set Display to 50", "s1", existing_context=existing)
        # Merged: old + new
        assert ctx.variables["wifi_name"] == "HKU"
        assert ctx.variables["target_setting"] == "Display"
        assert ctx.variables["val"] == "50"
        mock_llm.generate.assert_called_once()

    def test_extract_all_cached(self, intent_fsm: AppFSM) -> None:
        """All needed variables already in existing_context → no LLM call."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        extractor._llm = mock_llm

        existing = IntentContext(
            raw_instruction="old",
            variables={"wifi_name": "HKU", "target_setting": "X", "val": "Y"},
        )
        ctx = extractor.extract("whatever", "s1", existing_context=existing)
        assert ctx is existing
        mock_llm.generate.assert_not_called()

    def test_extract_llm_failure(self, intent_fsm: AppFSM) -> None:
        """LLM returns garbage → returns existing_context or empty IntentContext."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "not json at all"
        extractor._llm = mock_llm

        ctx = extractor.extract("Connect to HKU WiFi", "s1")
        # Should return IntentContext with empty variables (LLM failed)
        assert ctx.variables == {}
        # Two calls: initial + retry
        assert mock_llm.generate.call_count == 2

    def test_extract_no_llm_uses_rules(self, intent_fsm: AppFSM) -> None:
        """Without LLM, falls back to rule-based extraction."""
        extractor = IntentExtractor(intent_fsm)
        # No LLM configured
        assert extractor._llm is None
        ctx = extractor.extract('Connect to "HKU_WiFi"', "s1")
        # Rule-based extracts quoted string to one of the variables
        assert "HKU_WiFi" in ctx.variables.values()


class TestExtractViaLlm:
    """Test LLM extraction specifically."""

    def test_prompt_includes_all_variables(self, intent_fsm: AppFSM) -> None:
        """Verify the LLM prompt contains all variable names."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        mock_llm.generate.return_value = '{"wifi_name": "HKU", "target_setting": "", "val": ""}'
        extractor._llm = mock_llm

        extractor.extract("Connect to HKU WiFi", "s1")
        call_args = mock_llm.generate.call_args
        user_msg = call_args[0][1]
        assert "wifi_name" in user_msg
        assert "target_setting" in user_msg
        assert "val" in user_msg

    def test_json_parse_success(self) -> None:
        """LLM returns valid JSON → parsed correctly."""
        result = IntentExtractor._parse_json_response('{"wifi_name": "HKU"}')
        assert result == {"wifi_name": "HKU"}

    def test_json_parse_with_markdown_fences(self) -> None:
        """LLM returns ```json {...} ``` → strip fences, parse."""
        result = IntentExtractor._parse_json_response('```json\n{"wifi_name": "HKU"}\n```')
        assert result == {"wifi_name": "HKU"}

    def test_retry_on_parse_failure(self, intent_fsm: AppFSM) -> None:
        """First response invalid, second valid → retry works, 2 LLM calls."""
        extractor = IntentExtractor(intent_fsm)
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = [
            "not json",
            '{"wifi_name": "HKU", "target_setting": "", "val": ""}',
        ]
        extractor._llm = mock_llm

        ctx = extractor.extract("Connect to HKU WiFi", "s1")
        assert ctx.variables["wifi_name"] == "HKU"
        assert mock_llm.generate.call_count == 2


class TestExtractViaRules:
    """Test rule-based fallback."""

    def test_quoted_string_extraction(self) -> None:
        """Instruction with quoted string → extracted."""
        result = IntentExtractor._extract_via_rules('Connect to "HKU_WiFi"', {"wifi_name"})
        assert result["wifi_name"] == "HKU_WiFi"

    def test_single_quotes(self) -> None:
        """Single quotes work too."""
        result = IntentExtractor._extract_via_rules("Connect to 'eduroam'", {"wifi_name"})
        assert result["wifi_name"] == "eduroam"

    def test_boolean_on(self) -> None:
        """'turn on' → true for boolean-hint variables."""
        result = IntentExtractor._extract_via_rules("Turn on Bluetooth", {"bluetooth_enabled"})
        assert result["bluetooth_enabled"] == "true"

    def test_boolean_off_chinese(self) -> None:
        """'关掉' → false for boolean-hint variables."""
        result = IntentExtractor._extract_via_rules("帮我把蓝牙关掉", {"bluetooth_toggle"})
        assert result["bluetooth_toggle"] == "false"

    def test_no_match_returns_empty(self) -> None:
        """Ambiguous instruction with non-boolean variable → empty dict."""
        result = IntentExtractor._extract_via_rules("do something", {"wifi_name"})
        assert result == {}


class TestDecisionEngineIntegration:
    """Test IntentExtractor integration with DecisionEngine."""

    @pytest.fixture
    def intent_guarded_fsm(self) -> AppFSM:
        """FSM with $intent guard for integration tests."""
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
            )
        )
        return fsm

    def test_auto_extract_from_raw_instruction(self, intent_guarded_fsm: AppFSM) -> None:
        """verify_by_state with raw_instruction triggers extraction."""
        mock_extractor = MagicMock(spec=IntentExtractor)
        mock_extractor.extract.return_value = IntentContext(
            raw_instruction="Connect to HKU",
            variables={"wifi_name": "HKU"},
        )
        engine = DecisionEngine(intent_guarded_fsm, intent_extractor=mock_extractor)
        action_ctx = {"action_type": "click", "target_text": "HKU"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click"},
            action_ctx=action_ctx,
            raw_instruction="Connect to HKU",
        )
        assert out.result == VerifyResult.ALLOW
        mock_extractor.extract.assert_called_once_with("Connect to HKU", "s1")

    def test_explicit_intent_overrides_extraction(self, intent_guarded_fsm: AppFSM) -> None:
        """If intent_ctx is provided, raw_instruction is ignored."""
        mock_extractor = MagicMock(spec=IntentExtractor)
        engine = DecisionEngine(intent_guarded_fsm, intent_extractor=mock_extractor)
        intent = IntentContext(variables={"wifi_name": "HKU"})
        action_ctx = {"action_type": "click", "target_text": "HKU"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click"},
            intent_ctx=intent,
            action_ctx=action_ctx,
            raw_instruction="This should be ignored",
        )
        assert out.result == VerifyResult.ALLOW
        mock_extractor.extract.assert_not_called()

    def test_no_extractor_falls_through(self, intent_guarded_fsm: AppFSM) -> None:
        """DecisionEngine without intent_extractor → guard with $intent
        cannot bind → UNCERTAIN (three-valued semantics)."""
        engine = DecisionEngine(intent_guarded_fsm)
        action_ctx = {"action_type": "click", "target_text": "HKU"}
        out = engine.verify_by_state(
            "s1",
            {"type": "click"},
            action_ctx=action_ctx,
            raw_instruction="Connect to HKU",
        )
        # No extractor → intent_ctx is None → $intent.wifi_name unbound → UNKNOWN
        # → UNCERTAIN with GUARD_INCONCLUSIVE (was previously DENY).
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.GUARD_INCONCLUSIVE

    def test_get_required_variables(self, intent_guarded_fsm: AppFSM) -> None:
        """DecisionEngine.get_required_variables delegates correctly."""
        engine = DecisionEngine(intent_guarded_fsm)
        variables = engine.get_required_variables("s1")
        assert variables == {"wifi_name"}

    def test_get_required_variables_no_intent(self, intent_guarded_fsm: AppFSM) -> None:
        """State with no outgoing intent vars → empty set."""
        engine = DecisionEngine(intent_guarded_fsm)
        variables = engine.get_required_variables("s2")
        assert variables == set()
