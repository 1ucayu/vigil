"""Tier 2: DSL Semantic Verification (< 15 ms).

Evaluates DSL guard expressions against the current screen state using the Lark
parser (docs/dsl_grammar.lark). Guard templates are cached offline; parameters
are bound at runtime from user intent.
"""

from __future__ import annotations

import operator
import re
from pathlib import Path
from typing import Any

from lark import Lark, Token, Transformer
from loguru import logger
from pydantic import BaseModel, Field

# Default grammar path (relative to project root)
_DEFAULT_GRAMMAR = Path(__file__).parent.parent.parent.parent / "docs" / "dsl_grammar.lark"


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


class GuardResult(BaseModel):
    """Result of evaluating a DSL guard.

    Attributes:
        passed: Whether the guard evaluated to True.
        guard_expression: The original guard expression.
        bound_expression: The expression after $intent.* substitution.
        failure_reason: Why the guard failed (empty if passed).
    """

    passed: bool
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

# Pattern for $intent.variable_name placeholders (used by bind_intent for display)
_INTENT_PATTERN = re.compile(r"\$intent\.([a-zA-Z_][a-zA-Z0-9_]*)")


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

    Uses keep_all_tokens=True so we can distinguish && from ||.
    Structural tokens (parentheses, commas) are filtered in each handler.
    $intent.* tokens (INTENT_VAR) are resolved from intent_ctx during evaluation.
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

    def start(self, args: list[Any]) -> bool:
        return args[0]

    def guard(self, args: list[Any]) -> bool:
        # Check for && / || / ! among the raw tokens
        token_strs = [str(a) for a in args if isinstance(a, Token)]

        if "!" in token_strs:
            # "!" predicate — negate the predicate result
            filtered = _filter_named(args)
            return not bool(filtered[0]) if filtered else False

        if "&&" in token_strs:
            filtered = _filter_named(args)
            return bool(filtered[0]) and bool(filtered[1])

        if "||" in token_strs:
            filtered = _filter_named(args)
            return bool(filtered[0]) or bool(filtered[1])

        # Single predicate or parenthesized guard
        filtered = _filter_named(args)
        return bool(filtered[0]) if filtered else False

    def predicate(self, args: list[Any]) -> bool:
        return args[0]

    def read_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        # named = [ELEMENT, PROPERTY, OP, VALUE]
        if len(named) < 4:
            return False
        element_name = str(named[0])
        prop_name = str(named[1])
        op_str = str(named[2])
        expected = self._parse_value(named[3])

        actual = self._read_element(element_name, prop_name)
        if actual is None:
            return False
        return self._compare(actual, op_str, expected)

    def value_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        # named = [ELEMENT, OP, VALUE]
        if len(named) < 3:
            return False
        element_name = str(named[0])
        op_str = str(named[1])
        expected = self._parse_value(named[2])

        actual = self._read_element(element_name, "value")
        if actual is None:
            return False
        return self._compare(actual, op_str, expected)

    def time_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        if len(named) < 2:
            return False
        start_time = str(named[0])
        end_time = str(named[1])
        current = self._ctx.current_time
        if current is None:
            return False
        return start_time <= current <= end_time

    def state_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        if not named:
            return False
        expected_state = str(named[0])
        return self._ctx.current_state == expected_state

    def contains_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        # named = [ELEMENT, VALUE]
        if len(named) < 2:
            return False
        element_name = str(named[0])
        search_value = self._parse_value(named[1])

        el = self._ctx.elements.get(element_name)
        if el is None:
            return False
        # Check children list first (for list containers)
        children = el.get("children", [])
        if children:
            return any(child.get("text") == search_value for child in children)
        # Fallback: check element's own text content
        text = el.get("text", "")
        return str(search_value) in text

    def count_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        # named = [ELEMENT, OP, VALUE]
        if len(named) < 3:
            return False
        element_name = str(named[0])
        op_str = str(named[1])
        expected = self._parse_value(named[2])

        el = self._ctx.elements.get(element_name)
        if el is None:
            return False
        count = el.get("children_count", el.get("item_count", 0))
        return self._compare(count, op_str, expected)

    def action_pred(self, args: list[Any]) -> bool:
        named = _filter_named(args)
        # named = [PROPERTY, OP, VALUE]
        if len(named) < 3:
            return False
        prop_name = str(named[0])
        op_str = str(named[1])
        expected = self._parse_value(named[2])

        if self._action is None:
            return False
        actual = self._action.get(prop_name)
        if actual is None:
            return False
        return self._compare(actual, op_str, expected)

    def _read_element(self, element_name: str, prop_name: str) -> Any:
        """Look up an element property from screen context."""
        el = self._ctx.elements.get(element_name)
        if el is None:
            return None
        return el.get(prop_name)

    def _parse_value(self, token: Token | str) -> Any:
        """Parse a VALUE token into a Python value, resolving $intent.* from context."""
        s = str(token)
        # Resolve $intent.* variables from intent context
        if s.startswith("$intent."):
            var_name = s[len("$intent.") :]
            if self._intent:
                return self._intent.variables.get(var_name, "")
            return ""
        if s == "true":
            return True
        if s == "false":
            return False
        if s == "null":
            return None
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    @staticmethod
    def _compare(actual: Any, op_str: str, expected: Any) -> bool:
        """Compare with type coercion for numeric comparisons."""
        op_func = _OPS.get(op_str)
        if op_func is None:
            return False
        if isinstance(expected, int | float) and isinstance(actual, str):
            try:
                actual = float(actual)
            except (ValueError, TypeError):
                return False
        try:
            return op_func(actual, expected)
        except TypeError:
            return False


