"""Stable widget registry for contract-first guard generation (Stage 4, step 2).

This module builds a *deterministic, LLM-free* widget registry from runtime trace
evidence. A ``WidgetRegistry`` assigns each interactable element on a source screen a
stable alias (backed by ``resource_id`` / ``content_description`` / stable text role /
class alias) plus a coarse role, selector-stability grade, readable DSL property list,
and risk hints.

It is one of the two inputs the later typed-``GuardContract`` synthesis pass consumes
(the other being :mod:`vigil.neuro.guard_evidence`). Per project rules:

- XML/runtime traces are the source of truth: only elements actually present on the
  representative screen become registry entries.
- ``AppPrior`` (APK static artifacts) is a *prior only*. It may enrich the role or risk
  hints of an already-present entry, but must never create an entry for an element that
  is absent from the runtime screen.
- Raw capture-local ``e_XXXX`` handles are never exposed as a primary alias unless no
  better signal exists; they are kept in ``element_id_to_alias`` so later stages can map
  an action target handle back to its stable alias.

No DSL compilation, admission validation, or LLM call happens here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.models.fsm import AbstractState
    from vigil.neuro.app_prior import AppPrior


class WidgetRole(StrEnum):
    """Coarse, deterministically-inferred role of a widget."""

    UNKNOWN = "unknown"
    BUTTON = "button"
    TEXT_FIELD = "text_field"
    TOGGLE = "toggle"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    LIST_CONTAINER = "list_container"
    LIST_ITEM = "list_item"
    TITLE = "title"
    MENU_ITEM = "menu_item"
    TOOLBAR_ACTION = "toolbar_action"
    DIALOG_ACTION = "dialog_action"
    IMAGE_BUTTON = "image_button"


class SelectorStability(StrEnum):
    """How stable an element's primary selector is expected to be across captures."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WidgetRegistryEntry(BaseModel):
    """A single stable widget alias and its observed/derived properties."""

    alias: str
    element_id: str = ""
    resource_id: str = ""
    text: str = ""
    content_description: str = ""
    class_name: str = ""
    role: WidgetRole = WidgetRole.UNKNOWN
    readable_props: list[str] = Field(default_factory=list)
    selector_stability: SelectorStability = SelectorStability.LOW
    risk_hints: list[str] = Field(default_factory=list)
    source: str = "trace"


class WidgetRegistry(BaseModel):
    """Stable widget registry for one FSM state / source screen."""

    state_id: str
    screen_id: str | None = None
    entries: dict[str, WidgetRegistryEntry] = Field(default_factory=dict)
    element_id_to_alias: dict[str, str] = Field(default_factory=dict)
    resource_id_to_alias: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Deterministic vocabularies
# ---------------------------------------------------------------------------

# Risk keywords for high-risk / irreversible actions. Order is fixed so risk_hints
# is deterministic.
_RISK_KEYWORDS: tuple[str, ...] = (
    "send",
    "pay",
    "transfer",
    "delete",
    "remove",
    "allow",
    "grant",
    "confirm",
)

# Dialog-action labels that suggest a confirm/cancel style control.
_DIALOG_WORDS: tuple[str, ...] = (
    "cancel",
    "ok",
    "confirm",
    "delete",
    "send",
    "pay",
    "transfer",
    "allow",
)

