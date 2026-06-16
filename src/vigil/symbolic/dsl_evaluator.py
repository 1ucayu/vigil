"""Tier 2: DSL Semantic Verification (< 15 ms).

Evaluates DSL guard expressions against the current screen state using the Lark
parser shipped with the symbolic verifier. Guard templates are cached offline;
parameters are bound at runtime from user intent.

Evaluation is three-valued (TRUE / FALSE / UNKNOWN) to match the paper model:
proven-false predicates are distinguishable from those the verifier cannot
read (missing element, unbound $intent variable, parse failure, type-coercion
failure, etc.). UNKNOWN routes to UNCERTAIN at the DecisionEngine layer rather
than collapsing to FALSE.
"""

from __future__ import annotations

import ast
import operator
from enum import StrEnum
from pathlib import Path
from typing import Any

from lark import Lark, Token, Transformer
from loguru import logger
from pydantic import BaseModel, Field

from vigil.core.paths import resolve_dsl_grammar_path


class IntentContext(BaseModel):
    """Variables extracted from user instruction for guard binding.

    Attributes:
        raw_instruction: The original user instruction text.
        variables: Named values from the instruction (e.g., {"wifi_name": "HKU_WiFi"}).
    """

    raw_instruction: str = ""
    variables: dict[str, str] = Field(default_factory=dict)


class ScreenContext(BaseModel):
    """Runtime screen state for predicate evaluation.

    Attributes:
        elements: element_id → property dict (e.g., {"text": "HKU", "checked": True}).
        current_time: Current time as "HH:MM" for time_in predicates.
        current_state: Current FSM state name for in_state predicates.
    """

    elements: dict[str, dict[str, Any]] = Field(default_factory=dict)
    current_time: str | None = None
    current_state: str | None = None