class DSLEvaluator:
    """Evaluates DSL guard expressions against runtime context.

    Uses the Lark grammar at docs/dsl_grammar.lark for parsing.
    Guard templates contain $intent.* placeholders resolved at runtime via
    INTENT_VAR tokens in the grammar.

    Args:
        grammar_path: Path to the .lark grammar file. Defaults to docs/dsl_grammar.lark.
    """

    def __init__(self, grammar_path: str | Path | None = None) -> None:
        path = Path(grammar_path) if grammar_path else _DEFAULT_GRAMMAR
        grammar_text = path.read_text(encoding="utf-8")
        self._parser = Lark(grammar_text, parser="earley", start="start", keep_all_tokens=True)

    def evaluate(
        self,
        guard_expr: str,
        intent_ctx: IntentContext | None = None,
        screen_ctx: ScreenContext | None = None,
        action_ctx: dict[str, Any] | None = None,
    ) -> GuardResult:
        """Parse guard, resolve $intent.* variables, evaluate predicates.

        Args:
            guard_expr: The DSL guard expression string.
            intent_ctx: User intent variables for $intent.* resolution.
            screen_ctx: Runtime screen state for predicate evaluation.
            action_ctx: Proposed action metadata for action_pred evaluation.

        Returns:
            GuardResult with evaluation outcome.
        """
        if screen_ctx is None:
            screen_ctx = ScreenContext()

        # Compute bound expression for display (string substitution)
        bound_expr = self.bind_intent(guard_expr, intent_ctx) if intent_ctx else guard_expr

        # Parse the original expression (INTENT_VAR resolved during evaluation)
        try:
            tree = self._parser.parse(guard_expr)
        except Exception as e:
            logger.debug(f"Guard parse error: {e}")
            return GuardResult(
                passed=False,
                guard_expression=guard_expr,
                bound_expression=bound_expr,
                failure_reason=f"Parse error: {e}",
            )

        # Evaluate with all contexts
        try:
            evaluator = _GuardEvaluator(screen_ctx, intent_ctx=intent_ctx, action_ctx=action_ctx)
            result = evaluator.transform(tree)
            passed = bool(result)
        except Exception as e:
            logger.debug(f"Guard evaluation error: {e}")
            return GuardResult(
                passed=False,
                guard_expression=guard_expr,
                bound_expression=bound_expr,
                failure_reason=f"Evaluation error: {e}",
            )

        return GuardResult(
            passed=passed,
            guard_expression=guard_expr,
            bound_expression=bound_expr,
            failure_reason="" if passed else "Guard condition not satisfied",
        )

    @staticmethod
    def bind_intent(guard_expr: str, intent_ctx: IntentContext) -> str:
        """Replace $intent.* placeholders with quoted values from intent context.

        Used for computing the bound_expression display string. The actual
        evaluation resolves INTENT_VAR tokens directly from the parse tree.

        Args:
            guard_expr: Guard expression with $intent.* placeholders.
            intent_ctx: Context containing variable values.

        Returns:
            Expression with placeholders replaced by quoted string literals.
        """

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            value = intent_ctx.variables.get(var_name, "")
            return f'"{value}"'

        return _INTENT_PATTERN.sub(_replace, guard_expr)
