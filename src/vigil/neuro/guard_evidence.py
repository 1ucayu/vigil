"""Per-transition guard evidence view for contract-first guard generation.

A :class:`GuardEvidence` is the deterministic, LLM-free evidence bundle for one FSM
transition that the later typed-``GuardContract`` synthesis pass consumes. It joins:

- the source/target :class:`~vigil.neuro.guard_registry.WidgetRegistry`,
- the proposed canonical action and its resolved stable target alias,
- sibling outgoing transitions from the same source state,
- replay confidence / low-trust scope and transition provenance,
- a lightweight deterministic source-to-target diff summary,
- Hoare-style source/target observation evidence (XML path/full text, screenshot path,
  compact tree, LLM-derived alt text), and
- static-prior hints (activity label, permissions, string resources).

This module only *reads* the existing FSM and raw trace screens. It does not add edges,
modify state identity, change replay confidence, compile DSL, validate admission, or call
an LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from vigil.neuro.guard_registry import (
    WidgetRegistry,
    build_widget_registry,
    build_widget_registry_from_screen_ids,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.models.fsm import AbstractState, AppFSM, Transition
    from vigil.neuro.app_prior import AppPrior


class ScreenEvidence(BaseModel):
    """Observation evidence for one side of a transition.

    Paths are provenance/debug metadata. The prompt also carries textual content
    (compact tree and XML text) because an ordinary LLM API cannot open local paths.
    Screenshots are attached separately by the LLM call path when the file exists.
    """

    state_id: str
    screen_id: str = ""
    activity_name: str = ""
    package_name: str = ""
    screenshot_path: str = ""
    xml_tree_path: str = ""
    compact_tree_text: str = ""
    xml_excerpt: str = ""
    alt_text: str = ""
    page_function: str = ""
    display_name: str = ""


class GuardEvidence(BaseModel):
    """Deterministic evidence bundle for one transition's guard synthesis."""

    transition_index: int
    source_state_id: str
    target_state_id: str
    action: dict[str, Any]
    source_state_name: str = ""
    target_state_name: str = ""
    source_page_function: str = ""
    target_page_function: str = ""
    source_screen_ids: list[str] = Field(default_factory=list)
    target_screen_ids: list[str] = Field(default_factory=list)
    source_screen: ScreenEvidence = Field(default_factory=lambda: ScreenEvidence(state_id=""))
    target_screen: ScreenEvidence = Field(default_factory=lambda: ScreenEvidence(state_id=""))
    source_registry: WidgetRegistry
    target_registry: WidgetRegistry | None = None
    sibling_actions: list[dict[str, Any]] = Field(default_factory=list)
    action_target_alias: str | None = None
    action_target_alias_reason: str = ""
    replay_confidence: float = 0.0
    low_trust: bool = False
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    static_prior_hints: list[str] = Field(default_factory=list)
    diff_summary: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selector_resource_id(action: dict[str, Any]) -> str:
    selector = action.get("target_selector")
    if isinstance(selector, dict):
        return str(selector.get("resource_id") or "")
    return ""


def _resolve_target_alias(
    action: dict[str, Any], source_registry: WidgetRegistry
) -> tuple[str | None, str]:
    """Resolve the proposed action target to a stable source-registry alias.

    Returns ``(alias, reason)``. ``alias`` is ``None`` when the target cannot be
    resolved unambiguously; ``reason`` is an observable explanation (e.g. a text match
    that hit zero or multiple entries) for evidence/report metadata.
    """
    # 1. capture-local element handle.
    target = str(action.get("target") or "")
    if target and target in source_registry.element_id_to_alias:
        return source_registry.element_id_to_alias[target], "matched:element_id"

    # 2. resource id from the action or its selector.
    for rid in (
        str(action.get("target_resource_id") or ""),
        str(action.get("resource_id") or ""),
        _selector_resource_id(action),
    ):
        if rid and rid in source_registry.resource_id_to_alias:
            return source_registry.resource_id_to_alias[rid], "matched:resource_id"

    # 3. text match against registry entry text — only when exactly one entry matches.
    selector = action.get("target_selector")
    selector_text = selector.get("text") if isinstance(selector, dict) else None
    target_text = str(action.get("target_text") or selector_text or "").strip()
    if target_text:
        matches = [
            entry.alias
            for entry in source_registry.entries.values()
            if entry.text.strip() == target_text
        ]
        if len(matches) == 1:
            return matches[0], "matched:text"
        if len(matches) > 1:
            return None, (f"ambiguous:text={target_text!r} matched {len(matches)} entries")
        return None, f"unresolved:no_text_match={target_text!r}"

    return None, "unresolved:no_target_signal"


