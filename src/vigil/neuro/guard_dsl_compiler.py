"""Deterministic ``GuardContract`` / ``PredicateSpec`` → DSL string compiler (step 4).

This is a *pure structural renderer*: it turns typed predicates into the current DSL
grammar (``output_docs/dsl_grammar.lark``) with no knowledge of the widget registry or
the runtime context. Element and action-property references are emitted verbatim;
lowering them to runtime-resolvable keys and deciding executability is the job of
:mod:`vigil.neuro.guard_admission`.

No LLM, no grammar expansion. Output style matches the gold artifacts
(``fidelity_app/*/gold/guards.json``): spaced operators, JSON-quoted string literals,
``$intent.x`` variables.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vigil.models.guard import EffectRequirement, GuardContract, PredicateSpec, ValueRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

_DEFAULT_OP = "=="
_VALID_OPS: frozenset[str] = frozenset(
    {"==", "!=", ">", "<", ">=", "<=", "contains", "not_contains"}
)
_CONTAINMENT_OPS: frozenset[str] = frozenset({"contains", "not_contains"})


def _render_value(ref: ValueRef | None) -> str | None:
    """Render the right-hand VALUE token, or ``None`` if it cannot be expressed."""
    if ref is None:
        return None
    if ref.kind == "literal":
        value = ref.value
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        return json.dumps(str(value))
    if ref.kind == "intent":
        if not ref.slot:
            return None
        return f"$intent.{ref.slot}"
    # kind == "action" / "read": a VALUE token cannot be action(...)/read(...) on the
    # right-hand side of the current grammar — drop conservatively.
    return None


def _op(predicate: PredicateSpec) -> str | None:
    op = predicate.operator or _DEFAULT_OP
    return op if op in _VALID_OPS else None


def compile_predicate_spec(predicate: PredicateSpec) -> str | None:
    """Compile one :class:`PredicateSpec` to a DSL predicate, or ``None``.

    Returns ``None`` when a required part is missing or the predicate cannot be expressed
    in the current grammar (e.g. an ``action``/``read`` value on the right-hand side).
    """
    ptype = predicate.predicate_type

    if ptype == "read":
        if not predicate.element or not predicate.property:
            return None
        op = _op(predicate)
        value = _render_value(predicate.expected)
        if op is None or value is None:
            return None
        return f"read({predicate.element}, {predicate.property}) {op} {value}"

    if ptype == "value":
        if not predicate.element:
            return None
        op = _op(predicate)
        value = _render_value(predicate.expected)
        if op is None or value is None:
            return None
        return f"value({predicate.element}) {op} {value}"

    if ptype == "action":
        if not predicate.property:
            return None
        op = _op(predicate)
        value = _render_value(predicate.expected)
        if op is None or value is None:
            return None
        return f"action({predicate.property}) {op} {value}"

    if ptype == "contains":
        value = _render_value(predicate.expected)
        if not predicate.element or value is None:
            return None
        op = predicate.operator if predicate.operator in _CONTAINMENT_OPS else "contains"
        return f"value({predicate.element}) {op} {value}"

    if ptype == "count":
        if not predicate.element:
            return None
        op = _op(predicate)
        value = _render_value(predicate.expected)
        if op is None or value is None:
            return None
        return f"count({predicate.element}) {op} {value}"

    if ptype == "in_state":
        name = predicate.args.get("state")
        if name is None and predicate.expected is not None:
            name = predicate.expected.value
        if not name:
            return None
        return f"in_state({name})"

    if ptype == "time_in":
        start = predicate.args.get("start")
        end = predicate.args.get("end")
        if not start or not end:
            return None
        return f"time_in({start}, {end})"

    if ptype in {"appeared", "disappeared", "value_changed"}:
        return None

    return None


def compile_guard_contract(contract: GuardContract) -> str | None:
    """Compile a :class:`GuardContract` to a single DSL string, or ``None``.

    Predicates that cannot be expressed are dropped. If no predicate compiles (including
    the common case of an optional contract with no predicates) the result is ``None``.
    """
    compiled = [
        rendered
        for predicate in contract.predicates
        if (rendered := compile_predicate_spec(predicate)) is not None
    ]
    if not compiled:
        return None
    return " && ".join(compiled)


def compile_effect_requirement(effect: EffectRequirement) -> str | None:
    """Return ``None`` because post-arrival effects are audit metadata, not DSL."""
    return None
