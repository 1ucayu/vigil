"""Admission validation for compiled guard contracts (step 4) — the executability gate.

Admission decides whether a synthesized :class:`~vigil.models.guard.GuardContract` may be
attached to a transition as an executable guard. Unlike a pure grammar check, admission
guarantees the resulting DSL is **runtime-executable by the current**
:class:`~vigil.symbolic.decision_engine.DecisionEngine` context:

- Element references are lowered to a stable, offline-known, runtime-resolvable key — the
  full ``resource_id``. Registry-only slug aliases and synthesized ``Class_idx`` aliases
  (whose live index is not knowable offline) are rejected rather than guessed.
- Action properties are restricted to what the runtime action context actually exposes
  (``action_type``/``target_text``/``target_resource_id``/``target_content_desc``), with
  ``type`` normalized to ``action_type``. ``action(text)``/``action(value)`` are rejected.
- ``$intent.*`` slots must be declared in ``contract.required_slots``.
- Generic exploration input placeholders are rejected as guard literals; they are trace
  evidence that a field accepts input, not task intent.
- Required semantic guard kinds must contain at least one executable ``$intent.*`` binding
  predicate. Action-identity or enabledness-only candidates are marked ``LOW_TRUST`` and
  are not attached.

Anything that cannot be guaranteed executable is ``REJECTED`` or ``LOW_TRUST`` — never
attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from lark import Lark
from lark.exceptions import LarkError
from pydantic import BaseModel, Field

from vigil.core.paths import resolve_dsl_grammar_path
from vigil.models.guard import (
    GuardAdmissionStatus,
    GuardContract,
    GuardKind,
    PredicateSpec,
    ValueRef,
)
from vigil.neuro.exploration_inputs import is_exploration_synthetic_input
from vigil.neuro.guard_dsl_compiler import compile_predicate_spec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.neuro.guard_evidence import GuardEvidence
    from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry


# Element properties the runtime evaluator can read (decision_engine._build_screen_context).
_READABLE_PROPS: frozenset[str] = frozenset(
    {
        "text",
        "content_description",
        "value",
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
        "class_name",
        "resource_id",
        "children",
        "children_count",
        "item_count",
    }
)

_READABLE_BOOL_PROPS: frozenset[str] = frozenset(
    {
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
    }
)

_READABLE_STRING_PROPS: frozenset[str] = frozenset(
    {"text", "content_description", "value", "class_name", "resource_id"}
)

_READABLE_NUMERIC_PROPS: frozenset[str] = frozenset({"children_count", "item_count"})

# Action properties the runtime action context exposes (decision_engine._build_action_context).
_ALLOWED_ACTION_PROPS: frozenset[str] = frozenset(
    {
        "action_type",
        "target_text",
        "target_resource_id",
        "target_content_desc",
        "input_text",
    }
)
_CONTAINMENT_OPS: frozenset[str] = frozenset({"contains", "not_contains"})
_VALID_ACTION_TYPE_LITERALS: frozenset[str] = frozenset(
    {
        "click",
        "long_press",
        "input_text",
        "scroll_up",
        "scroll_down",
        "navigate_back",
        "navigate_home",
        "swipe",
        "scroll",
    }
)
_SEMANTIC_GUARD_KINDS: frozenset[GuardKind] = frozenset(
    {
        GuardKind.ITEM_BINDING,
        GuardKind.INPUT_BINDING,
        GuardKind.FORM_CHECK,
        GuardKind.CONFIRM_COMMIT,
        GuardKind.SAFETY_CHECK,
    }
)
_LLM_LEAKAGE_MARKERS: tuple[str, ...] = (
    "to=final",
    "LlmTransitionGuardResponse",
    "malformed output",
    "code omitted",
    "```",
)

# Registry-stored string properties usable for offline literal proof-of-false.
_KNOWN_STRING_PROPS: tuple[str, ...] = (
    "text",
    "content_description",
    "resource_id",
    "class_name",
)


def _literal_type_error(ptype: str, prop: str, value: object) -> str:
    """Return an admission rejection reason for literal/property type mismatch."""
    if ptype == "read":
        if prop in _READABLE_BOOL_PROPS and not isinstance(value, bool):
            return f"literal for boolean property '{prop}' must be JSON boolean"
        if prop in _READABLE_NUMERIC_PROPS and (
            isinstance(value, bool) or not isinstance(value, int | float)
        ):
            return f"literal for numeric property '{prop}' must be JSON number"
        if prop in _READABLE_STRING_PROPS and not isinstance(value, str):
            return f"literal for string property '{prop}' must be JSON string"
    if ptype == "action" and prop in _ALLOWED_ACTION_PROPS and not isinstance(value, str):
        return f"literal for action property '{prop}' must be JSON string"
    if ptype == "count" and (isinstance(value, bool) or not isinstance(value, int | float)):
        return "literal for count predicate must be JSON number"
    return ""


def _literal_content_error(ptype: str, prop: str, value: object) -> str:
    """Reject string literals that are syntactically valid but semantically unusable."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if is_exploration_synthetic_input(text):
        return (
            f"literal {text!r} is an exploration synthetic input placeholder; "
            "use a declared intent slot instead"
        )
    if any(marker in text for marker in _LLM_LEAKAGE_MARKERS):
        return "literal appears to contain LLM/tool-output leakage"
    if ptype == "action" and prop == "action_type" and text not in _VALID_ACTION_TYPE_LITERALS:
        return f"action_type literal {text!r} is not a valid runtime action type"
    return ""


