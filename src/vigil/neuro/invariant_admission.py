"""Deterministic admission for state-invariant candidates (the executability gate).

A state-invariant *candidate* is a single DSL predicate string proposed by the LLM or the
deterministic synthesizer (e.g. ``value(total_amount) >= 0`` or
``value(title) contains "Payment successful"``). This module decides — deterministically,
with no LLM — whether that candidate may become a runtime
:class:`~vigil.models.fsm.StateInvariant` in ``AbstractState.invariant_specs``.

The runtime invariant checker (:class:`~vigil.symbolic.invariant_checker.InvariantChecker`)
evaluates invariants with a :class:`~vigil.symbolic.dsl_evaluator.ScreenContext` **only**
— no intent, no action, no current-state/-time. ``decision_engine._build_screen_context``
keys runtime elements by ``element_id`` and ``resource_id`` and the evaluator does exact
key lookup. Admission therefore enforces:

1. Single, parseable predicate (compound ``&&`` / ``||`` / ``!`` is rejected).
2. No ``$intent.*`` / ``$bind.*`` (intent-dependent) and no ``action(...)`` (action-dependent)
   — those become :class:`effect hints`, never runtime invariants.
3. No ``in_state(...)`` / ``time_in(...)`` — the runtime ScreenContext supplies neither
   ``current_state`` nor ``current_time``.
4. Element references lower to a runtime-resolvable full ``resource_id`` via the existing
   :func:`~vigil.neuro.guard_admission._resolve_element`. Unresolved / invented /
   static-prior-only aliases are rejected.
5. **Evidence replay** — the lowered predicate must evaluate ``TRUE`` against the
   :class:`ScreenContext` of *every* observed raw screen of the state (≥1 observation),
   using the existing :class:`DSLEvaluator`. This single gate enforces runtime-evidence
   support and volatility filtering at once: a value that changes across visits, an
   unstable list count, an empty field, or an element absent at runtime all fail to hold
   across observations and are rejected.

Reuses the guard path's parser, element resolution, readable-property set, literal type
checks, and the shared DSL compiler — it does not introduce a parallel guard/DSL system.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from lark import Token, Transformer
from lark.exceptions import LarkError
from pydantic import BaseModel

from vigil.models.fsm import StateInvariant
from vigil.models.guard import PredicateSpec, ValueRef
from vigil.neuro.guard_admission import (
    _READABLE_PROPS,
    _literal_type_error,
    _normalize_literal_value,
    _parser,
    _resolve_element,
)
from vigil.neuro.guard_dsl_compiler import compile_predicate_spec
from vigil.neuro.guard_registry import _as_dict
from vigil.symbolic.dsl_evaluator import DSLEvaluator, GuardStatus, ScreenContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.models.invariant_candidate import StateInvariantCandidate
    from vigil.neuro.invariant_evidence import InvariantEvidence


# Named terminals of the DSL grammar (everything else in a parsed predicate is structural
# punctuation we ignore).
_NAMED_TERMINALS: frozenset[str] = frozenset(
    {"ELEMENT", "PROPERTY", "OP", "VALUE", "STATE_NAME", "TIME"}
)
_COMBINATOR_TOKENS: frozenset[str] = frozenset({"&&", "||", "!"})
_CONTAINMENT_OPS: frozenset[str] = frozenset({"contains", "not_contains"})

# Boolean element properties exposed by the runtime screen context. Always populated so a
# ``read(x, is_enabled)`` predicate can evaluate rather than read UNKNOWN.
_BOOL_KEYS: tuple[str, ...] = (
    "is_clickable",
    "is_long_clickable",
    "is_checkable",
    "is_checked",
    "is_enabled",
    "is_editable",
    "is_scrollable",
    "is_focusable",
    "is_focused",
    "is_selected",
    "is_password",
)


@dataclass(frozen=True)
class _ParsedPredicate:
    """The extracted parts of a single DSL predicate."""

    kind: str  # read | value | contains | count | action | in_state | time_in
    element: str | None
    property: str | None
    operator: str | None
    value_token: str | None


_COMPOUND = object()  # sentinel: the expression is not a single predicate


def _named(args: list[Any]) -> list[Token]:
    return [a for a in args if isinstance(a, Token) and a.type in _NAMED_TERMINALS]


class _PredicateExtractor(Transformer[Any, Any]):
    """Lark transformer that returns the single predicate of an expr, or ``_COMPOUND``."""

    def read_pred(self, args: list[Any]) -> _ParsedPredicate:
        n = _named(args)
        return _ParsedPredicate("read", str(n[0]), str(n[1]), str(n[2]), str(n[3]))

    def value_pred(self, args: list[Any]) -> _ParsedPredicate:
        n = _named(args)
        return _ParsedPredicate("value", str(n[0]), None, str(n[1]), str(n[2]))

    def contains_pred(self, args: list[Any]) -> _ParsedPredicate:
        n = _named(args)
        return _ParsedPredicate("contains", str(n[0]), None, None, str(n[1]))

    def count_pred(self, args: list[Any]) -> _ParsedPredicate:
        n = _named(args)
        return _ParsedPredicate("count", str(n[0]), None, str(n[1]), str(n[2]))

    def action_pred(self, args: list[Any]) -> _ParsedPredicate:
        n = _named(args)
        return _ParsedPredicate("action", None, str(n[0]), str(n[1]), str(n[2]))

    def state_pred(self, args: list[Any]) -> _ParsedPredicate:
        n = _named(args)
        return _ParsedPredicate("in_state", None, None, None, str(n[0]) if n else None)

    def time_pred(self, args: list[Any]) -> _ParsedPredicate:
        return _ParsedPredicate("time_in", None, None, None, None)

    def predicate(self, args: list[Any]) -> Any:
        return args[0]

    def guard(self, args: list[Any]) -> Any:
        if any(isinstance(a, Token) and str(a) in _COMBINATOR_TOKENS for a in args):
            return _COMPOUND
        preds = [a for a in args if isinstance(a, _ParsedPredicate)]
        if len(preds) == 1:
            return preds[0]
        return _COMPOUND

    def start(self, args: list[Any]) -> Any:
        return args[0]


def extract_single_predicate(expr: str, grammar_path: str | None = None) -> _ParsedPredicate | None:
    """Parse ``expr`` and return its single predicate, or ``None`` if compound/invalid."""
    try:
        tree = _parser(grammar_path).parse(expr)
    except LarkError:
        return None
    try:
        result = _PredicateExtractor().transform(tree)
    except Exception:  # noqa: BLE001 - defensive: malformed tree -> not admissible
        return None
    return result if isinstance(result, _ParsedPredicate) else None


def _literal_from_token(token: str | None) -> Any:
    """Parse a DSL VALUE token into a Python literal."""
    s = (token or "").strip()
    if s.startswith('"') and s.endswith('"'):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        try:
            value = ast.literal_eval(s)
        except (SyntaxError, ValueError):
            return s[1:-1]
        return value if isinstance(value, str) else s[1:-1]
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "null":
        return None
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return s


def _predicate_spec_from_parsed(parsed: _ParsedPredicate, element_key: str) -> PredicateSpec | None:
    """Build a typed, resource-id-lowered :class:`PredicateSpec` for compilation/replay."""
    op = parsed.operator or "=="
    value = _literal_from_token(parsed.value_token)
    expected = ValueRef(kind="literal", value=value)
    if parsed.kind == "read":
        return PredicateSpec(
            predicate_type="read",
            element=element_key,
            property=parsed.property,
            operator=op,
            expected=expected,
        )
    if parsed.kind == "value":
        return PredicateSpec(
            predicate_type="value", element=element_key, operator=op, expected=expected
        )
    if parsed.kind == "contains":
        return PredicateSpec(predicate_type="contains", element=element_key, expected=expected)
    if parsed.kind == "count":
        return PredicateSpec(
            predicate_type="count", element=element_key, operator=op, expected=expected
        )
    return None


def _props_from_element(el: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    text = str(el.get("text") or "")
    props: dict[str, Any] = {
        "text": text,
        "content_description": str(el.get("content_description") or ""),
        # Runtime DecisionEngine._build_screen_context sets ``value = e.text or ""``: at
        # runtime ``value`` has TEXT semantics, never a separate raw ``value`` field. Mirror
        # that exactly so a ``{text, value}`` split cannot admit an invariant the live
        # verifier would read differently (e.g. text="Visible", value="42").
        "value": text,
        "class_name": str(el.get("class_name") or ""),
        "resource_id": str(el.get("resource_id") or ""),
    }
    for key in _BOOL_KEYS:
        # UIElement defaults (models/state.py): is_enabled=True, every other boolean False.
        # Mirror those so a compact observation omitting a flag replays like a live
        # RawScreen/UIElement (where the trace-to-model deserialization fills the default).
        props[key] = bool(el.get(key, key == "is_enabled"))

    children = el.get("children")
    if isinstance(children, list) and children:
        child_texts: list[dict[str, str]] = []
        for child in children:
            if isinstance(child, dict):
                ctext = child.get("text")
                if ctext:
                    child_texts.append({"text": str(ctext)})
            else:
                resolved = by_id.get(str(child))
                if resolved and resolved.get("text"):
                    child_texts.append({"text": str(resolved.get("text"))})
        props["children"] = child_texts
        props["children_count"] = len(children)
    # Mirror DecisionEngine._build_screen_context exactly: children_count comes ONLY from
    # real children (len), and item_count is NEVER populated at runtime. Reading a raw
    # children_count/item_count dict key would let a count() invariant admit offline yet read
    # UNKNOWN at runtime (the live ScreenContext carries neither key).
    return props


def _runtime_elements_of(screen_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Return elements in the same traversal domain as DecisionEngine runtime checks.

    Full raw screens carry ``elements``; compact test/provenance fixtures may only carry
    ``interactable_elements``. Prefer the full tree whenever available because duplicate
    resource-id binding at runtime is determined by the complete ``RawScreen.elements``
    traversal, not the guard registry's readable/actionable subset.
    """
    raw_elements = screen_dict.get("elements")
    if isinstance(raw_elements, list) and raw_elements:
        return [_as_dict(element) for element in raw_elements]
    return [_as_dict(element) for element in screen_dict.get("interactable_elements") or []]


