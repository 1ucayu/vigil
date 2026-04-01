"""DSL guard data structures for semantic verification.

Guards are expressions in a formal grammar (defined in docs/dsl_grammar.lark)
that annotate FSM transitions with runtime-checkable conditions.
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
    """Types of DSL predicates."""

    READ = "read"
    TIME_IN = "time_in"
    IN_STATE = "in_state"
    VALUE = "value"


class DSLPredicate(BaseModel):
    """A single predicate in a DSL guard expression.

    Examples:
        read(amount_field, value) > 0
        time_in(09:00, 17:00)
        in_state(PaymentConfirm)
        value(recipient_field) != ""
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

    Attributes:
        raw_expression: The original guard string (e.g., "read(x, y) > 0 && in_state(Foo)").
        predicates: Flat list of predicates in the expression.
        is_negated: Whether the entire guard is negated.
        transition_source: Source state this guard is attached to.
        transition_target: Target state this guard is attached to.
    """

    raw_expression: str
    predicates: list[DSLPredicate] = Field(default_factory=list)
    is_negated: bool = False
    transition_source: str | None = None
    transition_target: str | None = None

    def evaluate(self, context: dict[str, Any]) -> bool:
        """Evaluate this guard against a runtime context (screen state).

        Args:
            context: Dictionary mapping element.property paths to runtime values.

        Returns:
            True if the guard condition is satisfied, False otherwise.
        """
        raise NotImplementedError