def _normalize_literal_value(ptype: str, prop: str, expected: ValueRef | None) -> ValueRef | None:
    """Normalize narrow LLM JSON literal slips that preserve the typed meaning."""
    if expected is None or expected.kind != "literal":
        return expected
    value = expected.value
    if ptype == "read" and prop in _READABLE_BOOL_PROPS and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "false"}:
            return expected.model_copy(update={"value": normalized == "true"})
    return expected


class GuardAdmissionResult(BaseModel):
    """Outcome of admitting a guard contract."""

    admitted: bool
    status: GuardAdmissionStatus
    guard: str | None = None
    reason: str = ""
    rejected_predicates: list[str] = Field(default_factory=list)
    semantic_binding_required: bool = False
    semantic_binding_incomplete: bool = False


@lru_cache(maxsize=8)
def _parser(grammar_path: str | None = None) -> Lark:
    path = resolve_dsl_grammar_path(grammar_path)
    grammar_text = path.read_text(encoding="utf-8")
    return Lark(grammar_text, parser="earley", start="start", keep_all_tokens=True)


@dataclass(frozen=True)
class _Lowered:
    """Result of lowering one predicate: a compiled string, or a rejection reason."""

    compiled: str | None
    reason: str
    is_binding: bool = False


def _known_string_props(entry: WidgetRegistryEntry) -> dict[str, str]:
    out: dict[str, str] = {}
    for prop in _KNOWN_STRING_PROPS:
        value = getattr(entry, prop, "")
        if value:
            out[prop] = str(value)
    return out


def _resolve_element(
    element: str, registry: WidgetRegistry, registry_name: str = "source"
) -> tuple[str | None, WidgetRegistryEntry | None, str]:
    """Lower an element reference to a runtime-resolvable key (full ``resource_id``)."""
    entry = registry.entries.get(element)
    if entry is not None:
        if entry.resource_id:
            return entry.resource_id, entry, ""
        return (
            None,
            None,
            f"element '{element}' has no resource_id (no runtime-resolvable key)",
        )
    if element in registry.resource_id_to_alias:
        # Already a known resource_id — use verbatim.
        alias = registry.resource_id_to_alias[element]
        return element, registry.entries.get(alias), ""
    return None, None, f"element '{element}' not present in {registry_name} registry"


