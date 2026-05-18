"""Tests for vigil.symbolic.dsl_evaluator — Tier 2 DSL guard evaluation."""

from __future__ import annotations

from vigil.symbolic.dsl_evaluator import DSLEvaluator, IntentContext, ScreenContext


class TestReadPredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_read_pred_true(self) -> None:
        ctx = ScreenContext(elements={"wifi_item": {"text": "HKU"}})
        r = self.ev.evaluate('read(wifi_item, text) == "HKU"', screen_ctx=ctx)
        assert r.passed is True

    def test_read_pred_false(self) -> None:
        ctx = ScreenContext(elements={"wifi_item": {"text": "D3410"}})
        r = self.ev.evaluate('read(wifi_item, text) == "HKU"', screen_ctx=ctx)
        assert r.passed is False

    def test_read_pred_not_equal(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "hello"}})
        r = self.ev.evaluate('read(item, text) != ""', screen_ctx=ctx)
        assert r.passed is True

    def test_read_missing_element(self) -> None:
        ctx = ScreenContext(elements={})
        r = self.ev.evaluate('read(missing, text) == "hello"', screen_ctx=ctx)
        assert r.passed is False
        assert r.failure_reason != ""

    def test_read_missing_property(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "hi"}})
        r = self.ev.evaluate("read(item, checked) == true", screen_ctx=ctx)
        assert r.passed is False


class TestNumericComparison:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_greater_than(self) -> None:
        ctx = ScreenContext(elements={"amount": {"value": "50"}})
        r = self.ev.evaluate("read(amount, value) > 0", screen_ctx=ctx)
        assert r.passed is True

    def test_less_than(self) -> None:
        ctx = ScreenContext(elements={"amount": {"value": "50"}})
        r = self.ev.evaluate("read(amount, value) < 100", screen_ctx=ctx)
        assert r.passed is True

    def test_greater_fails(self) -> None:
        ctx = ScreenContext(elements={"amount": {"value": "0"}})
        r = self.ev.evaluate("read(amount, value) > 0", screen_ctx=ctx)
        assert r.passed is False


class TestValuePredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_value_equals(self) -> None:
        ctx = ScreenContext(elements={"slider": {"value": "75"}})
        r = self.ev.evaluate("value(slider) == 75", screen_ctx=ctx)
        assert r.passed is True

    def test_value_missing(self) -> None:
        ctx = ScreenContext(elements={})
        r = self.ev.evaluate("value(slider) > 0", screen_ctx=ctx)
        assert r.passed is False


class TestIntentBinding:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_bind_intent_substitution(self) -> None:
        intent = IntentContext(variables={"name": "test"})
        result = self.ev.bind_intent("$intent.name", intent)
        assert result == '"test"'

    def test_evaluate_with_intent_match(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "HKU"}})
        intent = IntentContext(variables={"wifi_name": "HKU"})
        r = self.ev.evaluate(
            "read(item, text) == $intent.wifi_name",
            intent_ctx=intent,
            screen_ctx=ctx,
        )
        assert r.passed is True

    def test_evaluate_intent_mismatch(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "Other"}})
        intent = IntentContext(variables={"wifi_name": "HKU"})
        r = self.ev.evaluate(
            "read(item, text) == $intent.wifi_name",
            intent_ctx=intent,
            screen_ctx=ctx,
        )
        assert r.passed is False

    def test_bind_multiple_variables(self) -> None:
        intent = IntentContext(variables={"a": "x", "b": "y"})
        result = self.ev.bind_intent("$intent.a && $intent.b", intent)
        assert '"x"' in result
        assert '"y"' in result