def screen_context_from_raw(screen: Any) -> ScreenContext:
    """Build a runtime-shaped :class:`ScreenContext` from a raw trace screen dict.

    Mirrors ``decision_engine._build_screen_context`` keying: every element is keyed by
    its ``element_id`` and (when present) its full ``resource_id``, so a resource-id-lowered
    invariant predicate resolves exactly as it will at runtime.
    """
    screen_dict = _as_dict(screen)
    raw_elements = _runtime_elements_of(screen_dict)
    by_id = {str(e.get("element_id") or ""): e for e in raw_elements}
    elements: dict[str, dict[str, Any]] = {}
    for el in raw_elements:
        props = _props_from_element(el, by_id)
        element_id = str(el.get("element_id") or "")
        resource_id = str(el.get("resource_id") or "")
        if element_id:
            elements[element_id] = props
        if resource_id:
            # Match DecisionEngine._build_screen_context exactly: duplicate resource-id
            # keys are overwritten by later elements in traversal order. Admission
            # rejects duplicate-resource predicates before replay, but keeping parity
            # here prevents any future caller from proving against different binding
            # semantics than runtime uses.
            elements[resource_id] = props
    return ScreenContext(elements=elements)


def _duplicate_resource_id_screens(
    observations: list[dict[str, Any]], resource_id: str
) -> list[str]:
    """Return screen ids where ``resource_id`` is not a unique element key."""
    duplicate_screens: list[str] = []
    for observation in observations:
        count = 0
        for element in _runtime_elements_of(_as_dict(observation)):
            if str(element.get("resource_id") or "") == resource_id:
                count += 1
                if count > 1:
                    duplicate_screens.append(str(observation.get("screen_id") or "<unknown>"))
                    break
    return duplicate_screens


