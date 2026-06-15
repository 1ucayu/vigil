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
- Contracts that explicitly declare ``semantic_binding_required`` must carry at
  least one executable semantic *binding* predicate to be marked semantically
  complete; an enabled/clickable-only predicate is admitted only as incomplete
  metadata.

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
    EffectRequirement,
    GuardAdmissionStatus,
    GuardContract,
    PredicateSpec,
    TransitionPostcondition,
    ValueRef,
)
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
    semantic_binding_incomplete: bool = False


class PostconditionAdmissionResult(BaseModel):
    """Outcome of admitting a postcondition contract as executable ``Psi`` DSL."""

    admitted: bool
    status: GuardAdmissionStatus
    postcondition: str | None = None
    reason: str = ""
    rejected_predicates: list[str] = Field(default_factory=list)
    unsupported_effects: list[str] = Field(default_factory=list)
    intent_effect_incomplete: bool = False


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

    if ptype in {"appeared", "disappeared", "value_changed"}:
        return _Lowered(None, f"{ptype} predicate is postcondition-only")

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
        lowered = pred.model_copy(update={"property": prop})

    compiled = compile_predicate_spec(lowered)
    if compiled is None:
        return _Lowered(None, f"{ptype} predicate not compilable")
    return _Lowered(compiled, "", is_binding)


def _lower_effect_requirement(
    effect: EffectRequirement,
    evidence: GuardEvidence,
    declared_slots: set[str],
) -> _Lowered:
    kind = (effect.effect_kind or "").strip().lower()
    if kind in {"appeared", "disappeared", "value_changed"}:
        return _Lowered(None, f"{kind} effect is audit-only")
    return _Lowered(None, f"effect_requirement kind '{kind or 'unknown'}' unsupported")


def admit_guard_contract(
    contract: GuardContract,
    evidence: GuardEvidence,
    grammar_path: str | None = None,
) -> GuardAdmissionResult:
    """Admit (or reject) a guard contract as a runtime-executable guard."""
    registry = evidence.source_registry
    declared = {slot.name for slot in contract.required_slots}
    # Semantic completeness is controlled by the explicit guard-obligation bit.
    # ``$bind.*`` binding_requirements are metadata only and never satisfy
    # this executable ``$intent.*`` requirement.
    binding_required = contract.semantic_binding_required

    surviving: list[str] = []
    rejected: list[str] = []
    has_binding = False
    for pred in contract.predicates:
        result = _lower_predicate(pred, registry, declared)
        if result.compiled is None:
            rejected.append(result.reason)
        else:
            surviving.append(result.compiled)
            has_binding = has_binding or result.is_binding

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
        )

    # No executable predicate at all.
    if not surviving:
        if not contract.required:
            return GuardAdmissionResult(
                admitted=True,
                status=GuardAdmissionStatus.ADMITTED,
                guard=None,
                reason="optional contract; no guard required",
            )
        return GuardAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.REJECTED,
            guard=None,
            reason="required guard has no runtime-executable predicate",
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
        )

    # Executable guard admitted. Semantic completeness is strict: a guard is
    # semantic-complete only when at least one *surviving executable* ``$intent.*`` binding
    # predicate exists (``has_binding``). A semantic-required guard with only structural /
    # enabledness predicates — or whose only binding is a non-executable ``$bind.*``
    # requirement — is still evidence-backed and executable, so we attach it but record
    # that its semantic binding is incomplete (metadata, not a blocker).
    semantic_binding_incomplete = (not has_binding) and (
        binding_required or contract.semantic_binding_incomplete
    )
    reason = (
        "executable guard admitted; semantic binding incomplete"
        if semantic_binding_incomplete
        else f"admitted: {len(surviving)} executable predicate(s)"
    )
    return GuardAdmissionResult(
        admitted=True,
        status=GuardAdmissionStatus.ADMITTED,
        guard=guard,
        reason=reason,
        semantic_binding_incomplete=semantic_binding_incomplete,
    )


def _postcondition_state_name(pred: PredicateSpec) -> str:
    state = pred.args.get("state")
    if state is None and pred.expected is not None and pred.expected.kind == "literal":
        state = pred.expected.value
    return str(state or "")