class TestLogicalOperators:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_and_both_true(self) -> None:
        ctx = ScreenContext(elements={"a": {"x": "1"}, "b": {"y": "2"}})
        r = self.ev.evaluate('read(a, x) == "1" && read(b, y) == "2"', screen_ctx=ctx)
        assert r.passed is True

    def test_and_one_false(self) -> None:
        ctx = ScreenContext(elements={"a": {"x": "1"}, "b": {"y": "99"}})
        r = self.ev.evaluate('read(a, x) == "1" && read(b, y) == "2"', screen_ctx=ctx)
        assert r.passed is False

    def test_or_one_true(self) -> None:
        ctx = ScreenContext(elements={"a": {"x": "1"}, "b": {"y": "99"}})
        r = self.ev.evaluate('read(a, x) == "1" || read(b, y) == "2"', screen_ctx=ctx)
        assert r.passed is True

    def test_or_both_false(self) -> None:
        ctx = ScreenContext(elements={"a": {"x": "0"}, "b": {"y": "0"}})
        r = self.ev.evaluate('read(a, x) == "1" || read(b, y) == "2"', screen_ctx=ctx)
        assert r.passed is False

    def test_not_true(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "hello"}})
        r = self.ev.evaluate('!read(item, text) == ""', screen_ctx=ctx)
        assert r.passed is True

    def test_not_false(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": ""}})
        r = self.ev.evaluate('!read(item, text) == ""', screen_ctx=ctx)
        assert r.passed is False


class TestTimePredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_time_in_range(self) -> None:
        ctx = ScreenContext(current_time="12:30")
        r = self.ev.evaluate("time_in(09:00, 17:00)", screen_ctx=ctx)
        assert r.passed is True

    def test_time_out_of_range(self) -> None:
        ctx = ScreenContext(current_time="22:00")
        r = self.ev.evaluate("time_in(09:00, 17:00)", screen_ctx=ctx)
        assert r.passed is False

    def test_time_no_current(self) -> None:
        ctx = ScreenContext()
        r = self.ev.evaluate("time_in(09:00, 17:00)", screen_ctx=ctx)
        assert r.passed is False


class TestStatePredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_in_state_match(self) -> None:
        ctx = ScreenContext(current_state="WiFiSettings")
        r = self.ev.evaluate("in_state(WiFiSettings)", screen_ctx=ctx)
        assert r.passed is True

    def test_in_state_mismatch(self) -> None:
        ctx = ScreenContext(current_state="MainSettings")
        r = self.ev.evaluate("in_state(WiFiSettings)", screen_ctx=ctx)
        assert r.passed is False


class TestGuardResult:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_result_contains_expressions(self) -> None:
        intent = IntentContext(variables={"name": "test"})
        ctx = ScreenContext(elements={"item": {"text": "test"}})
        r = self.ev.evaluate("read(item, text) == $intent.name", intent_ctx=intent, screen_ctx=ctx)
        assert r.guard_expression == "read(item, text) == $intent.name"
        assert r.bound_expression == 'read(item, text) == "test"'

    def test_parse_error_returns_false(self) -> None:
        r = self.ev.evaluate("invalid !@# syntax")
        assert r.passed is False
        assert "Parse error" in r.failure_reason


# ============================================================
# New predicate tests (contains, count, action)
# ============================================================


class TestContainsPredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_contains_pred_true(self) -> None:
        ctx = ScreenContext(
            elements={
                "wifi_list": {
                    "children": [
                        {"text": "HKU_WiFi"},
                        {"text": "eduroam"},
                        {"text": "CityU_WiFi"},
                    ]
                }
            }
        )
        r = self.ev.evaluate('contains(wifi_list, "HKU_WiFi")', screen_ctx=ctx)
        assert r.passed is True

    def test_contains_pred_false(self) -> None:
        ctx = ScreenContext(
            elements={
                "wifi_list": {
                    "children": [
                        {"text": "HKU_WiFi"},
                        {"text": "eduroam"},
                    ]
                }
            }
        )
        r = self.ev.evaluate('contains(wifi_list, "NonExistent")', screen_ctx=ctx)
        assert r.passed is False

    def test_contains_pred_missing_element(self) -> None:
        ctx = ScreenContext(elements={})
        r = self.ev.evaluate('contains(wifi_list, "HKU_WiFi")', screen_ctx=ctx)
        assert r.passed is False

    def test_contains_text_fallback(self) -> None:
        ctx = ScreenContext(elements={"msg": {"text": "Hello World"}})
        r = self.ev.evaluate('contains(msg, "World")', screen_ctx=ctx)
        assert r.passed is True


class TestCountPredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_count_greater_than_zero(self) -> None:
        ctx = ScreenContext(elements={"cart_items": {"children_count": 3}})
        r = self.ev.evaluate("count(cart_items) > 0", screen_ctx=ctx)
        assert r.passed is True

    def test_count_equals_zero_false(self) -> None:
        ctx = ScreenContext(elements={"cart_items": {"children_count": 3}})
        r = self.ev.evaluate("count(cart_items) == 0", screen_ctx=ctx)
        assert r.passed is False

    def test_count_item_count_property(self) -> None:
        ctx = ScreenContext(elements={"cart_items": {"item_count": 5}})
        r = self.ev.evaluate("count(cart_items) >= 5", screen_ctx=ctx)
        assert r.passed is True

    def test_count_missing_element(self) -> None:
        ctx = ScreenContext(elements={})
        r = self.ev.evaluate("count(cart_items) > 0", screen_ctx=ctx)
        assert r.passed is False


class TestActionPredicate:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_action_pred_match(self) -> None:
        action = {"target_text": "HKU_WiFi", "action_type": "click"}
        r = self.ev.evaluate(
            'action(target_text) == "HKU_WiFi"',
            action_ctx=action,
        )
        assert r.passed is True

    def test_action_pred_mismatch(self) -> None:
        action = {"target_text": "D3410", "action_type": "click"}
        r = self.ev.evaluate(
            'action(target_text) == "HKU_WiFi"',
            action_ctx=action,
        )
        assert r.passed is False

    def test_action_pred_no_context(self) -> None:
        r = self.ev.evaluate('action(target_text) == "HKU_WiFi"')
        assert r.passed is False

    def test_action_pred_missing_property(self) -> None:
        action = {"action_type": "click"}
        r = self.ev.evaluate(
            'action(target_text) == "HKU_WiFi"',
            action_ctx=action,
        )
        assert r.passed is False


class TestIntentVarInGrammar:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_intent_var_resolved_during_eval(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "HKU"}})
        intent = IntentContext(variables={"wifi_name": "HKU"})
        r = self.ev.evaluate(
            "read(item, text) == $intent.wifi_name",
            intent_ctx=intent,
            screen_ctx=ctx,
        )
        assert r.passed is True

    def test_intent_var_in_contains(self) -> None:
        ctx = ScreenContext(
            elements={"wifi_list": {"children": [{"text": "HKU_WiFi"}, {"text": "eduroam"}]}}
        )
        intent = IntentContext(variables={"wifi_name": "HKU_WiFi"})
        r = self.ev.evaluate(
            "contains(wifi_list, $intent.wifi_name)",
            intent_ctx=intent,
            screen_ctx=ctx,
        )
        assert r.passed is True

    def test_intent_var_in_action(self) -> None:
        intent = IntentContext(variables={"wifi_name": "HKU_WiFi"})
        action = {"target_text": "HKU_WiFi"}
        r = self.ev.evaluate(
            "action(target_text) == $intent.wifi_name",
            intent_ctx=intent,
            action_ctx=action,
        )
        assert r.passed is True


