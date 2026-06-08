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
attached. No LLM, no grammar expansion, no DecisionEngine/DSLEvaluator changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from lark import Lark
from lark.exceptions import LarkError
from pydantic import BaseModel, Field

from vigil.core.paths import resolve_dsl_grammar_path
from vigil.models.guard import GuardAdmissionStatus, GuardContract, PredicateSpec, ValueRef
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
    element: str, registry: WidgetRegistry
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
    return None, None, f"element '{element}' not present in source registry"


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
        if ptype == "count" and expected is not None and expected.kind == "literal":
            type_error = _literal_type_error(ptype, "", expected.value)
            if type_error:
                return _Lowered(None, type_error)
        if ptype == "read":
            if not pred.property or pred.property not in _READABLE_PROPS:
                return _Lowered(None, f"property '{pred.property}' not runtime-readable")
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


def admit_guard_contract(
    contract: GuardContract,
    evidence: GuardEvidence,
    grammar_path: str | None = None,
) -> GuardAdmissionResult:
    """Admit (or reject) a guard contract as a runtime-executable guard."""
    registry = evidence.source_registry
    declared = {slot.name for slot in contract.required_slots}
    # Semantic completeness is controlled by the explicit guard-obligation bit, not by
    # risk metadata. ``$bind.*`` binding_requirements are metadata only and never satisfy
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