def _lower_postcondition_predicate(
    pred: PredicateSpec,
    evidence: GuardEvidence,
    declared_slots: set[str],
) -> _Lowered:
    """Lower one target-side ``Psi`` predicate."""
    if (
        pred.expected is not None
        and pred.expected.kind == "intent"
        and (not pred.expected.slot or pred.expected.slot not in declared_slots)
    ):
        return _Lowered(None, f"undeclared intent slot '{pred.expected.slot}'")

    if pred.predicate_type == "in_state":
        state = _postcondition_state_name(pred)
        if not state:
            return _Lowered(None, "in_state predicate missing args.state")
        if state != evidence.target_state_id:
            return _Lowered(
                None,
                f"in_state({state}) does not match target state {evidence.target_state_id}",
            )
        compiled = compile_predicate_spec(pred)
        if compiled is None:
            return _Lowered(None, "in_state predicate not compilable")
        return _Lowered(compiled, "")

    if pred.predicate_type in {"appeared", "disappeared", "value_changed"}:
        return _Lowered(None, f"{pred.predicate_type} effect predicate is audit-only")

    if pred.predicate_type not in ("read", "value", "contains", "count"):
        return _Lowered(
            None,
            f"{pred.predicate_type} predicate is not supported in postcondition Psi",
        )

    if evidence.target_registry is None:
        return _Lowered(None, "target registry unavailable for postcondition element predicate")
    return _lower_predicate(pred, evidence.target_registry, declared_slots)


def _lower_effect_requirements(
    postcondition: TransitionPostcondition,
    evidence: GuardEvidence,
    declared_slots: set[str],
) -> tuple[list[str], list[str], bool]:
    surviving: list[str] = []
    unsupported: list[str] = []
    has_binding = False
    for effect in postcondition.effect_requirements:
        name = effect.name or "effect"
        result = _lower_effect_requirement(effect, evidence, declared_slots)
        if result.compiled is None:
            reason = result.reason
            effect.unsupported_reason = reason
            unsupported.append(f"{name}: {reason}")
            continue
        effect.unsupported_reason = ""
        surviving.append(result.compiled)
        has_binding = has_binding or result.is_binding
    return surviving, unsupported, has_binding


def admit_postcondition_contract(
    postcondition: TransitionPostcondition,
    evidence: GuardEvidence,
    grammar_path: str | None = None,
) -> PostconditionAdmissionResult:
    """Admit (or reject) a postcondition contract as executable target-side ``Psi``."""
    declared = {slot.name for slot in postcondition.required_slots}

    surviving: list[str] = []
    rejected: list[str] = []
    has_intent_effect = False
    for pred in postcondition.predicates:
        result = _lower_postcondition_predicate(pred, evidence, declared)
        if result.compiled is None:
            rejected.append(result.reason)
        else:
            surviving.append(result.compiled)
            has_intent_effect = has_intent_effect or result.is_binding

    effect_predicates, unsupported_effects, effect_has_binding = _lower_effect_requirements(
        postcondition,
        evidence,
        declared,
    )
    surviving.extend(effect_predicates)
    has_intent_effect = has_intent_effect or effect_has_binding

    if rejected:
        return PostconditionAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.REJECTED,
            postcondition=None,
            reason="rejected non-executable postcondition predicate(s): " + "; ".join(rejected),
            rejected_predicates=rejected,
            unsupported_effects=unsupported_effects,
            intent_effect_incomplete=postcondition.intent_effect_required,
        )

    if not surviving:
        if not postcondition.required:
            return PostconditionAdmissionResult(
                admitted=True,
                status=GuardAdmissionStatus.ADMITTED,
                postcondition=None,
                reason="optional postcondition; no executable Psi required",
                unsupported_effects=unsupported_effects,
                intent_effect_incomplete=postcondition.intent_effect_incomplete,
            )
        return PostconditionAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.REJECTED,
            postcondition=None,
            reason="required postcondition has no runtime-executable predicate",
            unsupported_effects=unsupported_effects,
            intent_effect_incomplete=True,
        )

    psi = " && ".join(surviving)
    try:
        _parser(grammar_path).parse(psi)
    except LarkError as exc:
        return PostconditionAdmissionResult(
            admitted=False,
            status=GuardAdmissionStatus.REJECTED,
            postcondition=None,
            reason=f"parse error: {exc}",
            unsupported_effects=unsupported_effects,
            intent_effect_incomplete=postcondition.intent_effect_required,
        )

    intent_effect_incomplete = (not has_intent_effect) and (
        postcondition.intent_effect_required or postcondition.intent_effect_incomplete
    )
    reason = (
        "executable postcondition admitted; intent effect incomplete"
        if intent_effect_incomplete
        else f"admitted: {len(surviving)} executable postcondition predicate(s)"
    )
    return PostconditionAdmissionResult(
        admitted=True,
        status=GuardAdmissionStatus.ADMITTED,
        postcondition=psi,
        reason=reason,
        unsupported_effects=unsupported_effects,
        intent_effect_incomplete=intent_effect_incomplete,
    )
