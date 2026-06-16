"""Deterministic, rule-based ``GuardContract`` synthesizer (Stage 4, step 3).

This module turns a per-transition :class:`~vigil.neuro.guard_evidence.GuardEvidence`
into typed :class:`~vigil.models.guard.GuardContract` metadata using deterministic rules
only — **no LLM, no DSL compilation, no admission validation**. It is the bridge between
the evidence view (step 2) and the later DSL-compilation / admission passes (step 4):

    GuardEvidence -> [this module] -> GuardContract (admission_status = PENDING)

Design constraints (CLAUDE.md → "DSL Guard Generation Direction"):

- Conservative: when the evidence does not support a guard, return a pending / low-trust
  contract rather than inventing facts.
- Never fabricate element aliases: a ``read``/``value`` predicate may reference an alias
  only when it resolves in the source-state widget registry. Intent slots (``$intent.*``)
  are frozen intent variables and are always allowed.
- Generic, not benchmark-specific: domain inference keys on generic semantic tokens, not
  package names, product names, contacts, or any fixture string.

This module does not attach contracts to transitions; that is a later step.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from vigil.models.guard import (
    GuardAdmissionStatus,
    GuardContract,
    GuardKind,
    IntentSlot,
    PredicateSpec,
    SlotType,
    ValueRef,
)
from vigil.neuro.guard_registry import WidgetRole

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.neuro.guard_evidence import GuardEvidence
    from vigil.neuro.guard_registry import WidgetRegistryEntry


# ---------------------------------------------------------------------------
# Deterministic vocabularies
# ---------------------------------------------------------------------------

# Side-effect / irreversible action words. These words classify the guard kind and
# provenance only; they do not make a guard mandatory.
_SIDE_EFFECT_WORDS: tuple[str, ...] = (
    "send",
    "pay",
    "transfer",
    "delete",
    "remove",
    "allow",
    "grant",
    "confirm",
)

# Words whose intent is destructive / permission-granting rather than a positive commit.
# Used to pick SAFETY_CHECK vs CONFIRM_COMMIT.
_SAFETY_WORDS: frozenset[str] = frozenset({"delete", "remove", "allow", "grant"})

# Commit-like actions that are not in the side-effect/irreversible set. Matched on
# whole-word tokens (not substrings) to avoid false positives. Generic UI verbs only —
# no package, product, contact, or timer-label strings. These tokens classify candidate
# contracts; they are not a guard-admission gate.
_COMMIT_WORDS: frozenset[str] = frozenset(
    {"checkout", "submit", "attach", "attachment", "lap", "buy", "purchase"}
)
# Multi-word commit phrases matched against the normalized haystack.
_COMMIT_PHRASES: tuple[str, ...] = (
    "add to cart",
    "place order",
    "start timer",
    "save alarm",
    "set alarm",
    "edit alarm",
)
# Cancel / dismiss control words -> ordinary navigation.
_CANCEL_WORDS: frozenset[str] = frozenset({"cancel", "dismiss", "close", "back"})

# Command-button words excluded from item binding (a row labeled with one of these is a
# control, not a dynamic list item).
_COMMAND_WORDS: frozenset[str] = frozenset(
    {
        "save",
        "cancel",
        "ok",
        "okay",
        "confirm",
        "delete",
        "send",
        "pay",
        "transfer",
        "allow",
        "remove",
        "grant",
        "next",
        "done",
        "submit",
        "apply",
        "close",
        "back",
        "dismiss",
        "edit",
        "add",
    }
)

# Generic static navigation / control text -> ordinary navigation.
_STATIC_NAV_WORDS: frozenset[str] = frozenset(
    {
        "open",
        "view",
        "details",
        "detail",
        "menu",
        "more",
        "info",
        "about",
        "help",
        "settings",
        "home",
        "back",
        "tab",
        "profile",
        "search",
    }
)

# Alias / resource substrings hinting at navigation affordances.
_NAV_ALIAS_HINTS: tuple[str, ...] = (
    "open_",
    "nav_",
    "menu",
    "tab",
    "drawer",
    "back",
    "home",
    "settings",
)

# Generic domain token sets. Fixed precedence: bank -> commerce -> chat -> clock.
# Bare "pay"/"payment" is intentionally absent from domain tokens; it is an
# action-obligation word instead.
_DOMAIN_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "bank",
        (
            "transfer",
            "account",
            "payee",
            "recipient",
            "wire",
            "ach",
            "balance",
            "otp",
            "bank",
        ),
    ),
    (
        "commerce",
        (
            "product",
            "cart",
            "basket",
            "checkout",
            "catalog",
            "order",
            "shop",
            "store",
            "purchase",
            "commerce",
        ),
    ),
    (
        "chat",
        (
            "chat",
            "message",
            "messaging",
            "conversation",
            "thread",
            "inbox",
            "contact",
            "compose",
            "dm",
        ),
    ),
    ("clock", ("clock", "timer", "alarm", "stopwatch", "countdown", "chronometer")),
)

_NAVIGATION_TYPES: frozenset[str] = frozenset({"navigate_back", "navigate_home"})
_SCROLL_TYPES: frozenset[str] = frozenset({"scroll_up", "scroll_down"})
_CLICK_TYPES: frozenset[str] = frozenset({"click", "long_press"})

_STRONG_CONFIDENCE = 0.8
_WEAK_CONFIDENCE = 0.5


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _action_type(evidence: GuardEvidence) -> str:
    return _norm(str(evidence.action.get("type") or ""))


def _action_target_text(evidence: GuardEvidence) -> str:
    return str(evidence.action.get("target_text") or "").strip()


def _resolved_entry(evidence: GuardEvidence) -> WidgetRegistryEntry | None:
    alias = evidence.action_target_alias
    if not alias:
        return None
    return evidence.source_registry.entries.get(alias)


def _alias_in_registry(evidence: GuardEvidence) -> str | None:
    """Return the action target alias only when it resolves in the source registry."""
    alias = evidence.action_target_alias
    if alias and alias in evidence.source_registry.entries:
        return alias
    return None


# ---------------------------------------------------------------------------
# Domain + slot inference (public helpers per spec)
# ---------------------------------------------------------------------------


def _domain_haystack(evidence: GuardEvidence) -> str:
    return " ".join(
        _norm(part)
        for part in (
            evidence.source_page_function,
            evidence.target_page_function,
            evidence.source_state_name,
            evidence.target_state_name,
        )
    )


def _infer_domain(evidence: GuardEvidence) -> str:
    haystack = _domain_haystack(evidence)
    tokens = set(haystack.replace("/", " ").replace("_", " ").split())
    for domain, words in _DOMAIN_TOKENS:
        if tokens.intersection(words):
            return domain
    return "generic"


def infer_item_slot_name(evidence: GuardEvidence) -> str:
    """Deterministic slot name for an item-binding guard."""
    domain = _infer_domain(evidence)
    if domain == "chat":
        return "contact_name"
    if domain == "commerce":
        return "product_name"
    if domain == "bank":
        return "recipient"
    return "target_item"


def infer_input_slot_name(evidence: GuardEvidence) -> str:
    """Deterministic slot name for an input-binding guard."""
    alias = _norm(evidence.action_target_alias)
    haystack = f"{alias} {_domain_haystack(evidence)}"
    if "amount" in haystack or "price" in haystack:
        return "amount"
    if _infer_domain(evidence) == "chat" or any(
        tok in alias for tok in ("message", "body", "note")
    ):
        return "message_text"
    return "field_value"


def infer_commit_slots(evidence: GuardEvidence) -> list[IntentSlot]:
    """Deterministic intent slots for a side-effectful commit, by generic domain.

    These are intent slots only (frozen ``$intent.*`` variables); they never name
    element aliases.
    """
    domain = _infer_domain(evidence)
    if domain == "bank":
        return [
            IntentSlot(name="amount", slot_type=SlotType.NUMBER),
            IntentSlot(name="recipient", slot_type=SlotType.STRING),
        ]
    if domain == "commerce":
        return [
            IntentSlot(name="amount", slot_type=SlotType.NUMBER),
            IntentSlot(name="product_name", slot_type=SlotType.STRING),
        ]
    if domain == "chat":
        return [
            IntentSlot(name="contact_name", slot_type=SlotType.STRING),
            IntentSlot(name="message_text", slot_type=SlotType.STRING),
        ]
    return []


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _side_effect_hits(evidence: GuardEvidence) -> list[str]:
    """Keywords indicating that this action has externally visible side-effect semantics."""
    entry = _resolved_entry(evidence)
    parts = [
        str(evidence.action.get(field) or "")
        for field in ("target_text", "text", "target_content_desc", "resource_id")
    ]
    if entry is not None:
        parts.extend([entry.text, entry.content_description, entry.resource_id, entry.alias])
    haystack = _norm(" ".join(parts))
    return [kw for kw in _SIDE_EFFECT_WORDS if kw in haystack]


def _commit_haystack(evidence: GuardEvidence) -> str:
    """Normalized text drawn from the action and the resolved widget for commit matching."""
    entry = _resolved_entry(evidence)
    parts: list[str] = [
        str(evidence.action.get("target_text") or ""),
        str(evidence.action.get("text") or ""),
        str(evidence.action.get("target_content_desc") or ""),
        str(evidence.action_target_alias or ""),
    ]
    if entry is not None:
        parts.extend([entry.text, entry.content_description, entry.resource_id, entry.alias])
    return _norm(" ".join(parts))


def _commit_hit(evidence: GuardEvidence) -> str | None:
    """Return the matched token for a commit-like action.

    Single words match on whole-word tokens; phrases match as substrings of the normalized
    haystack. ``matched_commit`` is ``None`` when no commit signal is present.
    """
    haystack = _commit_haystack(evidence)
    tokens = {tok for tok in re.split(r"[^a-z0-9]+", haystack) if tok}
    for word in _COMMIT_WORDS:
        if word in tokens:
            return word
    for phrase in _COMMIT_PHRASES:
        if phrase in haystack:
            return phrase
    return None


def _is_command_text(text: str) -> bool:
    return _norm(text) in _COMMAND_WORDS


def _is_cancel(evidence: GuardEvidence) -> bool:
    text = _norm(_action_target_text(evidence))
    alias = _norm(evidence.action_target_alias)
    if text in _CANCEL_WORDS:
        return True
    return any(word in alias for word in _CANCEL_WORDS)


def _item_like_siblings(evidence: GuardEvidence) -> list[dict[str, Any]]:
    """Sibling actions shaped like dynamic list rows (clicks with non-command text)."""
    out: list[dict[str, Any]] = []
    for action in evidence.sibling_actions:
        if _norm(str(action.get("type") or "")) not in _CLICK_TYPES:
            continue
        text = str(action.get("target_text") or "").strip()
        if not text or _is_command_text(text):
            continue
        out.append(action)
    return out


def _has_list_container(evidence: GuardEvidence) -> bool:
    return any(
        entry.role is WidgetRole.LIST_CONTAINER
        for entry in evidence.source_registry.entries.values()
    )


def _looks_like_static_nav(evidence: GuardEvidence) -> bool:
    text = _norm(_action_target_text(evidence))
    alias = _norm(evidence.action_target_alias)
    if text and text in _STATIC_NAV_WORDS:
        return True
    if not text and any(hint in alias for hint in _NAV_ALIAS_HINTS):
        return True
    return bool(text) and any(hint in alias for hint in _NAV_ALIAS_HINTS)


def _enabled_predicate(alias: str) -> PredicateSpec:
    return PredicateSpec(
        predicate_type="read",
        element=alias,
        property="is_enabled",
        operator="==",
        expected=ValueRef(kind="literal", value=True),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def synthesize_guard_contract(evidence: GuardEvidence) -> GuardContract:
    """Synthesize a typed :class:`GuardContract` from one transition's evidence.

    The contract is always returned with ``admission_status = PENDING``; admission is a
    later step. The classification is deterministic and conservative.
    """
    atype = _action_type(evidence)
    domain = _infer_domain(evidence)
    side_effect_hits = _side_effect_hits(evidence)
    alias = _alias_in_registry(evidence)

    # 1. Global navigation actions.
    if atype in _NAVIGATION_TYPES:
        return _contract(
            kind=GuardKind.NAVIGATION,
            required=False,
            confidence=_STRONG_CONFIDENCE,
            provenance=["navigate_action"],
        )

    # 2. Scroll / swipe.
    if atype in _SCROLL_TYPES:
        return _contract(
            kind=GuardKind.NONE,
            required=False,
            confidence=_STRONG_CONFIDENCE,
            provenance=["scroll_action"],
        )

    # 3. Text entry. Classified before side-effect/commit handling so an
    #    amount/recipient field still binds the typed value to an intent slot instead
    #    of an enabled-only commit guard.
    if atype == "input_text":
        slot = infer_input_slot_name(evidence)
        predicate = PredicateSpec(
            predicate_type="action",
            property="input_text",
            operator="==",
            expected=ValueRef(kind="intent", slot=slot),
        )
        return _contract(
            kind=GuardKind.INPUT_BINDING,
            required=False,
            required_slots=[IntentSlot(name=slot, slot_type=_input_slot_type(slot))],
            predicates=[predicate],
            confidence=_STRONG_CONFIDENCE,
            provenance=["input_text_action", f"domain:{domain}"],
            notes=(
                "binds the typed value via action(input_text), exposed on the runtime "
                "action context for input actions"
            ),
        )

    # 4. Cancel / dismiss / close (only when not itself a side-effect word).
    if not side_effect_hits and _is_cancel(evidence):
        return _contract(
            kind=GuardKind.NAVIGATION,
            required=False,
            confidence=_STRONG_CONFIDENCE,
            provenance=["cancel_action"],
        )

    # 5. Side-effectful commit / safety action. These tokens classify the guard
    #    candidate and provenance; they do not create a guard-admission gate.
    if side_effect_hits:
        is_safety = any(kw in _SAFETY_WORDS for kw in side_effect_hits)
        predicates: list[PredicateSpec] = []
        provenance = [
            "side_effect_action",
            *[f"side_effect:{kw}" for kw in side_effect_hits],
            f"domain:{domain}",
        ]
        notes = ""
        if alias is not None:
            # Executable evidence: we can pin the commit control's enabledness.
            predicates.append(_enabled_predicate(alias))
            confidence = _STRONG_CONFIDENCE
        else:
            # Only domain intent slots, no resolved widget -> weak, pending.
            confidence = _WEAK_CONFIDENCE
            notes = "no resolved source-widget binding; only domain intent slots inferred"
        return _contract(
            kind=GuardKind.SAFETY_CHECK if is_safety else GuardKind.CONFIRM_COMMIT,
            required=False,
            required_slots=infer_commit_slots(evidence),
            predicates=predicates,
            confidence=confidence,
            provenance=provenance,
            notes=notes,
            semantic_binding_required=False,
            semantic_binding_incomplete=False,
        )

    # 6. Commit-like actions (checkout / submit / attach / buy / add-to-cart / timer
    #    start / alarm save / stopwatch lap). They classify the candidate but do not
    #    create a mandatory semantic-completeness gate.
    commit_word = _commit_hit(evidence)
    if atype in _CLICK_TYPES and commit_word is not None:
        predicates = []
        if alias is not None:
            predicates.append(_enabled_predicate(alias))
        return _contract(
            kind=GuardKind.CONFIRM_COMMIT,
            required=False,
            required_slots=infer_commit_slots(evidence),
            predicates=predicates,
            confidence=_STRONG_CONFIDENCE if alias is not None else _WEAK_CONFIDENCE,
            provenance=["semantic_commit", f"commit:{commit_word}", f"domain:{domain}"],
            notes=(
                "commit-like action; deterministic synthesis can pin enabledness "
                "when the source widget is resolvable"
            ),
            semantic_binding_required=False,
            semantic_binding_incomplete=False,
        )

    # 7. Toggle / checkable click.
    entry = _resolved_entry(evidence)
    if (
        atype in _CLICK_TYPES
        and entry is not None
        and entry.role in (WidgetRole.TOGGLE, WidgetRole.CHECKBOX, WidgetRole.RADIO)
    ):
        predicates = []
        if alias is not None:
            predicates.append(_enabled_predicate(alias))
        return _contract(
            kind=GuardKind.TOGGLE_BINDING,
            required=False,
            required_slots=[IntentSlot(name="desired_state", slot_type=SlotType.BOOLEAN)],
            predicates=predicates,
            confidence=_STRONG_CONFIDENCE if alias is not None else _WEAK_CONFIDENCE,
            provenance=["toggle_role", f"domain:{domain}"],
            notes=(
                "desired post-toggle state is not expressible in the current DSL; "
                "left to admission/runtime"
            ),
        )

    # 8. Item binding — requires genuine list / repeated-row evidence.
    if atype in _CLICK_TYPES:
        target_text = _action_target_text(evidence)
        item_siblings = _item_like_siblings(evidence)
        role_is_item = entry is not None and entry.role in (
            WidgetRole.LIST_ITEM,
            WidgetRole.MENU_ITEM,
        )
        list_evidence = (
            role_is_item
            or (_has_list_container(evidence) and bool(item_siblings))
            or bool(item_siblings)
        )
        if target_text and not _is_command_text(target_text) and list_evidence:
            slot = infer_item_slot_name(evidence)
            predicate = PredicateSpec(
                predicate_type="action",
                property="target_text",
                operator="==",
                expected=ValueRef(kind="intent", slot=slot),
            )
            row_source = "list_item_role" if role_is_item else "sibling_rows"
            return _contract(
                kind=GuardKind.ITEM_BINDING,
                required=False,
                required_slots=[IntentSlot(name=slot, slot_type=SlotType.STRING)],
                predicates=[predicate],
                confidence=_STRONG_CONFIDENCE,
                provenance=["item_binding", row_source, f"domain:{domain}"],
            )

    # 9. Ordinary state-changing navigation with static-looking control text.
    if (
        atype in _CLICK_TYPES
        and evidence.source_state_id != evidence.target_state_id
        and _looks_like_static_nav(evidence)
    ):
        return _contract(
            kind=GuardKind.NAVIGATION,
            required=False,
            confidence=_STRONG_CONFIDENCE,
            provenance=["state_change_navigation", f"domain:{domain}"],
        )

    # 10. Unknown — conservative fallback.
    return _contract(
        kind=GuardKind.UNKNOWN,
        required=False,
        confidence=_WEAK_CONFIDENCE,
        provenance=["unclassified_click"],
    )


def synthesize_all_guard_contracts(
    evidence_items: list[GuardEvidence],
) -> list[GuardContract]:
    """Synthesize contracts for a batch of evidence items, order-preserving."""
    return [synthesize_guard_contract(ev) for ev in evidence_items]


# ---------------------------------------------------------------------------
# Internal construction
# ---------------------------------------------------------------------------


def _input_slot_type(slot: str) -> SlotType:
    return SlotType.NUMBER if slot == "amount" else SlotType.STRING


def _contract(
    *,
    kind: GuardKind,
    required: bool,
    confidence: float,
    provenance: list[str],
    required_slots: list[IntentSlot] | None = None,
    predicates: list[PredicateSpec] | None = None,
    notes: str = "",
    semantic_binding_required: bool = False,
    semantic_binding_incomplete: bool = False,
) -> GuardContract:
    return GuardContract(
        kind=kind,
        required=required,
        required_slots=required_slots or [],
        predicates=predicates or [],
        admission_status=GuardAdmissionStatus.PENDING,
        admission_reason="",
        confidence=confidence,
        provenance=provenance,
        notes=notes,
        semantic_binding_required=semantic_binding_required,
        semantic_binding_incomplete=semantic_binding_incomplete,
    )
