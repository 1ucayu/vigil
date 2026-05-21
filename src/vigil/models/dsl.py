"""DSL guard data structures for semantic verification.

NOTE: The DSLGuard and DSLPredicate models below are structural definitions
only. Runtime guard evaluation is handled by DSLEvaluator (Lark Transformer)
in vigil.symbolic.dsl_evaluator, which parses guard strings directly and
does NOT use these models. These models may be useful for static analysis,
serialization, or IDE tooling in the future.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ComparisonOp(StrEnum):
    """Comparison operators for DSL predicates."""

    EQ = "=="
    NEQ = "!="
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="


class LogicalOp(StrEnum):
    """Logical connectives for combining predicates."""

    AND = "&&"
    OR = "||"
    NOT = "!"


class PredicateType(StrEnum):
    """Types of DSL predicates (aligned with output_docs/dsl_grammar.lark)."""

    READ = "read"
    VALUE = "value"
    TIME_IN = "time_in"
    IN_STATE = "in_state"
    CONTAINS = "contains"
    COUNT = "count"
    ACTION = "action"


class DSLPredicate(BaseModel):
    """A single predicate in a DSL guard expression.

    Examples:
        read(amount_field, value) > 0
        time_in(09:00, 17:00)
        in_state(PaymentConfirm)
        value(recipient_field) != ""
        contains(wifi_list, $intent.network_name)
        count(recycler_view) >= 1
        action(target_text) == $intent.menu_item
    """

    predicate_type: PredicateType
    element: str | None = None
    property: str | None = None
    operator: ComparisonOp | None = None
    value: str | int | float | bool | None = None
    time_start: str | None = None
    time_end: str | None = None
    state_name: str | None = None


class DSLGuard(BaseModel):
    """A parsed DSL guard expression.

    Represents the full guard as a tree of predicates connected by logical operators.
    Raw expression is preserved for serialization; parsed tree is used for evaluation.
    """

    raw_expression: str
    predicates: list[DSLPredicate] = Field(default_factory=list)
    is_negated: bool = False
    transition_source: str | None = None
    transition_target: str | None = None

    def evaluate(self, context: dict[str, Any]) -> bool:
        """Not implemented — use DSLEvaluator.evaluate() instead.

        Runtime guard evaluation is handled by
        vigil.symbolic.dsl_evaluator.DSLEvaluator, which uses a Lark
        Transformer for direct parse-tree evaluation.

        Raises:
            NotImplementedError: Always. Use DSLEvaluator instead.
        """
        raise NotImplementedError(
            "Use vigil.symbolic.dsl_evaluator.DSLEvaluator.evaluate() for guard evaluation"
        )