def _sibling_actions(fsm: AppFSM, transition: Transition) -> list[dict[str, Any]]:
    return [
        t.action for t in fsm.transitions if t.source == transition.source and t is not transition
    ]


def _provenance_screen_ids(transition: Transition, side: str) -> list[str]:
    """Ordered, de-duplicated provenance screen ids for one side of a transition.

    ``side`` is ``"source"`` or ``"target"``. These screens are the exact captures
    before/after the action, so they deterministically contain the capture-local action
    target handle even when a merged/quotiented state spans several raw screens.
    """
    attr = "source_screen_id" if side == "source" else "target_screen_id"
    ids: list[str] = []
    for entry in transition.provenance:
        sid = getattr(entry, attr, None)
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _path_text(raw_path: Any) -> str:
    if not raw_path:
        return ""
    path = Path(str(raw_path))
    try:
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _first_observation(
    candidate_ids: list[str],
    raw_screens: dict[str, Any],
) -> dict[str, Any] | None:
    for sid in candidate_ids:
        obs = raw_screens.get(sid)
        if isinstance(obs, BaseModel):
            obs = obs.model_dump()
        if isinstance(obs, dict):
            return obs
    return None


def _screen_evidence(
    state: AbstractState | None,
    state_id: str,
    screen_ids: list[str],
    raw_screens: dict[str, Any],
) -> ScreenEvidence:
    """Build Hoare evidence for one transition side."""
    annotations = state.annotations if state is not None else None
    obs = _first_observation(screen_ids, raw_screens)
    if obs is None:
        return ScreenEvidence(
            state_id=state_id,
            alt_text=annotations.alt_text if annotations is not None else "",
            page_function=annotations.page_function if annotations is not None else "",
            display_name=annotations.display_name if annotations is not None else "",
        )

    xml_tree_path = str(obs.get("xml_tree_path") or "")
    return ScreenEvidence(
        state_id=state_id,
        screen_id=str(obs.get("screen_id") or (screen_ids[0] if screen_ids else "")),
        activity_name=str(obs.get("activity_name") or ""),
        package_name=str(obs.get("package_name") or ""),
        screenshot_path=str(obs.get("screenshot_path") or ""),
        xml_tree_path=xml_tree_path,
        compact_tree_text=str(obs.get("compact_tree_text") or "").strip(),
        xml_excerpt=_path_text(xml_tree_path),
        alt_text=annotations.alt_text if annotations is not None else "",
        page_function=annotations.page_function if annotations is not None else "",
        display_name=annotations.display_name if annotations is not None else "",
    )


def _side_registry(
    state: AbstractState,
    provenance_ids: list[str],
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None,
) -> WidgetRegistry:
    """Build a side registry, preferring provenance screens over state evidence."""
    registry = build_widget_registry_from_screen_ids(
        state.state_id,
        provenance_ids,
        raw_screens,
        app_prior,
        widget_aliases=state.annotations.widget_aliases,
    )
    if registry is not None:
        return registry
    # Fall back to the state's representative raw screen.
    return build_widget_registry(state, raw_screens, app_prior)


def _diff_summary(
    source_registry: WidgetRegistry,
    target_registry: WidgetRegistry | None,
) -> str:
    """Deterministic, lightweight source-to-target diff keyed by resource id."""
    if target_registry is None:
        return ""

    def by_resource(reg: WidgetRegistry) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for entry in reg.entries.values():
            if entry.resource_id:
                out.setdefault(entry.resource_id, entry)
        return out

    src = by_resource(source_registry)
    tgt = by_resource(target_registry)

    parts: list[str] = []

    # Resource ids gained / lost between source and target.
    added = sorted(set(tgt) - set(src))
    removed = sorted(set(src) - set(tgt))
    for rid in added:
        parts.append(f"+{rid}")
    for rid in removed:
        parts.append(f"-{rid}")

    # Changed text / checked / enabled facts for shared resource ids.
    for rid in sorted(set(src) & set(tgt)):
        s_entry = src[rid]
        t_entry = tgt[rid]
        if s_entry.text != t_entry.text:
            parts.append(f"{rid}.text:{s_entry.text!r}->{t_entry.text!r}")
        s_props = set(s_entry.readable_props)
        t_props = set(t_entry.readable_props)
        if ("is_checked" in s_props) != ("is_checked" in t_props):
            parts.append(f"{rid}.is_checked:changed")
        if ("is_enabled" in s_props) != ("is_enabled" in t_props):
            parts.append(f"{rid}.is_enabled:changed")

    return "; ".join(parts)