def _lower_predicate(
    pred: PredicateSpec, registry: WidgetRegistry, declared_slots: set[str]
) -> _Lowered:
    ptype = pred.predicate_type
    expected = pred.expected
    is_binding = bool(expected and expected.kind == "intent")

    # Intent slot must be declared in the contract.
    if (
        expected is not None
        and expected.kind == "intent"
        and (not expected.slot or expected.slot not in declared_slots)
    ):
        return _Lowered(None, f"undeclared intent slot '{expected.slot}'")

    lowered = pred

    if ptype in ("read", "value", "contains", "count"):
        if not pred.element:
            return _Lowered(None, f"{ptype} predicate missing element")
        key, entry, reason = _resolve_element(pred.element, registry)
        if key is None:
            return _Lowered(None, reason)
        op = pred.operator or ("contains" if ptype == "contains" else "==")
        if ptype == "count" and expected is not None and expected.kind == "literal":
            if op in _CONTAINMENT_OPS:
                return _Lowered(None, "containment operator is not valid for count(...)")
            type_error = _literal_type_error(ptype, "", expected.value)
            if type_error:
                return _Lowered(None, type_error)
            content_error = _literal_content_error(ptype, "", expected.value)
            if content_error:
                return _Lowered(None, content_error)
        if ptype == "read":
            if not pred.property or pred.property not in _READABLE_PROPS:
                return _Lowered(None, f"property '{pred.property}' not runtime-readable")
            if op in _CONTAINMENT_OPS and pred.property not in _READABLE_STRING_PROPS:
                return _Lowered(
                    None,
                    f"containment operator is not valid for non-string property '{pred.property}'",
                )
            expected = _normalize_literal_value(ptype, pred.property, expected)
            if expected is not pred.expected:
                pred = pred.model_copy(update={"expected": expected})
            if expected is not None and expected.kind == "literal":
                type_error = _literal_type_error(ptype, pred.property, expected.value)
                if type_error:
                    return _Lowered(None, type_error)
                content_error = _literal_content_error(ptype, pred.property, expected.value)
                if content_error:
                    return _Lowered(None, content_error)
            # Offline literal proof-of-false on registry-known string properties.
            if (
                entry is not None
                and expected is not None
                and expected.kind == "literal"
                and (pred.operator or "==") == "=="
            ):
                known = _known_string_props(entry)
                if pred.property in known and known[pred.property] != str(expected.value):
                    return _Lowered(
                        None,
                        f"literal predicate proven false on '{key}.{pred.property}'",
                    )
        if ptype in {"value", "contains"} and expected is not None and expected.kind == "literal":
            content_error = _literal_content_error(ptype, "", expected.value)
            if content_error:
                return _Lowered(None, content_error)
        lowered = pred.model_copy(update={"element": key})

    elif ptype == "action":
        prop = pred.property or ""
        if prop == "type":
            prop = "action_type"
        if prop not in _ALLOWED_ACTION_PROPS:
            return _Lowered(None, f"action property '{pred.property}' not runtime-resolvable")
        if expected is not None and expected.kind == "literal":
            type_error = _literal_type_error(ptype, prop, expected.value)
            if type_error:
                return _Lowered(None, type_error)
            content_error = _literal_content_error(ptype, prop, expected.value)
            if content_error:
                return _Lowered(None, content_error)
        lowered = pred.model_copy(update={"property": prop})

    compiled = compile_predicate_spec(lowered)
    if compiled is None:
        return _Lowered(None, f"{ptype} predicate not compilable")
    return _Lowered(compiled, "", is_binding)


def _semantic_binding_required(contract: GuardContract) -> bool:
    return bool(contract.required and contract.kind in _SEMANTIC_GUARD_KINDS)


def admit_guard_contract(
    contract: GuardContract,
    evidence: GuardEvidence,
    grammar_path: str | None = None,
) -> GuardAdmissionResult:
    """Admit (or reject) a guard contract as a runtime-executable guard."""
    registry = evidence.source_registry
    declared = {slot.name for slot in contract.required_slots}
    semantic_binding_required = _semantic_binding_required(contract)

    surviving: list[str] = []
    rejected: list[str] = []
    for pred in contract.predicates:
        result = _lower_predicate(pred, registry, declared)
        if result.compiled is None:
            rejected.append(result.reason)
        else:
            surviving.append(result.compiled)

    # Any rejected predicate is genuinely non-executable -> hard reject (do not partially
    # attach). This covers unresolved aliases, unsupported action properties, undeclared
    # intent slots, non-readable properties, and literals proven false from source
    # evidence.
    if rejected:
        return GuardAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.REJECTED,
            guard=None,
            reason="rejected non-executable predicate(s): " + "; ".join(rejected),
            rejected_predicates=rejected,
            semantic_binding_required=semantic_binding_required,
        )

    # No executable predicate at all.
    if not surviving:
        if semantic_binding_required:
            return GuardAdmissionResult(
                admitted=False,
                status=GuardAdmissionStatus.LOW_TRUST,
                guard=None,
                reason="semantic binding incomplete: no executable predicate",
                semantic_binding_required=True,
                semantic_binding_incomplete=True,
            )
        return GuardAdmissionResult(
            admitted=True,
            status=GuardAdmissionStatus.ADMITTED,
            guard=None,
            reason="no runtime-executable predicate",
            semantic_binding_required=semantic_binding_required,
        )

    has_binding = any(
        lowered.is_binding
        for pred in contract.predicates
        if (lowered := _lower_predicate(pred, registry, declared)).compiled is not None
    )
    if semantic_binding_required and not has_binding:
        return GuardAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.LOW_TRUST,
            guard=None,
            reason=(
                "semantic binding incomplete: required semantic guard has no executable "
                "$intent.* binding predicate"
            ),
            semantic_binding_required=True,
            semantic_binding_incomplete=True,
        )

    guard = " && ".join(surviving)
    try:
        _parser(grammar_path).parse(guard)
    except LarkError as exc:
        return GuardAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.REJECTED,
            guard=None,
            reason=f"parse error: {exc}",
            semantic_binding_required=semantic_binding_required,
        )

    return GuardAdmissionResult(
        admitted=True,
        status=GuardAdmissionStatus.ADMITTED,
        guard=guard,
        reason=f"admitted: {len(surviving)} executable predicate(s)",
        semantic_binding_required=semantic_binding_required,
        semantic_binding_incomplete=False,
    )