class TestResourceIdElement:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_element_with_resource_id(self) -> None:
        ctx = ScreenContext(elements={"com.android.settings:id/title": {"text": "WiFi"}})
        r = self.ev.evaluate(
            'read(com.android.settings:id/title, text) == "WiFi"',
            screen_ctx=ctx,
        )
        assert r.passed is True


class TestCombinedNewPredicates:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_contains_and_count(self) -> None:
        ctx = ScreenContext(
            elements={
                "wifi_list": {"children": [{"text": "HKU_WiFi"}, {"text": "eduroam"}]},
                "cart": {"children_count": 2},
            }
        )
        intent = IntentContext(variables={"wifi_name": "HKU_WiFi"})
        r = self.ev.evaluate(
            "contains(wifi_list, $intent.wifi_name) && count(cart) > 0",
            intent_ctx=intent,
            screen_ctx=ctx,
        )
        assert r.passed is True

    def test_action_and_read(self) -> None:
        ctx = ScreenContext(elements={"status": {"text": "connected"}})
        action = {"target_text": "HKU_WiFi"}
        r = self.ev.evaluate(
            'action(target_text) == "HKU_WiFi" && read(status, text) == "connected"',
            screen_ctx=ctx,
            action_ctx=action,
        )
        assert r.passed is True


# ── Three-valued evaluation (TRUE / FALSE / UNKNOWN) ─────────────


from vigil.symbolic.dsl_evaluator import GuardStatus  # noqa: E402


class TestThreeValuedEvaluation:
    def setup_method(self) -> None:
        self.ev = DSLEvaluator()

    def test_proven_true(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "hi"}})
        r = self.ev.evaluate('read(item, text) == "hi"', screen_ctx=ctx)
        assert r.status is GuardStatus.TRUE
        assert r.passed is True

    def test_proven_false(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "hi"}})
        r = self.ev.evaluate('read(item, text) == "bye"', screen_ctx=ctx)
        assert r.status is GuardStatus.FALSE
        assert r.passed is False

    def test_missing_element_unknown(self) -> None:
        r = self.ev.evaluate('read(missing, text) == "x"', screen_ctx=ScreenContext())
        assert r.status is GuardStatus.UNKNOWN
        assert r.passed is False
        assert "Inconclusive" in r.failure_reason

    def test_missing_intent_var_unknown(self) -> None:
        ctx = ScreenContext(elements={"item": {"text": "x"}})
        intent = IntentContext(variables={})  # var not bound
        r = self.ev.evaluate("read(item, text) == $intent.name", intent_ctx=intent, screen_ctx=ctx)
        assert r.status is GuardStatus.UNKNOWN

    def test_parse_error_unknown(self) -> None:
        r = self.ev.evaluate("!!@@ not valid")
        assert r.status is GuardStatus.UNKNOWN

    def test_and_unknown_false_is_false(self) -> None:
        ctx = ScreenContext(elements={"x": {"text": "hi"}})
        # left is FALSE (proven), right is UNKNOWN (missing element)
        r = self.ev.evaluate('read(x, text) == "no" && read(missing, text) == "y"', screen_ctx=ctx)
        assert r.status is GuardStatus.FALSE

    def test_or_true_unknown_is_true(self) -> None:
        ctx = ScreenContext(elements={"x": {"text": "hi"}})
        r = self.ev.evaluate('read(x, text) == "hi" || read(missing, text) == "y"', screen_ctx=ctx)
        assert r.status is GuardStatus.TRUE

    def test_and_true_unknown_is_unknown(self) -> None:
        ctx = ScreenContext(elements={"x": {"text": "hi"}})
        r = self.ev.evaluate('read(x, text) == "hi" && read(missing, text) == "y"', screen_ctx=ctx)
        assert r.status is GuardStatus.UNKNOWN