# Boolean DSL-readable properties, in stable output order. Included only when the key
# is actually present in the element dict.
_BOOL_PROPS: tuple[str, ...] = (
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


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _as_dict(obj: Any) -> dict[str, Any]:
    """Coerce a screen/element to a plain dict (pydantic models -> ``model_dump``)."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    dumper = getattr(obj, "model_dump", None)
    if callable(dumper):  # pragma: no cover - defensive
        result = dumper()
        if isinstance(result, dict):
            return result
    return {}


def _elements_of(screen: dict[str, Any]) -> list[dict[str, Any]]:
    """Return element dicts, preferring ``interactable_elements`` then ``elements``."""
    raw = screen.get("interactable_elements")
    if raw is None:
        raw = screen.get("elements", [])
    return [_as_dict(el) for el in (raw or [])]


def _short_class(class_name: str) -> str:
    return class_name.rsplit(".", 1)[-1] if "." in class_name else class_name


def _slug(value: str) -> str:
    """Lowercase, non-alphanumeric -> ``_``, collapse and trim underscores."""
    out: list[str] = []
    prev_us = False
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_")


def _resource_short(resource_id: str) -> str:
    """Short name from a resource id, e.g. ``com.app:id/amount`` -> ``amount``."""
    short = resource_id
    if ":id/" in short:
        short = short.split(":id/", 1)[-1]
    elif "/" in short:
        short = short.rsplit("/", 1)[-1]
    return _slug(short)


# ---------------------------------------------------------------------------
# Per-element inference
# ---------------------------------------------------------------------------


def _infer_role(el: dict[str, Any]) -> WidgetRole:
    cls = _short_class(str(el.get("class_name") or "")).lower()
    rid = str(el.get("resource_id") or "").lower()
    text = str(el.get("text") or "").lower()
    cdesc = str(el.get("content_description") or "").lower()
    label = f"{text} {cdesc} {rid}"
    clickable = bool(el.get("is_clickable"))

    if "edittext" in cls or el.get("is_editable"):
        return WidgetRole.TEXT_FIELD
    if "switch" in cls:
        return WidgetRole.TOGGLE
    if "checkbox" in cls:
        return WidgetRole.CHECKBOX
    if "radiobutton" in cls:
        return WidgetRole.RADIO
    if "imagebutton" in cls:
        return WidgetRole.IMAGE_BUTTON
    if "button" in cls:
        return WidgetRole.BUTTON
    if any(k in cls for k in ("recyclerview", "listview", "scrollview")) or el.get("is_scrollable"):
        return WidgetRole.LIST_CONTAINER
    if "title" in rid or "title" in text:
        return WidgetRole.TITLE
    if clickable and any(w in label for w in _DIALOG_WORDS):
        return WidgetRole.DIALOG_ACTION
    if el.get("is_checkable"):
        return WidgetRole.CHECKBOX
    return WidgetRole.UNKNOWN


def _infer_stability(el: dict[str, Any]) -> SelectorStability:
    if str(el.get("resource_id") or "").strip():
        return SelectorStability.HIGH
    if str(el.get("content_description") or "").strip():
        return SelectorStability.MEDIUM
    if str(el.get("text") or "").strip():
        return SelectorStability.MEDIUM
    return SelectorStability.LOW


def _readable_props(el: dict[str, Any]) -> list[str]:
    props: list[str] = []
    # text / content_description / value are exposed by the runtime screen context
    # (DecisionEngine._build_screen_context) even when empty — an empty EditText / value
    # is meaningful for form guards. Include them whenever the key is present.
    for key in ("text", "content_description", "value"):
        if key in el:
            props.append(key)
    if str(el.get("class_name") or "").strip():
        props.append("class_name")
    if str(el.get("resource_id") or "").strip():
        props.append("resource_id")
    for name in _BOOL_PROPS:
        if name in el:
            props.append(name)
    if "children" in el or "children_count" in el:
        props.append("children_count")
    return props


def _risk_hints(el: dict[str, Any], semantic_label: str = "") -> list[str]:
    text = str(el.get("text") or "").lower()
    cdesc = str(el.get("content_description") or "").lower()
    rid = str(el.get("resource_id") or "").lower()
    label = f"{text} {cdesc} {rid} {semantic_label.lower()}"
    return [kw for kw in _RISK_KEYWORDS if kw in label]


def _alias_candidate(el: dict[str, Any], role: WidgetRole) -> str:
    """Best stable alias signal for an element, ``""`` if only the raw id is available."""
    resource_id = str(el.get("resource_id") or "").strip()
    if resource_id:
        short = _resource_short(resource_id)
        if short:
            return short
    cdesc = _slug(str(el.get("content_description") or ""))
    if cdesc:
        return cdesc
    text = _slug(str(el.get("text") or ""))
    if text:
        return f"{text}_{role.value}"
    return ""


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_widget_registry_from_screen(
    state_id: str,
    screen: dict[str, Any],
    app_prior: AppPrior | None = None,
    widget_aliases: list[dict[str, Any]] | None = None,
) -> WidgetRegistry:
    """Build a :class:`WidgetRegistry` from a single representative screen dict."""
    screen_dict = _as_dict(screen)
    registry = WidgetRegistry(
        state_id=state_id,
        screen_id=screen_dict.get("screen_id"),
    )
    semantic_labels = _semantic_label_map(widget_aliases)

    used_aliases: set[str] = set()
    class_counts: dict[str, int] = {}

    for el in _elements_of(screen_dict):
        element_id = str(el.get("element_id") or "")
        resource_id = str(el.get("resource_id") or "")
        semantic_label = semantic_labels.get(element_id, "")
        role = _infer_role(el)

        alias = _alias_candidate(el, role)
        if not alias:
            short_class = _short_class(str(el.get("class_name") or ""))
            if short_class:
                idx = class_counts.get(short_class, 0)
                class_counts[short_class] = idx + 1
                alias = f"{short_class}_{idx}"
            else:
                alias = element_id or f"element_{len(registry.entries)}"

        # Ensure uniqueness within this registry.
        base_alias = alias
        suffix = 2
        while alias in used_aliases:
            alias = f"{base_alias}_{suffix}"
            suffix += 1
        used_aliases.add(alias)

        entry = WidgetRegistryEntry(
            alias=alias,
            element_id=element_id,
            resource_id=resource_id,
            text=str(el.get("text") or ""),
            content_description=str(el.get("content_description") or ""),
            class_name=str(el.get("class_name") or ""),
            role=role,
            readable_props=_readable_props(el),
            selector_stability=_infer_stability(el),
            risk_hints=_risk_hints(el, semantic_label),
            source="trace+llm" if semantic_label else "trace",
        )
        registry.entries[alias] = entry
        if element_id:
            registry.element_id_to_alias[element_id] = alias
        if resource_id and resource_id not in registry.resource_id_to_alias:
            registry.resource_id_to_alias[resource_id] = alias

    if app_prior is not None:
        _enrich_with_prior(registry, app_prior)

    return registry


def build_widget_registry(
    state: AbstractState,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
) -> WidgetRegistry:
    """Build a :class:`WidgetRegistry` for ``state`` from its representative screen.

    Uses the first id in ``state.evidence.raw_screen_ids`` that resolves in
    ``raw_screens``. If the state has no usable screen, returns an empty registry that
    still carries the ``state_id`` (no crash).
    """
    screen: dict[str, Any] | None = None
    screen_id: str | None = None
    for sid in state.evidence.raw_screen_ids:
        candidate = raw_screens.get(sid) if raw_screens else None
        if candidate is not None:
            screen = _as_dict(candidate)
            screen_id = sid
            break

    if screen is None:
        return WidgetRegistry(state_id=state.state_id)

    registry = build_widget_registry_from_screen(
        state.state_id,
        screen,
        app_prior,
        widget_aliases=state.annotations.widget_aliases,
    )
    # Prefer the screen-id key we matched in raw_screens when the screen dict omits it.
    if registry.screen_id is None:
        registry.screen_id = screen_id
    return registry


def build_widget_registry_from_screen_ids(
    state_id: str,
    screen_ids: list[str],
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
    widget_aliases: list[dict[str, Any]] | None = None,
) -> WidgetRegistry | None:
    """Build a registry from the first id in ``screen_ids`` that resolves.

    Returns ``None`` when none of the ids resolves in ``raw_screens`` (so the caller can
    fall back to a state-evidence-based screen). Used to prefer per-transition
    provenance screens, which deterministically contain the capture-local action target
    handle, over a merged/quotiented state's first representative screen.
    """
    for sid in screen_ids:
        candidate = raw_screens.get(sid) if raw_screens else None
        if candidate is not None:
            registry = build_widget_registry_from_screen(
                state_id,
                _as_dict(candidate),
                app_prior,
                widget_aliases=widget_aliases,
            )
            if registry.screen_id is None:
                registry.screen_id = sid
            return registry
    return None


def _semantic_label_map(widget_aliases: list[dict[str, Any]] | None) -> dict[str, str]:
    """Return LLM-derived element_id -> label hints from state annotations.

    These labels are non-authoritative: they may enrich risk hints, but they do not create
    elements and admission still requires runtime-resolvable selectors.
    """
    out: dict[str, str] = {}
    for item in widget_aliases or []:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("element_id") or "").strip()
        label = str(item.get("label") or "").strip()
        if element_id and label:
            out[element_id] = label
    return out


def _enrich_with_prior(registry: WidgetRegistry, app_prior: AppPrior) -> None:
    """Enrich *existing* entries with static-prior hints. Never creates new entries.

    Static APK artifacts are priors only: we may strengthen risk hints or mark a
    static-prior corroboration, but element presence still comes from the runtime
    screen.
    """
    # High-risk string-constant values (e.g. a "Transfer" / "Delete" label resource).
    risky_constants: set[str] = set()
    for value in app_prior.string_constants.values():
        low = str(value).lower()
        if any(kw in low for kw in _RISK_KEYWORDS):
            risky_constants.add(low)

    # Widget declarations indexed by short id for cheap lookup.
    decl_classes: dict[str, str] = {}
    for decl in app_prior.widget_declarations:
        decl_classes[_slug(decl.widget_id)] = decl.widget_class

    for entry in registry.entries.values():
        text_low = entry.text.lower().strip()
        if text_low and text_low in risky_constants:
            for kw in _RISK_KEYWORDS:
                if kw in text_low and kw not in entry.risk_hints:
                    entry.risk_hints.append(kw)
            if "prior" not in entry.source:
                entry.source = "trace+prior"

        if entry.resource_id:
            short = _resource_short(entry.resource_id)
            decl_class = decl_classes.get(short)
            if decl_class and entry.role is WidgetRole.UNKNOWN:
                inferred = _infer_role({"class_name": decl_class})
                if inferred is not WidgetRole.UNKNOWN:
                    entry.role = inferred
                    if "prior" not in entry.source:
                        entry.source = "trace+prior"