def _static_prior_hints(
    source: AbstractState | None,
    app_prior: AppPrior | None,
) -> list[str]:
    """List matched static-prior facts. Empty when no prior is supplied."""
    if app_prior is None:
        return []

    hints: list[str] = []
    hints.append(f"package:{app_prior.package_name}")
    if app_prior.entry_activity:
        hints.append(f"entry_activity:{app_prior.entry_activity}")

    activity = None
    if source is not None:
        activity = source.android_context.activity_name
    if activity:
        for info in app_prior.activities:
            if info.name == activity:
                label = info.label or info.predicted_function
                hints.append(f"activity:{info.name}" + (f"({label})" if label else ""))
                break

    for info in app_prior.activities:
        label = info.label or ""
        function = info.predicted_function or ""
        launcher = ":launcher" if info.is_launcher else ""
        hints.append(f"activity_prior:{info.name}{launcher} label={label!r} function={function!r}")

    for perm in app_prior.permissions:
        hints.append(f"perm:{perm.rsplit('.', 1)[-1]}")

    for name in sorted(app_prior.string_arrays):
        values = app_prior.string_arrays.get(name) or []
        rendered_values = ", ".join(repr(str(v)) for v in values)
        hints.append(f"string_array:{name} values=[{rendered_values}]")

    for name, value in sorted(app_prior.string_constants.items()):
        hints.append(f"string:{name}={value!r}")

    for decl in app_prior.widget_declarations:
        hints.append(
            "layout_widget:" f"{decl.layout_file} id={decl.widget_id!r} class={decl.widget_class!r}"
        )

    return hints


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_guard_evidence_for_transition(
    fsm: AppFSM,
    transition: Transition,
    transition_index: int,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
) -> GuardEvidence:
    """Build the :class:`GuardEvidence` view for a single transition.

    Missing source/target states or raw screens degrade gracefully to a partial
    evidence object (empty source registry, ``target_registry=None``) rather than
    crashing.
    """
    source = fsm.states.get(transition.source)
    target = fsm.states.get(transition.target)
    source_screen_ids = _provenance_screen_ids(transition, "source")
    target_screen_ids = _provenance_screen_ids(transition, "target")
    if source is not None:
        source_screen_ids = source_screen_ids or list(source.evidence.raw_screen_ids)
    if target is not None:
        target_screen_ids = target_screen_ids or list(target.evidence.raw_screen_ids)

    if source is not None:
        source_registry = _side_registry(
            source,
            source_screen_ids,
            raw_screens,
            app_prior,
        )
    else:
        source_registry = WidgetRegistry(state_id=transition.source)

    target_registry: WidgetRegistry | None = None
    if target is not None:
        target_registry = _side_registry(
            target,
            target_screen_ids,
            raw_screens,
            app_prior,
        )

    action_target_alias, alias_reason = _resolve_target_alias(transition.action, source_registry)

    return GuardEvidence(
        transition_index=transition_index,
        source_state_id=transition.source,
        target_state_id=transition.target,
        action=transition.action,
        source_state_name=source.name if source is not None else "",
        target_state_name=target.name if target is not None else "",
        source_page_function=(source.annotations.page_function if source is not None else ""),
        target_page_function=(target.annotations.page_function if target is not None else ""),
        source_screen_ids=source_screen_ids,
        target_screen_ids=target_screen_ids,
        source_screen=_screen_evidence(source, transition.source, source_screen_ids, raw_screens),
        target_screen=_screen_evidence(target, transition.target, target_screen_ids, raw_screens),
        source_registry=source_registry,
        target_registry=target_registry,
        sibling_actions=_sibling_actions(fsm, transition),
        action_target_alias=action_target_alias,
        action_target_alias_reason=alias_reason,
        replay_confidence=transition.confidence,
        low_trust=transition.low_trust,
        provenance=[p.model_dump() for p in transition.provenance],
        static_prior_hints=_static_prior_hints(source, app_prior),
        diff_summary=_diff_summary(source_registry, target_registry),
    )


def build_all_guard_evidence(
    fsm: AppFSM,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
) -> list[GuardEvidence]:
    """Build :class:`GuardEvidence` for every transition in ``fsm`` (index-ordered)."""
    return [
        build_guard_evidence_for_transition(fsm, transition, index, raw_screens, app_prior)
        for index, transition in enumerate(fsm.transitions)
    ]