class InvariantAdmissionResult(BaseModel):
    """Outcome of admitting one state-invariant candidate."""

    admitted: bool
    classification: str  # runtime_state_invariant | effect_hint | rejected
    invariant: StateInvariant | None = None
    reason: str = ""
    hint_reason: str = ""  # why_not_runtime_state_invariant, for effect_hint
    lowered_expr: str = ""


@lru_cache(maxsize=8)
def _default_evaluator(grammar_path: str | None = None) -> DSLEvaluator:
    return DSLEvaluator(grammar_path)


def _admitted_confidence(evidence_count: int) -> float:
    """Deterministic confidence from evidence strength (multi-visit is stronger)."""
    if evidence_count <= 1:
        return 0.5
    return min(0.5 + 0.1 * (evidence_count - 1), 0.9)


def _admitted_source(candidate_source: str, evidence_count: int) -> str:
    source = candidate_source or "llm"
    if evidence_count >= 2 and "cross_visit" not in source:
        source = f"{source}+cross_visit"
    return source


def _reject(reason: str) -> InvariantAdmissionResult:
    return InvariantAdmissionResult(admitted=False, classification="rejected", reason=reason)


def _hint(why: str, reason: str, lowered_expr: str = "") -> InvariantAdmissionResult:
    return InvariantAdmissionResult(
        admitted=False,
        classification="effect_hint",
        reason=reason,
        hint_reason=why,
        lowered_expr=lowered_expr,
    )