class GuardStatus(StrEnum):
    """Three-valued evaluation result for a DSL guard or invariant."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class GuardResult(BaseModel):
    """Result of evaluating a DSL guard.

    Attributes:
        status: TRUE, FALSE, or UNKNOWN.
        passed: Backward-compat boolean (True iff status is TRUE).
        guard_expression: The original guard expression.
        bound_expression: The expression after $intent.* substitution.
        failure_reason: Why the guard failed or is unknown (empty if passed).
    """

    status: GuardStatus = GuardStatus.FALSE
    passed: bool = False
    guard_expression: str
    bound_expression: str
    failure_reason: str = ""


# Operator lookup
_OPS: dict[str, Any] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
}
_CONTAINMENT_OPS = frozenset({"contains", "not_contains"})


class _Unknown:
    """Sentinel value representing an inconclusive predicate result."""

    __slots__ = ("reason",)

    def __init__(self, reason: str = "") -> None:
        self.reason = reason

    def __bool__(self) -> bool:  # pragma: no cover — guarded by callers
        return False

    def __repr__(self) -> str:
        return f"<UNKNOWN: {self.reason}>"


def _is_unknown(v: Any) -> bool:
    return isinstance(v, _Unknown)


_STRUCTURAL_TOKENS = frozenset(
    {
        "read(",
        "value(",
        "time_in(",
        "in_state(",
        "contains(",
        "count(",
        "action(",
        ",",
        ")",
        "(",
        "&&",
        "||",
        "!",
    }
)


def _filter_named(args: list[Any]) -> list[Any]:
    """Filter out anonymous structural tokens, keeping only named tokens and results."""
    return [a for a in args if not (isinstance(a, Token) and str(a) in _STRUCTURAL_TOKENS)]


class _GuardEvaluator(Transformer):
    """Lark Transformer that evaluates a parsed guard tree against screen context.

    Each predicate handler returns either True, False, or `_Unknown(reason)`.
    Combiner rules (`guard`) implement three-valued logic:
      UNKNOWN && False -> False;  UNKNOWN && True -> UNKNOWN
      UNKNOWN || True  -> True;   UNKNOWN || False -> UNKNOWN
      !UNKNOWN -> UNKNOWN
    """

    def __init__(
        self,
        screen_ctx: ScreenContext,
        intent_ctx: IntentContext | None = None,
        action_ctx: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._ctx = screen_ctx
        self._intent = intent_ctx
        self._action = action_ctx

    def start(self, args: list[Any]) -> Any:
        return args[0]

    def guard(self, args: list[Any]) -> Any:
        token_strs = [str(a) for a in args if isinstance(a, Token)]
        filtered = _filter_named(args)

        if "!" in token_strs:
            if not filtered:
                return _Unknown("empty negation")
            v = filtered[0]
            if _is_unknown(v):
                return v
            return not bool(v)

        if "&&" in token_strs:
            if len(filtered) < 2:
                return _Unknown("malformed conjunction")
            a, b = filtered[0], filtered[1]
            # Three-valued AND
            if a is False or b is False:
                return False
            if _is_unknown(a) or _is_unknown(b):
                return _Unknown("conjunction has UNKNOWN operand")
            return bool(a) and bool(b)

        if "||" in token_strs:
            if len(filtered) < 2:
                return _Unknown("malformed disjunction")
            a, b = filtered[0], filtered[1]
            if a is True or b is True:
                return True
            if _is_unknown(a) or _is_unknown(b):
                return _Unknown("disjunction has UNKNOWN operand")
            return bool(a) or bool(b)

        # Single predicate or parenthesized guard
        return filtered[0] if filtered else _Unknown("empty guard")

    def predicate(self, args: list[Any]) -> Any:
        return args[0]

    def read_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if len(named) < 4:
            return _Unknown("malformed read predicate")
        element_name = str(named[0])
        prop_name = str(named[1])
        op_str = str(named[2])
        expected = self._parse_value(named[3])
        if _is_unknown(expected):
            return expected

        el = self._ctx.elements.get(element_name)
        if el is None:
            return _Unknown(f"element '{element_name}' not present on screen")
        if prop_name not in el:
            return _Unknown(f"property '{prop_name}' not readable on '{element_name}'")
        actual = el.get(prop_name)
        return self._compare(actual, op_str, expected)

    def value_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if len(named) < 3:
            return _Unknown("malformed value predicate")
        element_name = str(named[0])
        op_str = str(named[1])
        expected = self._parse_value(named[2])
        if _is_unknown(expected):
            return expected

        el = self._ctx.elements.get(element_name)
        if el is None:
            return _Unknown(f"element '{element_name}' not present on screen")
        if "value" not in el and "text" not in el and op_str not in _CONTAINMENT_OPS:
            return _Unknown(f"no value on '{element_name}'")
        actual = el.get("value", el.get("text", el))
        return self._compare(actual, op_str, expected)

    def time_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if len(named) < 2:
            return _Unknown("malformed time_in predicate")
        start_time = str(named[0])
        end_time = str(named[1])
        current = self._ctx.current_time
        if current is None:
            return _Unknown("current_time not provided")
        return start_time <= current <= end_time

    def state_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if not named:
            return _Unknown("malformed in_state predicate")
        expected_state = str(named[0])
        if self._ctx.current_state is None:
            return _Unknown("current_state not provided")
        return self._ctx.current_state == expected_state

    def contains_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if len(named) < 2:
            return _Unknown("malformed contains predicate")
        element_name = str(named[0])
        search_value = self._parse_value(named[1])
        if _is_unknown(search_value):
            return search_value

        el = self._ctx.elements.get(element_name)
        if el is None:
            return _Unknown(f"element '{element_name}' not present on screen")
        return self._contains(el, search_value)

    def count_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if len(named) < 3:
            return _Unknown("malformed count predicate")
        element_name = str(named[0])
        op_str = str(named[1])
        expected = self._parse_value(named[2])
        if _is_unknown(expected):
            return expected

        el = self._ctx.elements.get(element_name)
        if el is None:
            return _Unknown(f"element '{element_name}' not present on screen")
        if "children_count" not in el and "item_count" not in el:
            return _Unknown(f"no count on '{element_name}'")
        count = el.get("children_count", el.get("item_count", 0))
        return self._compare(count, op_str, expected)

    def action_pred(self, args: list[Any]) -> Any:
        named = _filter_named(args)
        if len(named) < 3:
            return _Unknown("malformed action predicate")
        prop_name = str(named[0])
        op_str = str(named[1])
        expected = self._parse_value(named[2])
        if _is_unknown(expected):
            return expected

        if self._action is None:
            return _Unknown("action_ctx not provided")
        if prop_name not in self._action:
            return _Unknown(f"action property '{prop_name}' missing")
        actual = self._action.get(prop_name)
        return self._compare(actual, op_str, expected)

    def _parse_value(self, token: Token | str) -> Any:
        """Parse a VALUE token into a Python value, resolving $intent.* from context.

        Returns `_Unknown(reason)` if an $intent variable is referenced but
        absent from the IntentContext (or no IntentContext was supplied).
        """
        s = str(token)
        if s.startswith("$intent."):
            var_name = s[len("$intent.") :]
            if self._intent is None or var_name not in self._intent.variables:
                return _Unknown(f"$intent.{var_name} not bound")
            return self._intent.variables[var_name]
        if s == "true":
            return True
        if s == "false":
            return False
        if s == "null":
            return None
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            try:
                return ast.literal_eval(s)
            except (SyntaxError, ValueError):
                return s[1:-1]
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    @staticmethod
    def _contains(actual: Any, expected: Any) -> Any:
        """Evaluate containment over strings, lists, or runtime element dictionaries."""
        if actual is None:
            return _Unknown("actual value is None")
        if isinstance(actual, dict):
            children = actual.get("children", [])
            if children:
                for child in children:
                    if isinstance(child, dict) and child.get("text") == expected:
                        return True
                    if child == expected:
                        return True
                return False
            text = actual.get("text", actual.get("value"))
            if text is None:
                return _Unknown("no text/value for containment")
            return str(expected) in str(text)
        if isinstance(actual, list):
            for item in actual:
                if isinstance(item, dict) and item.get("text") == expected:
                    return True
                if item == expected:
                    return True
            return False
        return str(expected) in str(actual)

    @classmethod
    def _compare(cls, actual: Any, op_str: str, expected: Any) -> Any:
        """Three-valued comparison with type coercion for numeric comparisons."""
        if op_str in _CONTAINMENT_OPS:
            result = cls._contains(actual, expected)
            if _is_unknown(result):
                return result
            return (not result) if op_str == "not_contains" else result
        op_func = _OPS.get(op_str)
        if op_func is None:
            return _Unknown(f"unsupported operator '{op_str}'")
        if actual is None:
            return _Unknown("actual value is None")
        if isinstance(expected, int | float) and isinstance(actual, str):
            try:
                actual = float(actual)
            except (ValueError, TypeError):
                return _Unknown("numeric coercion failed")
        try:
            return op_func(actual, expected)
        except TypeError:
            return _Unknown("type mismatch in comparison")


def _predicate_clause(
    predicate_type: str,
    text: str,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "node_type": "predicate",
        "predicate_type": predicate_type,
        "text": text,
        **fields,
    }


def _logic_text(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("text") or "")
    return str(node)


def _collect_predicate_clauses(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    if node.get("node_type") == "predicate":
        return [node]
    clauses: list[dict[str, Any]] = []
    for child in node.get("children", []):
        clauses.extend(_collect_predicate_clauses(child))
    return clauses


class _LogicClauseTransformer(Transformer):
    """Convert a parsed DSL tree into display-oriented logic clauses.

    This transformer intentionally does not evaluate predicates. It exposes the
    post-parser symbolic structure used by the runtime verifier so reports and
    visualizations can show clauses instead of raw JSON metadata.
    """

    def start(self, args: list[Any]) -> Any:
        return args[0]

    def guard(self, args: list[Any]) -> Any:
        token_strs = [str(a) for a in args if isinstance(a, Token)]
        filtered = _filter_named(args)

        if "!" in token_strs:
            child = filtered[0] if filtered else {}
            return {
                "node_type": "logic",
                "operator": "not",
                "children": [child],
                "text": f"NOT ({_logic_text(child)})",
            }

        if "&&" in token_strs:
            left = filtered[0] if filtered else {}
            right = filtered[1] if len(filtered) > 1 else {}
            return {
                "node_type": "logic",
                "operator": "and",
                "children": [left, right],
                "text": f"({_logic_text(left)}) AND ({_logic_text(right)})",
            }

        if "||" in token_strs:
            left = filtered[0] if filtered else {}
            right = filtered[1] if len(filtered) > 1 else {}
            return {
                "node_type": "logic",
                "operator": "or",
                "children": [left, right],
                "text": f"({_logic_text(left)}) OR ({_logic_text(right)})",
            }

        return filtered[0] if filtered else {}

    def predicate(self, args: list[Any]) -> Any:
        return _filter_named(args)[0]

    def read_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        element = str(named[0])
        prop = str(named[1])
        op = str(named[2])
        value = str(named[3])
        return _predicate_clause(
            "read",
            f"read({element}, {prop}) {op} {value}",
            element=element,
            property=prop,
            operator=op,
            value=value,
        )

    def value_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        element = str(named[0])
        op = str(named[1])
        value = str(named[2])
        return _predicate_clause(
            "value",
            f"value({element}) {op} {value}",
            element=element,
            operator=op,
            value=value,
        )

    def time_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        start_time = str(named[0])
        end_time = str(named[1])
        return _predicate_clause(
            "time_in",
            f"time_in({start_time}, {end_time})",
            start_time=start_time,
            end_time=end_time,
        )

    def state_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        state_name = str(named[0])
        return _predicate_clause(
            "in_state",
            f"in_state({state_name})",
            state_name=state_name,
        )

    def contains_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        element = str(named[0])
        value = str(named[1])
        return _predicate_clause(
            "contains",
            f"contains({element}, {value})",
            element=element,
            value=value,
        )

    def count_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        element = str(named[0])
        op = str(named[1])
        value = str(named[2])
        return _predicate_clause(
            "count",
            f"count({element}) {op} {value}",
            element=element,
            operator=op,
            value=value,
        )

    def action_pred(self, args: list[Any]) -> dict[str, Any]:
        named = _filter_named(args)
        prop = str(named[0])
        op = str(named[1])
        value = str(named[2])
        return _predicate_clause(
            "action",
            f"action({prop}) {op} {value}",
            property=prop,
            operator=op,
            value=value,
        )


class DSLEvaluator:
    """Evaluates DSL guard expressions against runtime context.

    Uses the packaged Lark grammar for parsing.
    Guard templates contain $intent.* placeholders resolved at runtime via
    INTENT_VAR tokens in the grammar.

    Args:
        grammar_path: Path to the .lark grammar file. Defaults to the packaged
            symbolic grammar, with fallback support for older project layouts.
    """

    def __init__(self, grammar_path: str | Path | None = None) -> None:
        path = resolve_dsl_grammar_path(grammar_path)
        grammar_text = path.read_text(encoding="utf-8")
        self._parser = Lark(grammar_text, parser="earley", start="start", keep_all_tokens=True)

    def evaluate(
        self,
        guard_expr: str,
        intent_ctx: IntentContext | None = None,
        screen_ctx: ScreenContext | None = None,
        action_ctx: dict[str, Any] | None = None,
    ) -> GuardResult:
        """Parse guard, resolve $intent.* variables, evaluate predicates."""
        if screen_ctx is None:
            screen_ctx = ScreenContext()
        return self._evaluate_with_context(
            guard_expr,
            intent_ctx=intent_ctx,
            screen_ctx=screen_ctx,
            action_ctx=action_ctx,
        )

    def _evaluate_with_context(
        self,
        guard_expr: str,
        intent_ctx: IntentContext | None = None,
        screen_ctx: ScreenContext | None = None,
        action_ctx: dict[str, Any] | None = None,
    ) -> GuardResult:
        if screen_ctx is None:
            screen_ctx = ScreenContext()

        bound_expr = self.bind_intent(guard_expr, intent_ctx) if intent_ctx else guard_expr

        try:
            tree = self._parser.parse(guard_expr)
        except Exception as e:
            logger.debug(f"Guard parse error: {e}")
            return GuardResult(
                status=GuardStatus.UNKNOWN,
                passed=False,
                guard_expression=guard_expr,
                bound_expression=bound_expr,
                failure_reason=f"Parse error: {e}",
            )

        try:
            evaluator = _GuardEvaluator(
                screen_ctx,
                intent_ctx=intent_ctx,
                action_ctx=action_ctx,
            )
            result = evaluator.transform(tree)
        except Exception as e:
            logger.debug(f"Guard evaluation error: {e}")
            return GuardResult(
                status=GuardStatus.UNKNOWN,
                passed=False,
                guard_expression=guard_expr,
                bound_expression=bound_expr,
                failure_reason=f"Evaluation error: {e}",
            )

        if _is_unknown(result):
            return GuardResult(
                status=GuardStatus.UNKNOWN,
                passed=False,
                guard_expression=guard_expr,
                bound_expression=bound_expr,
                failure_reason=f"Inconclusive: {result.reason}",
            )
        if result is True:
            return GuardResult(
                status=GuardStatus.TRUE,
                passed=True,
                guard_expression=guard_expr,
                bound_expression=bound_expr,
                failure_reason="",
            )
        return GuardResult(
            status=GuardStatus.FALSE,
            passed=False,
            guard_expression=guard_expr,
            bound_expression=bound_expr,
            failure_reason="Guard condition not satisfied",
        )

    def parse_logic_clauses(self, guard_expr: str) -> dict[str, Any]:
        """Parse a DSL expression into display-ready symbolic logic clauses.

        Unlike :meth:`evaluate`, this method does not require screen, intent, or
        action context. It exposes the parser-confirmed logical structure for
        reports and visualization sidebars.
        """
        try:
            tree = self._parser.parse(guard_expr)
        except Exception as e:
            logger.debug(f"Guard parse error: {e}")
            return {
                "status": "parse_error",
                "expression": guard_expr,
                "error": str(e),
                "root": None,
                "clauses": [],
            }

        try:
            root = _LogicClauseTransformer().transform(tree)
        except Exception as e:
            logger.debug(f"Guard clause transform error: {e}")
            return {
                "status": "parse_error",
                "expression": guard_expr,
                "error": str(e),
                "root": None,
                "clauses": [],
            }

        return {
            "status": "parsed",
            "expression": guard_expr,
            "root": root,
            "clauses": _collect_predicate_clauses(root),
        }

    @staticmethod
    def bind_intent(guard_expr: str, intent_ctx: IntentContext) -> str:
        """Replace $intent.* placeholders with quoted values from intent context."""
        import re

        pattern = re.compile(r"\$intent\.([a-zA-Z_][a-zA-Z0-9_]*)")

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            value = intent_ctx.variables.get(var_name, "")
            return f'"{value}"'

        return pattern.sub(_replace, guard_expr)