def admit_state_invariant_candidate(
    candidate: StateInvariantCandidate,
    evidence: InvariantEvidence,
    *,
    grammar_path: str | None = None,
    evaluator: DSLEvaluator | None = None,
    min_runtime_observations: int = 2,
) -> InvariantAdmissionResult:
    """Admit (or reject / route to hint) one state-invariant candidate deterministically."""
    expr = (candidate.expr or "").strip()
    if not expr:
        return _reject("empty invariant expression")

    # Intent/bind dependence -> effect hint, never a runtime state invariant.
    if "$intent." in expr or "$bind." in expr:
        return _hint("depends_on_intent", "references $intent.* / $bind.* (intent-dependent)")

    parsed = extract_single_predicate(expr, grammar_path)
    if parsed is None:
        return _reject("not a single parseable DSL predicate")

    if parsed.kind == "action":
        return _hint("depends_on_action", "action(...) is not a state-invariant fact")
    if parsed.kind in ("in_state", "time_in"):
        return _hint(
            "unsupported_predicate",
            f"{parsed.kind}(...) needs runtime context the invariant checker does not supply",
        )
    if parsed.value_token and parsed.value_token.startswith("$intent."):
        return _hint("depends_on_intent", "compares against an intent slot")

    # Lower the element alias to a runtime-resolvable full resource_id.
    if not parsed.element:
        return _reject(f"{parsed.kind} predicate missing element")
    key, entry, reason = _resolve_element(parsed.element, evidence.arrival_registry)
    if key is None:
        return _reject(f"element not runtime-resolvable ({reason})")

    spec = _predicate_spec_from_parsed(parsed, key)
    if spec is None:
        return _reject(f"unsupported predicate kind {parsed.kind!r}")

    if parsed.kind == "read":
        if not parsed.property or parsed.property not in _READABLE_PROPS:
            return _reject(f"property {parsed.property!r} is not runtime-readable")
        if parsed.operator in _CONTAINMENT_OPS and parsed.property not in {
            "text",
            "content_description",
            "value",
            "class_name",
            "resource_id",
        }:
            return _reject(
                f"containment operator is not valid for non-string property {parsed.property!r}"
            )
        expected = _normalize_literal_value("read", parsed.property, spec.expected)
        if expected is not spec.expected:
            spec = spec.model_copy(update={"expected": expected})
        if spec.expected is not None and spec.expected.kind == "literal":
            type_error = _literal_type_error("read", parsed.property, spec.expected.value)
            if type_error:
                return _reject(type_error)
    elif parsed.kind == "count" and spec.expected is not None and spec.expected.kind == "literal":
        if parsed.operator in _CONTAINMENT_OPS:
            return _reject("containment operator is not valid for count(...)")
        type_error = _literal_type_error("count", "", spec.expected.value)
        if type_error:
            return _reject(type_error)

    lowered = compile_predicate_spec(spec)
    if lowered is None:
        return _reject("predicate could not be compiled to DSL")

    # Evidence replay: the fact must hold across every observed screen of the state.
    observations = evidence.observations
    if not observations:
        return _reject("no runtime observations support this state")
    duplicate_screens = _duplicate_resource_id_screens(observations, key)
    if duplicate_screens:
        return _reject(
            f"resource_id {key!r} is not unique in observation(s) {duplicate_screens}; "
            "single-element runtime invariant would bind ambiguously"
        )

    evaluator = evaluator or _default_evaluator(grammar_path)
    supported = 0
    for observation in observations:
        screen_ctx = screen_context_from_raw(observation)
        result = evaluator.evaluate(lowered, screen_ctx=screen_ctx)
        if result.status is GuardStatus.TRUE:
            supported += 1
            continue
        return _reject(
            f"not supported by all observations: {result.status.value}"
            f" — {result.failure_reason or 'condition not satisfied'}"
        )

    if supported < min_runtime_observations:
        return _hint(
            "insufficient_evidence",
            f"only {supported} observation(s) support this candidate; "
            f"requires at least {min_runtime_observations} for a runtime state invariant",
            lowered,
        )

    invariant = StateInvariant(
        expr=lowered,
        confidence=_admitted_confidence(supported),
        source=_admitted_source(candidate.source, supported),
        evidence_count=supported,
    )
    return InvariantAdmissionResult(
        admitted=True,
        classification="runtime_state_invariant",
        invariant=invariant,
        reason=f"admitted: holds across {supported} observation(s)",
        lowered_expr=lowered,
    )
