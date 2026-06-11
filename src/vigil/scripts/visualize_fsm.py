"""CLI entry point: vigil-visualize.

Visualizes a constructed FSM as a graph image using Graphviz.

Usage:
    vigil-visualize --fsm <fsm.json>
    vigil-visualize --fsm <fsm.json> --format html
    vigil-visualize --format html --gold-fsm <gold/fsm.json> --fsm <explored/fsm.json>
    vigil-visualize --fsm <fsm.json> --output <output.png>
    vigil-visualize --fsm <fsm.json> --output <output.svg> --format svg

When --output is omitted, generated files are written under output_docs/.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.paths import OUTPUT_DOCS_DIR, resolve_generated_output_path

_ACTION_COLORS: dict[str, str] = {
    "click": "#4a4a4a",
    "long_press": "#2e7d32",
    "scroll_up": "#1565c0",
    "scroll_down": "#1565c0",
    "navigate_back": "#b71c1c",
    "navigate_home": "#b71c1c",
    "input_text": "#e65100",
}

_ACTIVITY_COLORS = [
    "#dcedc8",
    "#b3e5fc",
    "#f8bbd0",
    "#fff9c4",
    "#d1c4e9",
    "#ffe0b2",
    "#b2dfdb",
    "#ffccbc",
    "#c5cae9",
    "#f0f4c3",
]

_INITIAL_COLOR = "#1565c0"
_INITIAL_FONT_COLOR = "white"

_SAFE_STATE_FIELDS = (
    "state_id",
    "name",
    "hierarchy_level",
    "parent_state",
    "kind",
    "android_context",
    "abstraction",
)


def render_fsm(
    fsm_path: Path,
    output_path: Path,
    fmt: str = "png",
    layout: str = "dot",
    show_guards: bool = False,
    show_counts: bool = False,
    cluster_activities: bool = True,
    max_label_len: int = 20,
) -> None:
    """Render an FSM to a graph image."""
    try:
        import graphviz
    except ImportError as err:
        logger.error(
            "graphviz package not installed. Install with: uv add graphviz\n"
            "Also need system binary: brew install graphviz"
        )
        raise SystemExit(1) from err

    from vigil.models.fsm import AppFSM

    fsm = AppFSM.deserialize(fsm_path)
    logger.info(f"Loaded {fsm}")

    dot = graphviz.Digraph(
        name=fsm.app_package,
        format=fmt,
        engine=layout,
    )
    dot.attr(
        rankdir="TB",
        fontname="Helvetica Neue,Helvetica,Arial,sans-serif",
        fontsize="12",
        bgcolor="white",
        pad="0.8",
        nodesep="0.6",
        ranksep="0.8",
        label=(
            f"\\n{fsm.app_package}  ({len(fsm.states)} states, {len(fsm.transitions)} transitions)"
        ),
        labelloc="t",
        labeljust="l",
    )
    dot.attr(
        "node",
        fontname="Helvetica Neue,Helvetica,Arial,sans-serif",
        fontsize="10",
        style="filled,rounded",
        shape="box",
        margin="0.15,0.08",
        penwidth="1.2",
    )
    dot.attr(
        "edge",
        fontname="Helvetica Neue,Helvetica,Arial,sans-serif",
        fontsize="8",
        arrowsize="0.7",
        penwidth="1.0",
    )

    activities = sorted({s.android_context.activity_name or "" for s in fsm.states.values()})
    activity_color = {
        act: _ACTIVITY_COLORS[i % len(_ACTIVITY_COLORS)] for i, act in enumerate(activities)
    }

    activity_states: dict[str, list[str]] = {}
    for state in fsm.states.values():
        act = state.android_context.activity_name or ""
        if act not in activity_states:
            activity_states[act] = []
        activity_states[act].append(state.state_id)

    def _add_node(parent_graph: graphviz.Digraph, state_id: str) -> None:
        state = fsm.states[state_id]
        name = _truncate(state.name, max_label_len)
        is_initial = state_id == fsm.initial_state

        if is_initial:
            parent_graph.node(
                state_id,
                label=name,
                fillcolor=_INITIAL_COLOR,
                fontcolor=_INITIAL_FONT_COLOR,
                penwidth="2.5",
            )
        else:
            fill = activity_color.get(state.android_context.activity_name or "", "#e8e8e8")
            parent_graph.node(state_id, label=name, fillcolor=fill)

    if cluster_activities and len(activities) > 1:
        for act in activities:
            sids = activity_states.get(act, [])
            if not sids:
                continue
            short_act = act.rsplit(".", 1)[-1] if act else "unknown"
            with dot.subgraph(name=f"cluster_{short_act}") as sub:
                sub.attr(
                    label=short_act,
                    style="dashed,rounded",
                    color="#999999",
                    fontsize="9",
                    fontcolor="#666666",
                    margin="12",
                )
                for sid in sids:
                    _add_node(sub, sid)
    else:
        for state_id in fsm.states:
            _add_node(dot, state_id)

    for t in fsm.transitions:
        action_type = t.action.get("type", "?")
        color = _ACTION_COLORS.get(action_type, "#888888")
        is_back = action_type in ("navigate_back", "navigate_home")

        label_parts: list[str] = []
        if not is_back:
            label_parts.append(action_type)
        else:
            label_parts.append("back")

        if show_counts and t.observed_count > 1:
            label_parts.append(f"×{t.observed_count}")
        if show_guards and t.guard:
            guard_short = _truncate(t.guard, 30)
            label_parts.append(f"[{guard_short}]")

        label = " ".join(label_parts)

        style = "dashed" if is_back else "solid"
        dot.edge(
            t.source,
            t.target,
            label=label,
            color=color,
            fontcolor=color,
            style=style,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_stem = str(output_path).removesuffix(f".{fmt}")
    dot.render(out_stem, cleanup=True)
    logger.info(f"FSM graph rendered to {output_path}")


def default_output_path(fsm_path: Path, fmt: str) -> Path:
    """Default generated visualization path under output_docs/."""
    app_slug = _infer_app_slug(fsm_path)
    if fmt == "html":
        return OUTPUT_DOCS_DIR / app_slug / "fsm.html"
    return OUTPUT_DOCS_DIR / f"{app_slug}_fsm.{fmt}"


def _infer_app_slug(fsm_path: Path) -> str:
    try:
        payload = json.loads(fsm_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}

    app_package = payload.get("app_package") if isinstance(payload, dict) else None
    if isinstance(app_package, str) and app_package:
        return app_package.replace(".", "_")
    if fsm_path.stem == "fsm" and fsm_path.parent.name:
        return fsm_path.parent.name
    return fsm_path.stem or "fsm"


def _fsm_to_view_dict(
    fsm: Any,
    show_guards: bool = False,
    include_sensitive_details: bool = False,
    screens_dir: Path | None = None,
) -> dict[str, Any]:
    """Convert an AppFSM to a frontend-friendly, JSON-serializable dict."""
    activities = sorted({s.android_context.activity_name or "" for s in fsm.states.values()})
    activity_colors = {
        act: _ACTIVITY_COLORS[i % len(_ACTIVITY_COLORS)] for i, act in enumerate(activities)
    }

    states = [
        _state_to_view_dict(
            state,
            include_sensitive_details=include_sensitive_details,
            screens_dir=screens_dir,
        )
        for state in fsm.states.values()
    ]
    transitions = [
        _transition_to_view_dict(
            transition,
            show_guards=show_guards,
            include_sensitive_details=include_sensitive_details,
        )
        for transition in fsm.transitions
    ]

    return {
        "app_package": fsm.app_package,
        "version": fsm.version,
        "initial_state": fsm.initial_state,
        "summary": {
            "num_states": len(fsm.states),
            "num_transitions": len(fsm.transitions),
        },
        "action_colors": dict(_ACTION_COLORS),
        "activity_colors": activity_colors,
        "initial_color": _INITIAL_COLOR,
        "initial_font_color": _INITIAL_FONT_COLOR,
        "states": states,
        "transitions": transitions,
    }


def _state_to_view_dict(
    state: Any,
    include_sensitive_details: bool,
    screens_dir: Path | None = None,
) -> dict[str, Any]:
    """Convert a state to the HTML view schema.

    Safe mode emits a redacted nested subset (no evidence, no annotations,
    no invariant specs, no legacy invariants, no abstraction selectors /
    parameter schemas). Sensitive mode emits the full nested
    ``state.model_dump(mode="json")`` plus optional raw screenshots.
    """
    safe_view: dict[str, Any] = {
        "state_id": state.state_id,
        "name": state.name,
        "hierarchy_level": state.hierarchy_level.value,
        "parent_state": state.parent_state,
        "kind": state.kind.value,
        "android_context": state.android_context.model_dump(mode="json"),
        # Redacted abstraction projection: keep classification + template
        # identity but drop selector / parameter dicts that can leak
        # resource ids, sample text values, or binding fingerprints.
        "abstraction": {
            "container_type": state.abstraction.container_type.value,
            "template_id": state.abstraction.template_id,
            "template_role": state.abstraction.template_role,
        },
    }
    if not include_sensitive_details:
        return safe_view
    full = state.model_dump(mode="json")
    if screens_dir is not None:
        images = _load_screen_images(state.evidence.raw_screen_ids, screens_dir)
        if images:
            full["raw_screen_images"] = images
    return full


def _load_screen_images(screen_ids: list[str], screens_dir: Path) -> list[dict[str, str]]:
    """Load raw screenshot files as base64 data URIs.

    Looks up '<screens_dir>/<screen_id>.png' (or .jpg/.jpeg/.webp) per id.
    Missing files are silently skipped — log at debug level.
    """
    images: list[dict[str, str]] = []
    extensions = (".png", ".jpg", ".jpeg", ".webp")
    for sid in screen_ids:
        path: Path | None = None
        for ext in extensions:
            candidate = screens_dir / f"{sid}{ext}"
            if candidate.exists():
                path = candidate
                break
        if path is None:
            logger.debug(f"screenshot not found for screen_id={sid} in {screens_dir}")
            continue
        try:
            raw = path.read_bytes()
        except OSError as err:
            logger.debug(f"failed to read screenshot {path}: {err}")
            continue
        mime, _ = mimetypes.guess_type(path.name)
        mime = mime or "image/png"
        data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        images.append({"screen_id": sid, "data_uri": data_uri})
    return images


def _transition_to_view_dict(
    transition: Any,
    show_guards: bool,
    include_sensitive_details: bool,
) -> dict[str, Any]:
    """Convert a transition to the HTML view schema."""
    if include_sensitive_details:
        transition_dict = transition.model_dump(mode="json")
        if not show_guards:
            transition_dict.pop("guard", None)
        return transition_dict

    view = {
        "source": transition.source,
        "target": transition.target,
        "action": {"type": transition.action.get("type")},
        "confidence": transition.confidence,
        "observed_count": transition.observed_count,
    }
    if show_guards and transition.guard:
        view["guard"] = transition.guard
    return view


def render_fsm_compare_html(
    *,
    gold_fsm_path: Path,
    explored_fsm_path: Path,
    output_path: Path,
    screens_dir: Path | None = None,
    max_label_len: int = 24,
) -> None:
    """Render a split-screen gold-vs-explored FSM comparison HTML file.

    The left pane shows the hand-authored fidelity gold FSM. The right pane shows the
    explored/enriched FSM, including state invariants, transition guards, and optional
    screenshot links in the sidebar. Screenshots are linked with relative paths instead of
    embedded as data URIs so large exploration runs stay usable.
    """
    from vigil.models.fsm import AppFSM

    explored = AppFSM.deserialize(explored_fsm_path)
    logger.info(f"Loaded explored {explored}")

    explored_view = _fsm_to_view_dict(
        explored,
        show_guards=True,
        include_sensitive_details=True,
        screens_dir=None,
    )
    _attach_parsed_logic_clauses(explored_view)
    if screens_dir is not None:
        _attach_linked_screen_images(explored_view, screens_dir, output_path)

    view = {
        "title": _infer_compare_title(gold_fsm_path, explored_fsm_path),
        "golden": _gold_fsm_to_view_dict(gold_fsm_path),
        "explored": explored_view,
        "options": {
            "max_label_len": max_label_len,
            "screens_dir": str(screens_dir) if screens_dir else None,
        },
    }

    payload = json.dumps(view, ensure_ascii=False).replace("</", "<\\/")
    html = _COMPARE_HTML_TEMPLATE.replace("__COMPARE_DATA__", payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"Interactive FSM comparison HTML written to {output_path}")


def _infer_compare_title(gold_fsm_path: Path, explored_fsm_path: Path) -> str:
    try:
        payload = json.loads(gold_fsm_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    app_id = payload.get("app_id") if isinstance(payload, dict) else None
    if isinstance(app_id, str) and app_id:
        return app_id
    return _infer_app_slug(explored_fsm_path)


def _attach_linked_screen_images(
    explored_view: dict[str, Any],
    screens_dir: Path,
    output_path: Path,
) -> None:
    for state in explored_view.get("states", []):
        if not isinstance(state, dict):
            continue
        evidence = state.get("evidence")
        if not isinstance(evidence, dict):
            continue
        screen_ids = evidence.get("raw_screen_ids")
        if not isinstance(screen_ids, list):
            continue
        images = _load_screen_image_links(
            [str(sid) for sid in screen_ids],
            screens_dir,
            output_path,
        )
        if images:
            state["raw_screen_images"] = images


def _load_screen_image_links(
    screen_ids: list[str],
    screens_dir: Path,
    output_path: Path,
) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    extensions = (".png", ".jpg", ".jpeg", ".webp")
    for sid in screen_ids:
        path: Path | None = None
        for ext in extensions:
            candidate = screens_dir / f"{sid}{ext}"
            if candidate.exists():
                path = candidate
                break
        if path is None:
            logger.debug(f"screenshot not found for screen_id={sid} in {screens_dir}")
            continue
        rel = os.path.relpath(path, start=output_path.parent)
        images.append({"screen_id": sid, "src": Path(rel).as_posix()})
    return images


def _attach_parsed_logic_clauses(explored_view: dict[str, Any]) -> None:
    try:
        from vigil.symbolic.dsl_evaluator import DSLEvaluator

        evaluator = DSLEvaluator()
    except Exception as err:
        logger.warning(f"DSL parser unavailable for visualization clauses: {err}")
        return

    for state in explored_view.get("states", []):
        if not isinstance(state, dict):
            continue
        invariants = state.get("invariant_specs")
        if not isinstance(invariants, list):
            continue
        for invariant in invariants:
            if not isinstance(invariant, dict):
                continue
            expr = invariant.get("expr")
            if isinstance(expr, str) and expr:
                invariant["logic"] = evaluator.parse_logic_clauses(expr)

    for transition in explored_view.get("transitions", []):
        if not isinstance(transition, dict):
            continue
        guard = transition.get("guard")
        if isinstance(guard, str) and guard:
            transition["guard_logic"] = evaluator.parse_logic_clauses(guard)
        postcondition_expr = transition.get("postcondition")
        if isinstance(postcondition_expr, str) and postcondition_expr:
            transition["postcondition_logic"] = evaluator.parse_logic_clauses(postcondition_expr)
        postcondition = transition.get("postcondition_contract")
        if isinstance(postcondition, dict):
            effects = _effect_requirements_view(postcondition)
            if effects:
                transition["postcondition_effects"] = effects


def _typed_contract_logic(contract: dict[str, Any]) -> dict[str, Any]:
    predicates = contract.get("predicates")
    clauses: list[dict[str, str]] = []
    if isinstance(predicates, list):
        for predicate in predicates:
            if not isinstance(predicate, dict):
                continue
            clauses.append(
                {
                    "text": _format_predicate_clause(predicate),
                    "predicate_type": str(predicate.get("predicate_type") or "predicate"),
                    "source": str(predicate.get("source") or ""),
                }
            )
    return {
        "status": "typed",
        "parser": "guard_contract",
        "root": {"operator": "and" if len(clauses) > 1 else "atom"},
        "clauses": clauses,
    }


def _effect_requirements_view(contract: dict[str, Any]) -> list[dict[str, str]]:
    effects = contract.get("effect_requirements")
    if not isinstance(effects, list):
        return []

    rows: list[dict[str, str]] = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        name = str(effect.get("name") or "effect")
        kind = str(effect.get("effect_kind") or "effect")
        description = str(effect.get("description") or "").strip()
        evidence = str(effect.get("evidence") or "").strip()
        unsupported_reason = str(effect.get("unsupported_reason") or "").strip()
        text = f"{name}: {description}" if description else name
        if evidence:
            text = f"{text} | evidence: {evidence}"
        if unsupported_reason:
            text = f"{text} | unsupported: {unsupported_reason}"
        rows.append(
            {
                "text": text,
                "effect_kind": kind,
                "source": str(effect.get("source") or ""),
                "unsupported_reason": unsupported_reason,
            }
        )
    return rows


def _format_predicate_clause(predicate: dict[str, Any]) -> str:
    predicate_type = str(predicate.get("predicate_type") or "predicate")
    element = _string_or_none(predicate.get("element"))
    property_name = _string_or_none(predicate.get("property"))
    operator = _string_or_none(predicate.get("operator"))
    expected = predicate.get("expected")
    expected_text = _format_value_ref(expected) if isinstance(expected, dict) else None
    args = predicate.get("args") if isinstance(predicate.get("args"), dict) else {}

    if predicate_type == "in_state":
        state = _first_nonempty(args.get("state"), _value_ref_literal(expected), expected_text)
        return f"in_state({state or '?'})"
    if predicate_type == "read":
        left = f"read({element or '?'}, {property_name or '?'})"
    elif predicate_type == "action":
        left = f"action({property_name or element or '?'})"
    elif predicate_type == "value":
        left = f"value({element or property_name or '?'})"
    elif predicate_type == "contains":
        subject = _join_subject(element, property_name)
        if expected_text:
            return f"contains({subject or '?'}, {expected_text})"
        return f"contains({subject or '?'})"
    elif predicate_type == "count":
        left = f"count({element or property_name or '*'})"
    elif predicate_type == "time_in":
        left = f"time_in({element or property_name or '?'})"
    else:
        left = predicate_type

    if operator and expected_text:
        return f"{left} {operator} {expected_text}"
    if expected_text:
        return f"{left} == {expected_text}"
    return left


def _format_value_ref(ref: dict[str, Any]) -> str | None:
    kind = str(ref.get("kind") or "")
    if kind == "literal":
        return _format_literal(ref.get("value"))
    if kind == "intent":
        slot = _string_or_none(ref.get("slot"))
        return f"$intent.{slot or '?'}"
    if kind == "action":
        prop = _string_or_none(ref.get("property"))
        return f"action({prop or '?'})"
    if kind == "read":
        element = _string_or_none(ref.get("element"))
        prop = _string_or_none(ref.get("property"))
        return f"read({element or '?'}, {prop or '?'})"
    return None


def _format_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _value_ref_literal(ref: Any) -> str | None:
    if not isinstance(ref, dict) or ref.get("kind") != "literal":
        return None
    value = ref.get("value")
    if value is None:
        return None
    return str(value)


def _join_subject(element: str | None, property_name: str | None) -> str:
    parts = [part for part in (element, property_name) if part]
    return ".".join(parts)


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return None


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value


def _gold_fsm_to_view_dict(gold_fsm_path: Path) -> dict[str, Any]:
    payload = json.loads(gold_fsm_path.read_text(encoding="utf-8"))
    states_raw = payload.get("states", [])
    actions_raw = payload.get("actions", [])
    transitions_raw = payload.get("transitions", [])
    actions_by_name = {
        str(action.get("name")): action
        for action in actions_raw
        if isinstance(action, dict) and action.get("name")
    }

    states: list[dict[str, Any]] = []
    for state in states_raw:
        if not isinstance(state, dict):
            continue
        sid = str(state.get("id") or "")
        if not sid:
            continue
        states.append(
            {
                "state_id": sid,
                "name": sid,
                "screen_marker": state.get("screen_marker"),
                "dialog": state.get("dialog", False),
                "anchor": state.get("anchor"),
                "terminal": sid in set(payload.get("terminal_states", []) or []),
                "spec": state,
            }
        )

    transitions: list[dict[str, Any]] = []
    for row in transitions_raw:
        if not isinstance(row, dict):
            continue
        transitions.append(_gold_transition_to_view_dict(row, actions_by_name, len(transitions)))

    explicit_count = len(transitions)
    for row in _expand_gold_global_navigation(payload.get("global_navigation") or {}):
        transitions.append(_gold_transition_to_view_dict(row, actions_by_name, len(transitions)))

    action_names = {str(action.get("name")) for action in actions_raw if isinstance(action, dict)}
    return {
        "app_package": payload.get("app_id") or gold_fsm_path.parent.parent.name,
        "version": payload.get("model") or "gold",
        "initial_state": payload.get("initial_state"),
        "summary": {
            "num_states": len(states),
            "num_transitions": len(transitions),
            "explicit_transitions": explicit_count,
            "global_nav_edges": len(transitions) - explicit_count,
            "num_actions": len(action_names),
        },
        "action_colors": dict(_ACTION_COLORS),
        "activity_colors": {"gold": "#fff3bf"},
        "initial_color": "#7a4b00",
        "initial_font_color": "white",
        "states": states,
        "transitions": transitions,
        "transition_kinds": payload.get("transition_kinds", {}),
        "templates": payload.get("templates", {}),
    }


def _gold_transition_to_view_dict(
    row: dict[str, Any],
    actions_by_name: dict[str, dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    action_name = str(row.get("action") or "")
    action_spec = actions_by_name.get(action_name, {})
    action_type = _gold_action_type(action_name, action_spec)
    view = {
        "index": index,
        "source": str(row.get("from") or row.get("source") or ""),
        "target": str(row.get("to") or row.get("target") or ""),
        "action": {
            "type": action_type,
            "name": action_name,
            "query": action_spec.get("query"),
        },
        "kind": row.get("kind"),
        "guard": row.get("guard"),
        "binds": row.get("binds"),
        "spec": row,
    }
    if row.get("global_navigation"):
        view["global_navigation"] = True
    return view


def _gold_action_type(action_name: str, action_spec: dict[str, Any]) -> str:
    raw = str(action_spec.get("type") or "")
    if raw == "input":
        return "input_text"
    if raw == "system_back":
        return "navigate_back"
    if raw:
        return raw
    if action_name == "system.back":
        return "navigate_back"
    return "click"


def _expand_gold_global_navigation(global_navigation: dict[str, Any]) -> list[dict[str, Any]]:
    visible_on = [
        str(sid)
        for sid in (global_navigation.get("visible_on") or [])
        if isinstance(sid, str) and sid
    ]
    actions = [a for a in (global_navigation.get("actions") or []) if isinstance(a, dict)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        name = str(action.get("action") or "")
        if not name:
            continue
        grouped.setdefault(name, []).append(action)

    rows: list[dict[str, Any]] = []
    for source in visible_on:
        for action_name, choices in grouped.items():
            chosen = _choose_gold_global_nav_choice(choices, source)
            if chosen is None:
                continue
            row = {
                "from": source,
                "action": action_name,
                "to": chosen.get("to"),
                "kind": chosen.get("kind", "nav"),
                "guard": chosen.get("guard"),
                "global_navigation": True,
            }
            rows.append(row)
    return rows


def _choose_gold_global_nav_choice(
    choices: list[dict[str, Any]],
    source: str,
) -> dict[str, Any] | None:
    if not choices:
        return None
    if len(choices) == 1:
        return choices[0]
    for choice in choices:
        guard = str(choice.get("guard") or "")
        if f"current_screen == {source}" in guard:
            return choice
    for choice in choices:
        guard = str(choice.get("guard") or "")
        match = re.search(r"current_screen not in \{([^}]*)\}", guard)
        if not match:
            continue
        excluded = {item.strip() for item in match.group(1).split(",")}
        if source not in excluded:
            return choice
    return choices[0]


def render_fsm_html(
    fsm_path: Path,
    output_path: Path,
    show_guards: bool = False,
    show_counts: bool = False,
    max_label_len: int = 20,
    include_sensitive_details: bool = False,
    screens_dir: Path | None = None,
) -> None:
    """Render an FSM as a self-contained interactive HTML file."""
    from vigil.models.fsm import AppFSM

    fsm = AppFSM.deserialize(fsm_path)
    logger.info(f"Loaded {fsm}")

    if screens_dir is not None and not include_sensitive_details:
        logger.warning(
            "--screens-dir was provided but --include-sensitive-details is not set; "
            "screenshots are sensitive and will NOT be embedded."
        )
        screens_dir = None

    view = _fsm_to_view_dict(
        fsm,
        show_guards=show_guards,
        include_sensitive_details=include_sensitive_details,
        screens_dir=screens_dir,
    )
    view["options"] = {
        "show_guards": show_guards,
        "show_counts": show_counts,
        "max_label_len": max_label_len,
        "include_sensitive_details": include_sensitive_details,
    }

    payload = json.dumps(view, ensure_ascii=False)
    # Avoid prematurely closing the <script> if any field contains '</script>'.
    payload = payload.replace("</", "<\\/")

    html = _HTML_TEMPLATE.replace("__FSM_DATA__", payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"Interactive FSM HTML written to {output_path}")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vigil FSM Viewer</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    height: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #222;
    background: #fafafa;
  }
  #app { display: flex; flex-direction: column; height: 100vh; }
  header {
    padding: 10px 16px;
    background: #1565c0;
    color: white;
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
    align-items: baseline;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .meta { font-size: 12px; opacity: 0.9; }
  header .meta b { font-weight: 600; }
  main { flex: 1; display: flex; min-height: 0; }
  #canvas-wrap {
    flex: 1;
    position: relative;
    background: #ffffff;
    overflow: hidden;
    border-right: 1px solid #e0e0e0;
  }
  svg#canvas { width: 100%; height: 100%; cursor: grab; display: block; }
  svg#canvas.panning { cursor: grabbing; }
  #sidebar {
    width: 380px;
    max-width: 40vw;
    overflow-y: auto;
    padding: 14px 16px;
    background: #ffffff;
  }
  #sidebar h2 { font-size: 14px; margin: 0 0 8px; color: #1565c0; }
  #sidebar .empty { color: #888; padding: 24px 0; text-align: center; font-style: italic; }
  #sidebar .field { margin: 6px 0; display: flex; gap: 8px; align-items: baseline; }
  #sidebar .field .k {
    color: #666;
    font-size: 11px;
    min-width: 130px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }
  #sidebar .field .v {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
    font-size: 12px;
    flex: 1;
  }
  #sidebar .section { margin-top: 14px; padding-top: 10px; border-top: 1px solid #eee; }
  #sidebar .section h3 {
    font-size: 12px;
    margin: 0 0 6px;
    color: #333;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  #sidebar ul { margin: 0; padding-left: 18px; }
  #sidebar li {
    margin: 2px 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    word-break: break-all;
  }
  #sidebar .tr {
    padding: 6px 8px;
    margin: 4px 0;
    background: #f5f7fa;
    border-left: 3px solid #ccc;
    border-radius: 3px;
    font-size: 12px;
  }
  #sidebar .tr .arrow { color: #1565c0; font-weight: 600; }
  #sidebar .tr .row {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
  }
  #sidebar .muted { color: #999; }
  #sidebar .screens { display: flex; flex-direction: column; gap: 10px; }
  #sidebar .screen {
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 6px;
    background: #fafafa;
  }
  #sidebar .screen .sid {
    font-size: 11px;
    color: #666;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    margin-bottom: 4px;
    word-break: break-all;
  }
  #sidebar .screen img {
    width: 100%;
    height: auto;
    display: block;
    border-radius: 2px;
    cursor: zoom-in;
    background: #000;
  }
  #lightbox {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.85);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    cursor: zoom-out;
  }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 95vw; max-height: 95vh; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
  .node circle { stroke: #333; stroke-width: 1.2; cursor: pointer; transition: stroke-width 0.1s; }
  .node.initial circle { stroke-width: 3; stroke: #0d3a6b; }
  .node.selected circle { stroke: #ff6f00; stroke-width: 3.5; }
  .node text {
    pointer-events: none;
    font-size: 10px;
    fill: #222;
    text-anchor: middle;
    user-select: none;
  }
  .edge path { fill: none; stroke-width: 1.3; }
  .edge text { font-size: 9px; fill: #444; pointer-events: none; user-select: none; }
  footer {
    padding: 4px 12px;
    font-size: 11px;
    color: #888;
    border-top: 1px solid #eee;
    background: #fafafa;
  }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1 id="app-title">Vigil FSM</h1>
    <div class="meta">version <b id="m-version"></b></div>
    <div class="meta"><b id="m-states"></b> states</div>
    <div class="meta"><b id="m-transitions"></b> transitions</div>
    <div class="meta">initial: <b id="m-initial"></b></div>
  </header>
  <main>
    <div id="canvas-wrap">
      <svg id="canvas">
        <defs id="defs"></defs>
        <g id="viewport">
          <g id="edges"></g>
          <g id="nodes"></g>
        </g>
      </svg>
    </div>
    <aside id="sidebar">
      <div id="sidebar-content">
        <div class="empty">Click a state to view details.</div>
      </div>
    </aside>
  </main>
  <footer>Drag nodes &middot; drag empty space to pan &middot; scroll to zoom</footer>
</div>
<div id="lightbox"><img alt=""></div>

<script>
const FSM_DATA = __FSM_DATA__;

(function() {
  const opts = FSM_DATA.options || {};
  const MAX_LABEL = opts.max_label_len || 20;
  const SHOW_GUARDS = !!opts.show_guards;
  const SHOW_COUNTS = !!opts.show_counts;
  const INCLUDE_SENSITIVE_DETAILS = !!opts.include_sensitive_details;

  // Header
  document.getElementById('app-title').textContent = FSM_DATA.app_package || 'Vigil FSM';
  document.getElementById('m-version').textContent = FSM_DATA.version || '';
  document.getElementById('m-states').textContent = FSM_DATA.summary.num_states;
  document.getElementById('m-transitions').textContent = FSM_DATA.summary.num_transitions;
  document.getElementById('m-initial').textContent = FSM_DATA.initial_state || '(none)';

  const statesById = {};
  FSM_DATA.states.forEach(s => { statesById[s.state_id] = s; });

  // Index transitions for sidebar
  const outgoing = {}, incoming = {};
  FSM_DATA.transitions.forEach(t => {
    (outgoing[t.source] = outgoing[t.source] || []).push(t);
    (incoming[t.target] = incoming[t.target] || []).push(t);
  });

  // ---- Layout (simple force-directed) ----
  const W = 1200, H = 800;
  const nodes = FSM_DATA.states.map((s, i) => {
    const point = initialPosition(i, FSM_DATA.states.length);
    return {
      id: s.state_id,
      x: point.x,
      y: point.y,
      vx: 0, vy: 0,
      data: s,
    };
  });
  const nodeIndex = {};
  nodes.forEach(n => { nodeIndex[n.id] = n; });
  const links = FSM_DATA.transitions
    .map(t => ({ source: nodeIndex[t.source], target: nodeIndex[t.target], data: t }))
    .filter(l => l.source && l.target);

  function initialPosition(i, total) {
    if (total > 100) {
      const cols = Math.ceil(Math.sqrt(total));
      const spacing = 72;
      const row = Math.floor(i / cols);
      const col = i % cols;
      return {
        x: W / 2 + (col - (cols - 1) / 2) * spacing,
        y: H / 2 + (row - Math.floor(total / cols) / 2) * spacing,
      };
    }
    const angle = (i / Math.max(1, total)) * Math.PI * 2;
    const radius = Math.max(180, Math.min(340, total * 8));
    return {
      x: W / 2 + Math.cos(angle) * radius,
      y: H / 2 + Math.sin(angle) * radius,
    };
  }

  const edgeGroups = {};
  links.forEach(l => {
    const key = l.source.id + '\u2192' + l.target.id;
    (edgeGroups[key] = edgeGroups[key] || []).push(l);
  });
  Object.values(edgeGroups).forEach(group => {
    group.forEach((l, i) => {
      l.parallelIndex = i;
      l.parallelCount = group.length;
      l.parallelOffset = (i - (group.length - 1) / 2) * 28;
    });
  });

  const totalIterations = Math.max(50, Math.min(300, 5000 / nodes.length));
  const simulationChunkSize = 20;
  let completedIterations = 0;

  function simulateStep(iterations) {
    const k = 80;          // ideal spring length
    const rep = 9000;      // repulsion
    const springK = 0.05;
    const damping = 0.85;
    for (let iter = 0; iter < iterations; iter++) {
      // repulsion
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy + 0.01;
          let d = Math.sqrt(d2);
          let f = rep / d2;
          let fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
      }
      // springs
      links.forEach(l => {
        const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
        const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const f = (d - k) * springK;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        l.source.vx += fx; l.source.vy += fy;
        l.target.vx -= fx; l.target.vy -= fy;
      });
      // center pull
      nodes.forEach(n => {
        n.vx += (W / 2 - n.x) * 0.002;
        n.vy += (H / 2 - n.y) * 0.002;
      });
      // integrate
      nodes.forEach(n => {
        n.vx *= damping; n.vy *= damping;
        n.x += n.vx; n.y += n.vy;
      });
    }
  }

  function runSimulationChunk() {
    if (completedIterations >= totalIterations) return;
    const nextIterations = Math.min(simulationChunkSize, totalIterations - completedIterations);
    simulateStep(nextIterations);
    completedIterations += nextIterations;
    updatePositions();
    window.requestAnimationFrame(runSimulationChunk);
  }

  // ---- Render SVG ----
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('canvas');
  const viewport = document.getElementById('viewport');
  const edgesG = document.getElementById('edges');
  const nodesG = document.getElementById('nodes');
  const defs = document.getElementById('defs');

  // Build arrow markers per action color
  const allColors = new Set(Object.values(FSM_DATA.action_colors));
  allColors.add('#888888');
  allColors.forEach(c => {
    const marker = document.createElementNS(SVG_NS, 'marker');
    marker.setAttribute('id', 'arrow-' + colorId(c));
    marker.setAttribute('viewBox', '0 0 10 10');
    marker.setAttribute('refX', '9');
    marker.setAttribute('refY', '5');
    marker.setAttribute('markerWidth', '7');
    marker.setAttribute('markerHeight', '7');
    marker.setAttribute('orient', 'auto-start-reverse');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
    path.setAttribute('fill', c);
    marker.appendChild(path);
    defs.appendChild(marker);
  });

  function colorId(c) { return c.replace('#', ''); }
  function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + '…' : (s || ''); }

  function edgeLabel(t) {
    const at = (t.action && t.action.type) || '?';
    const isBack = (at === 'navigate_back' || at === 'navigate_home');
    const parts = [isBack ? 'back' : at];
    if (SHOW_COUNTS && t.observed_count > 1) parts.push('×' + t.observed_count);
    if (SHOW_GUARDS && t.guard) parts.push('[' + truncate(t.guard, 30) + ']');
    return parts.join(' ');
  }

  function nodeFill(s) {
    if (s.state_id === FSM_DATA.initial_state) return FSM_DATA.initial_color;
    const activity = (s.android_context && s.android_context.activity_name) || '';
    return FSM_DATA.activity_colors[activity] || '#e8e8e8';
  }

  function nodeRadius(s) {
    const base = 22;
    const extra = Math.min(10, (s.name || s.state_id).length * 0.3);
    return base + extra;
  }

  // Edges
  const linkEls = links.map(l => {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'edge');
    const path = document.createElementNS(SVG_NS, 'path');
    const at = (l.data.action && l.data.action.type) || '?';
    const color = FSM_DATA.action_colors[at] || '#888888';
    const isBack = (at === 'navigate_back' || at === 'navigate_home');
    path.setAttribute('stroke', color);
    if (isBack) path.setAttribute('stroke-dasharray', '4,3');
    path.setAttribute('marker-end', 'url(#arrow-' + colorId(color) + ')');
    g.appendChild(path);
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('fill', color);
    label.textContent = edgeLabel(l.data);
    g.appendChild(label);
    edgesG.appendChild(g);
    l._path = path; l._label = label;
    return l;
  });

  // Nodes
  let selectedId = null;
  const nodeEls = nodes.map(n => {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'node' + (n.id === FSM_DATA.initial_state ? ' initial' : ''));
    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('r', nodeRadius(n.data));
    circle.setAttribute('fill', nodeFill(n.data));
    const text = document.createElementNS(SVG_NS, 'text');
    text.setAttribute('dy', '0.35em');
    if (n.id === FSM_DATA.initial_state) text.setAttribute('fill', FSM_DATA.initial_font_color);
    text.textContent = truncate(n.data.name || n.id, MAX_LABEL);
    g.appendChild(circle);
    g.appendChild(text);
    nodesG.appendChild(g);
    n._g = g; n._circle = circle; n._text = text;

    g.addEventListener('click', (ev) => {
      ev.stopPropagation();
      selectState(n.id);
    });
    attachDrag(n);
    return n;
  });

  function updatePositions() {
    nodeEls.forEach(n => {
      n._g.setAttribute('transform', 'translate(' + n.x + ',' + n.y + ')');
    });
    linkEls.forEach(l => {
      if (l.source === l.target) {
        const r = nodeRadius(l.source.data);
        const loopLift = r + 34 + (l.parallelIndex || 0) * 16;
        const sx = l.source.x + r * 0.65;
        const sy = l.source.y - r * 0.65;
        const tx = l.source.x - r * 0.65;
        const ty = l.source.y - r * 0.65;
        const c1x = l.source.x + loopLift;
        const c1y = l.source.y - loopLift;
        const c2x = l.source.x - loopLift;
        const c2y = l.source.y - loopLift;
        const loopPath = 'M ' + sx + ' ' + sy
          + ' C ' + c1x + ' ' + c1y
          + ' ' + c2x + ' ' + c2y
          + ' ' + tx + ' ' + ty;
        l._path.setAttribute('d', loopPath);
        l._label.setAttribute('x', l.source.x);
        l._label.setAttribute('y', l.source.y - loopLift - 4);
        return;
      }
      const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const sr = nodeRadius(l.source.data), tr = nodeRadius(l.target.data) + 6;
      const sx = l.source.x + (dx / d) * sr;
      const sy = l.source.y + (dy / d) * sr;
      const tx = l.target.x - (dx / d) * tr;
      const ty = l.target.y - (dy / d) * tr;
      const offset = l.parallelOffset || 0;
      if (offset === 0) {
        l._path.setAttribute('d', 'M ' + sx + ' ' + sy + ' L ' + tx + ' ' + ty);
        l._label.setAttribute('x', (sx + tx) / 2);
        l._label.setAttribute('y', (sy + ty) / 2 - 3);
        return;
      }
      const nx = -dy / d, ny = dx / d;
      const mx = (sx + tx) / 2 + nx * offset;
      const my = (sy + ty) / 2 + ny * offset;
      l._path.setAttribute('d', 'M ' + sx + ' ' + sy + ' Q ' + mx + ' ' + my + ' ' + tx + ' ' + ty);
      l._label.setAttribute('x', mx);
      l._label.setAttribute('y', my - 3);
    });
  }
  updatePositions();
  window.requestAnimationFrame(runSimulationChunk);

  // ---- Pan / zoom ----
  let tx = 0, ty = 0, scale = 1;
  function applyTransform() {
    viewport.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + scale + ')');
  }
  // initial center
  const rect0 = svg.getBoundingClientRect();
  tx = rect0.width / 2 - W / 2;
  ty = rect0.height / 2 - H / 2;
  applyTransform();

  let isPanning = false, panStart = null;
  svg.addEventListener('mousedown', (ev) => {
    if (ev.target.closest('.node')) return;
    isPanning = true;
    panStart = { x: ev.clientX - tx, y: ev.clientY - ty };
    svg.classList.add('panning');
  });
  window.addEventListener('mousemove', (ev) => {
    if (!isPanning) return;
    tx = ev.clientX - panStart.x;
    ty = ev.clientY - panStart.y;
    applyTransform();
  });
  window.addEventListener('mouseup', () => { isPanning = false; svg.classList.remove('panning'); });

  svg.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const rect = svg.getBoundingClientRect();
    const cx = ev.clientX - rect.left;
    const cy = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
    const newScale = Math.max(0.1, Math.min(4, scale * factor));
    // zoom around cursor
    tx = cx - (cx - tx) * (newScale / scale);
    ty = cy - (cy - ty) * (newScale / scale);
    scale = newScale;
    applyTransform();
  }, { passive: false });

  svg.addEventListener('click', (ev) => {
    if (!ev.target.closest('.node')) {
      selectState(null);
    }
  });

  // ---- Node drag ----
  function attachDrag(n) {
    let dragging = false, offset = null;
    n._g.addEventListener('mousedown', (ev) => {
      ev.stopPropagation();
      dragging = true;
      const pt = svgPoint(ev.clientX, ev.clientY);
      offset = { x: pt.x - n.x, y: pt.y - n.y };
    });
    window.addEventListener('mousemove', (ev) => {
      if (!dragging) return;
      const pt = svgPoint(ev.clientX, ev.clientY);
      n.x = pt.x - offset.x;
      n.y = pt.y - offset.y;
      updatePositions();
    });
    window.addEventListener('mouseup', () => { dragging = false; });
  }
  function svgPoint(clientX, clientY) {
    const rect = svg.getBoundingClientRect();
    return { x: (clientX - rect.left - tx) / scale, y: (clientY - rect.top - ty) / scale };
  }

  // ---- Sidebar ----
  function selectState(id) {
    selectedId = id;
    nodeEls.forEach(n => {
      n._g.classList.toggle('selected', n.id === id);
    });
    renderSidebar();
  }

  const sidebar = document.getElementById('sidebar-content');
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function field(k, v) {
    const wrap = el('div', 'field');
    wrap.appendChild(el('div', 'k', k));
    const val = el('div', 'v');
    if (v === null || v === undefined || v === '') {
      val.classList.add('muted');
      val.textContent = '—';
    } else if (typeof v === 'object') {
      val.textContent = JSON.stringify(v);
    } else {
      val.textContent = String(v);
    }
    wrap.appendChild(val);
    return wrap;
  }
  function section(title) {
    const s = el('div', 'section');
    s.appendChild(el('h3', null, title));
    return s;
  }
  function listFrom(arr) {
    const ul = el('ul');
    if (!arr || arr.length === 0) {
      const li = el('li', 'muted', '(none)');
      ul.appendChild(li);
      return ul;
    }
    arr.forEach(x => ul.appendChild(el('li', null, typeof x === 'object' ? JSON.stringify(x) : x)));
    return ul;
  }

  function appendDetail(parent, key, value) {
    if (key === 'raw_screen_images' && Array.isArray(value)) {
      if (value.length === 0) return;
      const sec = section('raw screenshots');
      const wrap = el('div', 'screens');
      value.forEach(item => {
        const card = el('div', 'screen');
        card.appendChild(el('div', 'sid', item.screen_id || ''));
        const img = document.createElement('img');
        img.alt = item.screen_id || 'screenshot';
        img.src = item.data_uri || item.src || '';
        img.addEventListener('click', () => openLightbox(img.src, img.alt));
        card.appendChild(img);
        wrap.appendChild(card);
      });
      sec.appendChild(wrap);
      parent.appendChild(sec);
      return;
    }
    if (Array.isArray(value)) {
      const sec = section(key);
      sec.appendChild(listFrom(value));
      parent.appendChild(sec);
      return;
    }
    if (value && typeof value === 'object') {
      const sec = section(key);
      const entries = Object.entries(value);
      if (entries.length === 0) {
        sec.appendChild(el('div', 'muted', '(none)'));
      } else {
        entries.forEach(([childKey, childValue]) => appendDetail(sec, childKey, childValue));
      }
      parent.appendChild(sec);
      return;
    }
    parent.appendChild(field(key, value));
  }

  const lightbox = document.getElementById('lightbox');
  const lightboxImg = lightbox.querySelector('img');
  function openLightbox(src, alt) {
    lightboxImg.src = src;
    lightboxImg.alt = alt || '';
    lightbox.classList.add('open');
  }
  lightbox.addEventListener('click', () => {
    lightbox.classList.remove('open');
    lightboxImg.src = '';
  });

  function renderTransition(t, direction) {
    const wrap = el('div', 'tr');
    const head = el('div', 'row');
    const peer = direction === 'out' ? t.target : t.source;
    const arrow = el('span', 'arrow', direction === 'out' ? ' → ' : ' ← ');
    head.appendChild(document.createTextNode(direction === 'out' ? 'to' : 'from'));
    head.appendChild(arrow);
    head.appendChild(document.createTextNode(peer));
    wrap.appendChild(head);
    const at = (t.action && t.action.type) || '?';
    wrap.appendChild(el('div', 'row', 'action: ' + at));
    if (INCLUDE_SENSITIVE_DETAILS && t.action && Object.keys(t.action).length > 1) {
      wrap.appendChild(el('div', 'row', 'action details: ' + JSON.stringify(t.action)));
    }
    if (SHOW_GUARDS && t.guard) wrap.appendChild(el('div', 'row', 'guard: ' + t.guard));
    const transitionMeta = 'confidence: ' + (t.confidence ?? 0)
      + '  observed: ' + (t.observed_count ?? 0);
    wrap.appendChild(el('div', 'row', transitionMeta));
    return wrap;
  }

  function renderSidebar() {
    clear(sidebar);
    if (!selectedId) {
      sidebar.appendChild(el('div', 'empty', 'Click a state to view details.'));
      return;
    }
    const s = statesById[selectedId];
    if (!s) return;
    sidebar.appendChild(el('h2', null, s.name || s.state_id));
    Object.entries(s).forEach(([key, value]) => appendDetail(sidebar, key, value));

    const outSec = section('outgoing transitions');
    const outs = outgoing[selectedId] || [];
    if (outs.length === 0) outSec.appendChild(el('div', 'muted', '(none)'));
    outs.forEach(t => outSec.appendChild(renderTransition(t, 'out')));
    sidebar.appendChild(outSec);

    const inSec = section('incoming transitions');
    const ins = incoming[selectedId] || [];
    if (ins.length === 0) inSec.appendChild(el('div', 'muted', '(none)'));
    ins.forEach(t => inSec.appendChild(renderTransition(t, 'in')));
    sidebar.appendChild(inSec);
  }

  renderSidebar();
})();
</script>
</body>
</html>
"""


_COMPARE_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vigil FSM Comparison</title>
<style>
  :root {
    color-scheme: light;
    --ink: #1d252c;
    --muted: #63707c;
    --line: #d9e0e6;
    --paper: #f6f8fa;
    --panel: #ffffff;
    --gold: #7a4b00;
    --explored: #075985;
    --guard: #9a3412;
    --invariant: #166534;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    height: 100%;
    font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
    color: var(--ink);
    background: var(--paper);
  }
  #app { height: 100vh; display: grid; grid-template-rows: auto 1fr; min-height: 0; }
  header {
    min-height: 48px;
    padding: 10px 14px;
    display: flex;
    align-items: center;
    gap: 18px;
    border-bottom: 1px solid var(--line);
    background: #ffffff;
  }
  header h1 { margin: 0; font-size: 15px; font-weight: 700; }
  header .meta { color: var(--muted); font-size: 12px; }
  header b { color: var(--ink); }
  #workbench {
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 0;
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
    transition: grid-template-columns 160ms ease;
  }
  #workbench.inspector-open {
    grid-template-columns: minmax(0, 1fr) clamp(360px, 25vw, 460px);
  }
  .panel {
    min-width: 0;
    min-height: 0;
    display: grid;
    grid-template-rows: auto 1fr;
    background: var(--panel);
  }
  #gold-panel { grid-column: 1; grid-row: 1; }
  #explored-panel { grid-column: 1; grid-row: 2; border-top: 1px solid var(--line); }
  .panel-title {
    height: 42px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
    overflow: hidden;
  }
  .panel-title h2 { margin: 0; font-size: 13px; font-weight: 700; }
  .panel-title .counts {
    color: var(--muted);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  #gold-panel .panel-title h2 { color: var(--gold); }
  #explored-panel .panel-title h2 { color: var(--explored); }
  .graph-area { min-height: 0; position: relative; overflow: hidden; }
  #explored-body {
    min-height: 0;
    min-width: 0;
    display: grid;
  }
  svg.graph { width: 100%; height: 100%; display: block; background: #fbfcfd; cursor: grab; }
  svg.graph.panning { cursor: grabbing; }
  .hint {
    position: absolute;
    left: 10px;
    bottom: 10px;
    padding: 4px 7px;
    border: 1px solid var(--line);
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.9);
    color: var(--muted);
    font-size: 11px;
    pointer-events: none;
  }
  .node circle {
    stroke: #2f3b45;
    stroke-width: 1.2;
    cursor: grab;
  }
  .node.dragging circle { cursor: grabbing; }
  .node.initial circle { stroke-width: 3; }
  .node.selected circle { stroke: #f97316; stroke-width: 3.5; }
  .node text {
    font-size: 10px;
    text-anchor: middle;
    pointer-events: none;
    user-select: none;
    fill: #17202a;
  }
  .node .badge {
    font-size: 9px;
    fill: var(--invariant);
    font-weight: 700;
  }
  .edge path {
    fill: none;
    stroke-width: 1.35;
    cursor: pointer;
  }
  .edge .edge-hit {
    fill: none;
    stroke: transparent;
    stroke-width: 18;
    pointer-events: stroke;
  }
  .edge.guard path:not(.edge-hit) { stroke-width: 2.1; }
  .edge.post path:not(.edge-hit) { stroke-width: 2.1; }
  .edge.selected path:not(.edge-hit) { stroke: #f97316 !important; stroke-width: 3.2; }
  .edge text {
    font-size: 9px;
    pointer-events: none;
    user-select: none;
    fill: #3c4650;
  }
  #inspector {
    grid-column: 2;
    grid-row: 1 / 3;
    min-height: 0;
    min-width: 0;
    width: 100%;
    overflow-y: auto;
    border-left: 0;
    background: #ffffff;
    padding: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 120ms ease;
  }
  #workbench.inspector-open #inspector {
    border-left: 1px solid var(--line);
    padding: 12px;
    opacity: 1;
    pointer-events: auto;
  }
  #inspector h2 { margin: 0 0 8px; color: var(--explored); font-size: 14px; }
  #inspector h3 {
    margin: 14px 0 6px;
    padding-top: 10px;
    border-top: 1px solid #edf0f2;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #3a4752;
  }
  .empty { margin-top: 24px; color: var(--muted); text-align: center; font-style: italic; }
  .kv { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 8px; margin: 5px 0; }
  .kv .k {
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .kv .v {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    word-break: break-word;
  }
  pre {
    margin: 6px 0 0;
    padding: 8px;
    max-height: 260px;
    overflow: auto;
    background: #f7f9fb;
    border: 1px solid #e3e8ed;
    border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .pill-row { display: flex; flex-wrap: wrap; gap: 5px; margin: 5px 0; }
  .pill {
    border: 1px solid #cbd5df;
    border-radius: 999px;
    padding: 2px 7px;
    color: #344451;
    background: #f8fafc;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }
  .guard { color: var(--guard); }
  .invariant { color: var(--invariant); }
  .logic-list { display: flex; flex-direction: column; gap: 7px; }
  .logic-clause {
    display: grid;
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 8px;
    align-items: start;
    padding: 7px 8px;
    border: 1px solid #d7e1ea;
    border-radius: 6px;
    background: #fbfdff;
  }
  .logic-clause .idx {
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }
  .logic-clause .clause-text {
    color: #1f6f3b;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    word-break: break-word;
  }
  .logic-clause .clause-kind {
    margin-top: 2px;
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .screens { display: grid; grid-template-columns: 1fr; gap: 10px; }
  .screen {
    border: 1px solid #d9e0e6;
    border-radius: 6px;
    padding: 6px;
    background: #fbfcfd;
  }
  .screen .sid {
    margin-bottom: 4px;
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }
  .screen img {
    width: 100%;
    display: block;
    border-radius: 4px;
    background: #000;
    cursor: zoom-in;
  }
  #lightbox {
    position: fixed;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.86);
    z-index: 1000;
    cursor: zoom-out;
  }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 96vw; max-height: 96vh; box-shadow: 0 4px 28px rgba(0,0,0,0.45); }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1 id="title">Vigil FSM Comparison</h1>
    <div class="meta">gold <b id="gold-summary"></b></div>
    <div class="meta">explored <b id="explored-summary"></b></div>
  </header>
  <main id="workbench">
    <section id="gold-panel" class="panel">
      <div class="panel-title"><h2>Golden FSM</h2><div class="counts" id="gold-counts"></div></div>
      <div class="graph-area">
        <svg id="gold-graph" class="graph"></svg>
        <div class="hint">Click nodes or edges for spec details</div>
      </div>
    </section>
    <section id="explored-panel" class="panel">
      <div class="panel-title">
        <h2>Explored FSM</h2><div class="counts" id="explored-counts"></div>
      </div>
      <div id="explored-body">
        <div class="graph-area">
          <svg id="explored-graph" class="graph"></svg>
          <div class="hint">Click a state for screenshots/invariants; click an edge for guards</div>
        </div>
      </div>
    </section>
    <aside id="inspector"><div class="empty">Select a state or transition.</div></aside>
  </main>
</div>
<div id="lightbox"><img alt=""></div>

<script>
const COMPARE_DATA = __COMPARE_DATA__;

(function() {
  const MAX_LABEL = (COMPARE_DATA.options && COMPARE_DATA.options.max_label_len) || 24;
  const ACTION_COLORS = Object.assign({
    click: '#4a4a4a',
    input_text: '#e65100',
    navigate_back: '#b71c1c',
    navigate_home: '#b71c1c'
  }, COMPARE_DATA.explored.action_colors || {});

  document.getElementById('title').textContent =
    (COMPARE_DATA.title || 'Vigil') + ' FSM Comparison';
  setSummary('gold', COMPARE_DATA.golden);
  setSummary('explored', COMPARE_DATA.explored);

  const workbench = document.getElementById('workbench');
  const inspector = document.getElementById('inspector');
  const lightbox = document.getElementById('lightbox');
  const lightboxImg = lightbox.querySelector('img');
  lightbox.addEventListener('click', () => {
    lightbox.classList.remove('open');
    lightboxImg.src = '';
  });

  const graphs = [];
  graphs.push(createGraph('gold-graph', COMPARE_DATA.golden, 'golden'));
  graphs.push(createGraph('explored-graph', COMPARE_DATA.explored, 'explored'));
  renderEmpty();

  function setSummary(prefix, data) {
    const s = data.summary || {};
    const states = s.num_states || 0;
    const transitions = s.num_transitions || 0;
    const actionText = s.num_actions ? ', ' + s.num_actions + ' actions' : '';
    document.getElementById(prefix + '-summary').textContent =
      states + ' states / ' + transitions + ' transitions';
    document.getElementById(prefix + '-counts').textContent =
      states + ' states, ' + transitions + ' transitions' + actionText
      + (s.global_nav_edges
        ? ' (' + s.explicit_transitions + ' explicit + '
          + s.global_nav_edges + ' global-nav)'
        : '');
  }

  function createGraph(svgId, data, side) {
    const svg = document.getElementById(svgId);
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const W = 1200, H = 820;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

    const defs = document.createElementNS(SVG_NS, 'defs');
    svg.appendChild(defs);
    Object.values(ACTION_COLORS).concat(['#888888']).forEach(color => {
      if (defs.querySelector('#arrow-' + colorId(color))) return;
      const marker = document.createElementNS(SVG_NS, 'marker');
      marker.setAttribute('id', 'arrow-' + colorId(color));
      marker.setAttribute('viewBox', '0 0 10 10');
      marker.setAttribute('refX', '9');
      marker.setAttribute('refY', '5');
      marker.setAttribute('markerWidth', '7');
      marker.setAttribute('markerHeight', '7');
      marker.setAttribute('orient', 'auto-start-reverse');
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
      path.setAttribute('fill', color);
      marker.appendChild(path);
      defs.appendChild(marker);
    });

    const viewport = document.createElementNS(SVG_NS, 'g');
    const edgesG = document.createElementNS(SVG_NS, 'g');
    const nodesG = document.createElementNS(SVG_NS, 'g');
    viewport.appendChild(edgesG);
    viewport.appendChild(nodesG);
    svg.appendChild(viewport);

    const nodes = (data.states || []).map((state, i) => {
      const point = initialPosition(i, (data.states || []).length, side);
      return { id: state.state_id, data: state, x: point.x, y: point.y, vx: 0, vy: 0 };
    });
    const byId = {};
    nodes.forEach(node => { byId[node.id] = node; });

    const rawLinks = (data.transitions || [])
      .map((transition, index) => ({
        source: byId[transition.source],
        target: byId[transition.target],
        data: Object.assign({ index }, transition)
      }))
      .filter(link => link.source && link.target);
    const links = foldLinks(rawLinks);

    const edgeGroups = {};
    links.forEach(link => {
      const key = link.source.id + '->' + link.target.id;
      (edgeGroups[key] = edgeGroups[key] || []).push(link);
    });
    Object.values(edgeGroups).forEach(group => {
      group.forEach((link, i) => {
        link.parallelIndex = i;
        link.parallelCount = group.length;
        link.parallelOffset = (i - (group.length - 1) / 2) * 22;
      });
    });

    runSimulation(nodes, links, W, H);

    const edgeEls = links.map(link => renderEdge(link));
    const nodeEls = nodes.map(node => renderNode(node));
    updatePositions();

    let tx = 0, ty = 0, scale = 1;
    let activeNodeDrag = null;
    fitToGraph();
    applyTransform();
    attachPanZoom();

    return { side, svg, nodeEls, edgeEls, clearSelection };

    function foldLinks(raw) {
      const groups = {};
      raw.forEach(link => {
        const key = link.source.id + '->' + link.target.id + '::' + edgeFoldKey(link.data);
        (groups[key] = groups[key] || []).push(link);
      });
      return Object.values(groups).map(group => {
        if (group.length === 1) return group[0];
        const first = group[0];
        const foldedData = Object.assign({}, first.data, {
          folded_count: group.length,
          folded_transitions: group.map(link => link.data)
        });
        const observed = group.reduce((sum, link) => {
          const count = Number(link.data.observed_count || 0);
          return sum + (Number.isFinite(count) ? count : 0);
        }, 0);
        if (observed > 0) foldedData.folded_observed_count = observed;
        return { source: first.source, target: first.target, data: foldedData };
      });
    }

    function renderNode(node) {
      const g = document.createElementNS(SVG_NS, 'g');
      g.setAttribute('class', 'node' + (node.id === data.initial_state ? ' initial' : ''));
      const circle = document.createElementNS(SVG_NS, 'circle');
      const radius = nodeRadius(node.data);
      circle.setAttribute('r', radius);
      circle.setAttribute('fill', nodeFill(node.data, data, side));
      const label = document.createElementNS(SVG_NS, 'text');
      label.setAttribute('dy', '0.35em');
      label.textContent = truncate(node.data.name || node.id, MAX_LABEL);
      g.appendChild(circle);
      g.appendChild(label);
      const invCount = invariantCount(node.data);
      if (invCount > 0) {
        const badge = document.createElementNS(SVG_NS, 'text');
        badge.setAttribute('class', 'badge');
        badge.setAttribute('dy', radius + 12);
        badge.textContent = 'I:' + invCount;
        g.appendChild(badge);
      }
      g.addEventListener('mousedown', ev => startNodeDrag(ev, node, g));
      g.addEventListener('click', ev => {
        ev.stopPropagation();
        if (node._dragMoved) {
          node._dragMoved = false;
          return;
        }
        selectItem(side, 'state', node.data, g);
      });
      nodesG.appendChild(g);
      node._g = g;
      return node;
    }

    function renderEdge(link) {
      const g = document.createElementNS(SVG_NS, 'g');
      const classes = ['edge'];
      if (link.data.guard) classes.push('guard');
      if (hasPostcondition(link.data)) classes.push('post');
      g.setAttribute('class', classes.join(' '));
      const hit = document.createElementNS(SVG_NS, 'path');
      hit.setAttribute('class', 'edge-hit');
      const path = document.createElementNS(SVG_NS, 'path');
      const actionType = actionTypeOf(link.data);
      const color = ACTION_COLORS[actionType] || '#888888';
      path.setAttribute('stroke', color);
      path.setAttribute('marker-end', 'url(#arrow-' + colorId(color) + ')');
      if (actionType === 'navigate_back' || actionType === 'navigate_home') {
        path.setAttribute('stroke-dasharray', '4,3');
      }
      const label = document.createElementNS(SVG_NS, 'text');
      label.textContent = edgeLabel(link.data);
      g.appendChild(hit);
      g.appendChild(path);
      g.appendChild(label);
      g.addEventListener('click', ev => {
        ev.stopPropagation();
        selectItem(side, 'transition', link.data, g);
      });
      edgesG.appendChild(g);
      link._g = g;
      link._hit = hit;
      link._path = path;
      link._label = label;
      return link;
    }

    function updatePositions() {
      nodeEls.forEach(node => {
        node._g.setAttribute('transform', 'translate(' + node.x + ',' + node.y + ')');
      });
      edgeEls.forEach(link => {
        if (link.source === link.target) {
          const r = nodeRadius(link.source.data);
          const lift = r + 34 + (link.parallelIndex || 0) * 14;
          const sx = link.source.x + r * 0.62;
          const sy = link.source.y - r * 0.62;
          const tx2 = link.source.x - r * 0.62;
          const ty2 = link.source.y - r * 0.62;
          const d = 'M ' + sx + ' ' + sy + ' C '
            + (link.source.x + lift) + ' ' + (link.source.y - lift) + ' '
            + (link.source.x - lift) + ' ' + (link.source.y - lift) + ' '
            + tx2 + ' ' + ty2;
          link._path.setAttribute('d', d);
          link._hit.setAttribute('d', d);
          link._label.setAttribute('x', link.source.x);
          link._label.setAttribute('y', link.source.y - lift - 4);
          return;
        }
        const dx = link.target.x - link.source.x;
        const dy = link.target.y - link.source.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const sr = nodeRadius(link.source.data);
        const tr = nodeRadius(link.target.data) + 8;
        const sx = link.source.x + (dx / dist) * sr;
        const sy = link.source.y + (dy / dist) * sr;
        const tx2 = link.target.x - (dx / dist) * tr;
        const ty2 = link.target.y - (dy / dist) * tr;
        const offset = link.parallelOffset || 0;
        if (offset === 0) {
          const d = 'M ' + sx + ' ' + sy + ' L ' + tx2 + ' ' + ty2;
          link._path.setAttribute('d', d);
          link._hit.setAttribute('d', d);
          link._label.setAttribute('x', (sx + tx2) / 2);
          link._label.setAttribute('y', (sy + ty2) / 2 - 3);
          return;
        }
        const nx = -dy / dist;
        const ny = dx / dist;
        const mx = (sx + tx2) / 2 + nx * offset;
        const my = (sy + ty2) / 2 + ny * offset;
        const d = 'M ' + sx + ' ' + sy + ' Q ' + mx + ' ' + my + ' ' + tx2 + ' ' + ty2;
        link._path.setAttribute('d', d);
        link._hit.setAttribute('d', d);
        link._label.setAttribute('x', mx);
        link._label.setAttribute('y', my - 3);
      });
    }

    function startNodeDrag(ev, node, element) {
      ev.preventDefault();
      ev.stopPropagation();
      const point = graphPoint(ev);
      activeNodeDrag = {
        node,
        element,
        dx: point.x - node.x,
        dy: point.y - node.y,
        moved: false
      };
      node._dragMoved = false;
      element.classList.add('dragging');
    }

    function attachPanZoom() {
      let panning = false;
      let start = null;
      svg.addEventListener('mousedown', ev => {
        if (ev.target.closest('.node') || ev.target.closest('.edge')) return;
        panning = true;
        start = { x: ev.clientX - tx, y: ev.clientY - ty };
        svg.classList.add('panning');
      });
      window.addEventListener('mousemove', ev => {
        if (activeNodeDrag) {
          const point = graphPoint(ev);
          const node = activeNodeDrag.node;
          const nextX = point.x - activeNodeDrag.dx;
          const nextY = point.y - activeNodeDrag.dy;
          if (Math.abs(nextX - node.x) + Math.abs(nextY - node.y) > 2) {
            activeNodeDrag.moved = true;
          }
          node.x = nextX;
          node.y = nextY;
          node.vx = 0;
          node.vy = 0;
          updatePositions();
          return;
        }
        if (!panning) return;
        tx = ev.clientX - start.x;
        ty = ev.clientY - start.y;
        applyTransform();
      });
      window.addEventListener('mouseup', () => {
        if (activeNodeDrag) {
          activeNodeDrag.node._dragMoved = activeNodeDrag.moved;
          activeNodeDrag.element.classList.remove('dragging');
          activeNodeDrag = null;
        }
        panning = false;
        svg.classList.remove('panning');
      });
      svg.addEventListener('wheel', ev => {
        ev.preventDefault();
        const rect = svg.getBoundingClientRect();
        const cx = ev.clientX - rect.left;
        const cy = ev.clientY - rect.top;
        const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
        const next = Math.max(0.18, Math.min(4, scale * factor));
        tx = cx - (cx - tx) * (next / scale);
        ty = cy - (cy - ty) * (next / scale);
        scale = next;
        applyTransform();
      }, { passive: false });
      svg.addEventListener('click', () => {
        clearSelection();
        renderEmpty();
      });
    }

    function applyTransform() {
      viewport.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + scale + ')');
    }

    function svgPoint(ev) {
      const rect = svg.getBoundingClientRect();
      return {
        x: ((ev.clientX - rect.left) / Math.max(1, rect.width)) * W,
        y: ((ev.clientY - rect.top) / Math.max(1, rect.height)) * H
      };
    }

    function graphPoint(ev) {
      const point = svgPoint(ev);
      return {
        x: (point.x - tx) / scale,
        y: (point.y - ty) / scale
      };
    }

    function fitToGraph() {
      if (!nodeEls.length) return;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      nodeEls.forEach(node => {
        const r = nodeRadius(node.data) + 44;
        minX = Math.min(minX, node.x - r);
        minY = Math.min(minY, node.y - r);
        maxX = Math.max(maxX, node.x + r);
        maxY = Math.max(maxY, node.y + r);
      });
      const graphW = Math.max(1, maxX - minX);
      const graphH = Math.max(1, maxY - minY);
      const targetScale = Math.min((W * 0.86) / graphW, (H * 0.82) / graphH, 2.35);
      scale = Math.max(0.72, targetScale);
      const centerX = (minX + maxX) / 2;
      const centerY = (minY + maxY) / 2;
      tx = W / 2 - centerX * scale;
      ty = H / 2 - centerY * scale;
    }

    function clearSelection() {
      nodeEls.forEach(node => node._g.classList.remove('selected'));
      edgeEls.forEach(link => link._g.classList.remove('selected'));
    }
  }

  function selectItem(side, type, item, element) {
    graphs.forEach(graph => graph.clearSelection());
    if (element) element.classList.add('selected');
    workbench.classList.add('inspector-open');
    if (type === 'state') renderState(side, item);
    else renderTransition(side, item);
  }

  function renderEmpty() {
    workbench.classList.remove('inspector-open');
    clear(inspector);
    inspector.appendChild(el('div', 'empty', 'Select a state or transition.'));
  }

  function renderState(side, state) {
    clear(inspector);
    inspector.appendChild(el('h2', null, sideLabel(side) + ' State'));
    appendKV('state', state.state_id);
    appendKV('name', state.name || state.state_id);
    if (state.screen_marker) appendKV('screen', state.screen_marker);
    if (state.kind) appendKV('kind', state.kind);
    if (state.dialog) appendKV('dialog', String(state.dialog));
    if (state.anchor) appendKV('anchor', state.anchor);
    if (state.evidence) {
      appendKV('observations', state.evidence.observation_count);
      appendKV('raw screens', (state.evidence.raw_screen_ids || []).length);
    }
    const invariants = state.invariant_specs || [];
    if (invariants.length) {
      inspector.appendChild(section('invariant logic clauses'));
      invariants.forEach((inv, index) => {
        appendLogic(inv.logic, inv.expr, 'I' + (index + 1));
      });
    }
    const images = state.raw_screen_images || [];
    if (images.length) {
      inspector.appendChild(section('screenshots (' + images.length + ')'));
      const wrap = el('div', 'screens');
      images.forEach(image => {
        const card = el('div', 'screen');
        card.appendChild(el('div', 'sid', image.screen_id || ''));
        const img = document.createElement('img');
        img.alt = image.screen_id || 'screenshot';
        img.src = image.src || image.data_uri || '';
        img.addEventListener('click', () => openLightbox(img.src, img.alt));
        card.appendChild(img);
        wrap.appendChild(card);
      });
      inspector.appendChild(wrap);
    }
  }

  function renderTransition(side, transition) {
    clear(inspector);
    inspector.appendChild(el('h2', null, sideLabel(side) + ' Transition'));
    appendKV('source', transition.source);
    appendKV('target', transition.target);
    appendKV('action', actionNameOf(transition));
    appendKV('type', actionTypeOf(transition));
    if (transition.kind) appendKV('kind', transition.kind);
    if (transition.confidence !== undefined) appendKV('confidence', transition.confidence);
    if (transition.observed_count !== undefined) appendKV('observed', transition.observed_count);
    if (transition.folded_count > 1) {
      appendKV('folded transitions', transition.folded_count);
      if (transition.folded_observed_count !== undefined) {
        appendKV('folded observed', transition.folded_observed_count);
      }
      appendFoldedTransitions(transition.folded_transitions || []);
    }
    if (transition.guard_admission_status) appendKV('admission', transition.guard_admission_status);
    if (transition.guard || transition.guard_logic) {
      inspector.appendChild(section('precondition Gamma DSL/Lark parsed clauses'));
      appendLogic(transition.guard_logic, transition.guard, 'G');
    }
    const post = transition.postcondition_contract || null;
    if (post || transition.postcondition || transition.postcondition_logic) {
      inspector.appendChild(section('postcondition Psi DSL/Lark parsed clauses'));
      if (post) {
        appendKV('post kind', post.kind);
        appendKV('post required', post.required);
        appendKV('post risk', post.risk_level);
        appendKV(
          'post incomplete',
          transition.postcondition_incomplete || post.intent_effect_incomplete
        );
        appendKV('intent effect', post.intent_effect_required);
      }
      if (transition.postcondition_admission_status) {
        appendKV('post admission', transition.postcondition_admission_status);
      }
      if (transition.postcondition_admission_reason) {
        appendKV('post reason', transition.postcondition_admission_reason);
      }
      if (transition.postcondition || transition.postcondition_logic) {
        appendLogic(transition.postcondition_logic, transition.postcondition, 'P');
      } else {
        const pre = el('pre');
        pre.textContent = '(no admitted executable Psi DSL)';
        inspector.appendChild(pre);
      }
      appendEffects(transition.postcondition_effects || []);
    }
    const action = transition.action || {};
    appendKV('resource', action.target_resource_id || action.resource_id || action.target);
    appendKV(
      'provenance',
      Array.isArray(transition.provenance) ? transition.provenance.length : ''
    );
    if (transition.spec) appendJSON('gold spec', transition.spec);
  }

  function appendFoldedTransitions(transitions) {
    if (!Array.isArray(transitions) || transitions.length <= 1) return;
    inspector.appendChild(section('folded identical actions'));
    const pre = el('pre');
    pre.textContent = transitions.map((transition, index) => {
      const observed = transition.observed_count !== undefined
        ? ' observed=' + transition.observed_count
        : '';
      const confidence = transition.confidence !== undefined
        ? ' confidence=' + transition.confidence
        : '';
      return '#'
        + (index + 1)
        + ' '
        + transition.source
        + ' -> '
        + transition.target
        + ' '
        + actionNameOf(transition)
        + observed
        + confidence;
    }).join('\\n');
    inspector.appendChild(pre);
  }

  function runSimulation(nodes, links, W, H) {
    const layoutLinks = layoutLinksFor(links, nodes.length);
    const iterations = Math.max(110, Math.min(320, 5200 / Math.max(1, nodes.length)));
    const idealDistance = nodes.length > 10 ? 230 : 255;
    for (let iter = 0; iter < iterations; iter++) {
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy + 0.01;
          let d = Math.sqrt(d2);
          let force = 42000 / d2;
          a.vx += (dx / d) * force;
          a.vy += (dy / d) * force;
          b.vx -= (dx / d) * force;
          b.vy -= (dy / d) * force;
          const minDistance = nodeRadius(a.data) + nodeRadius(b.data) + 86;
          if (d < minDistance) {
            const push = (minDistance - d) * 0.055;
            a.vx += (dx / d) * push;
            a.vy += (dy / d) * push;
            b.vx -= (dx / d) * push;
            b.vy -= (dy / d) * push;
          }
        }
      }
      layoutLinks.forEach(link => {
        const dx = link.target.x - link.source.x;
        const dy = link.target.y - link.source.y;
        const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const force = (d - idealDistance) * 0.012;
        link.source.vx += (dx / d) * force;
        link.source.vy += (dy / d) * force;
        link.target.vx -= (dx / d) * force;
        link.target.vy -= (dy / d) * force;
      });
      nodes.forEach(node => {
        node.vx += (W / 2 - node.x) * 0.0009;
        node.vy += (H / 2 - node.y) * 0.0009;
        node.vx *= 0.82;
        node.vy *= 0.82;
        node.x += node.vx;
        node.y += node.vy;
      });
    }
  }

  function layoutLinksFor(links, nodeCount) {
    const unique = [];
    const seen = new Set();
    links.forEach(link => {
      if (link.source === link.target) return;
      const key = link.source.id < link.target.id
        ? link.source.id + '::' + link.target.id
        : link.target.id + '::' + link.source.id;
      if (seen.has(key)) return;
      seen.add(key);
      unique.push(link);
    });
    if (unique.length > Math.max(1, nodeCount) * 2.2) {
      return [];
    }
    return unique;
  }

  function initialPosition(index, total, side) {
    const angle = (index / Math.max(1, total)) * Math.PI * 2;
    const radius = Math.max(300, Math.min(440, 210 + total * 18));
    const offset = side === 'golden' ? -16 : 16;
    return {
      x: 600 + Math.cos(angle) * radius + offset,
      y: 410 + Math.sin(angle) * radius
    };
  }

  function nodeFill(state, data, side) {
    if (state.state_id === data.initial_state) return data.initial_color || '#1565c0';
    if (side === 'golden') return state.dialog ? '#ffe8cc' : '#fff3bf';
    const activity = (state.android_context && state.android_context.activity_name) || '';
    return (data.activity_colors && data.activity_colors[activity]) || '#e0f2fe';
  }

  function nodeRadius(state) {
    return 24 + Math.min(12, String(state.name || state.state_id || '').length * 0.25);
  }

  function invariantCount(state) {
    return (state.invariant_specs || []).length;
  }

  function actionTypeOf(transition) {
    return (transition.action && transition.action.type) || '?';
  }

  function actionNameOf(transition) {
    const action = transition.action || {};
    return action.name
      || action.resource_id
      || action.target_resource_id
      || action.target
      || action.type
      || '?';
  }

  function edgeLabel(transition) {
    let label = edgeBaseLabel(transition);
    const folded = Number(transition.folded_count || 0);
    const suffix = folded > 1 ? ' x' + folded : '';
    return truncate(label, 34 - suffix.length) + suffix;
  }

  function edgeBaseLabel(transition) {
    let label = actionNameOf(transition);
    if (label === 'navigate_back' || label === 'system.back') label = 'back';
    if (transition.guard) label += ' [G]';
    if (hasPostcondition(transition)) label += ' [P]';
    return label;
  }

  function edgeFoldKey(transition) {
    const action = transition.action || {};
    return JSON.stringify([
      actionTypeOf(transition),
      actionNameOf(transition),
      action.text || '',
      action.value || '',
      action.input_text || '',
      action.target_text || '',
      transition.guard || '',
      transition.postcondition || '',
      hasPostcondition(transition) ? 'P' : ''
    ]);
  }

  function hasPostcondition(transition) {
    return !!(transition && (transition.postcondition || transition.postcondition_logic));
  }

  function colorId(color) { return String(color).replace('#', ''); }
  function truncate(value, max) {
    const text = String(value || '');
    return text.length > max ? text.slice(0, max - 1) + '...' : text;
  }
  function sideLabel(side) { return side === 'golden' ? 'Golden' : 'Explored'; }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function section(title) {
    const h = el('h3', null, title);
    return h;
  }
  function appendKV(key, value) {
    const row = el('div', 'kv');
    row.appendChild(el('div', 'k', key));
    const shown = value === undefined || value === null || value === '' ? '-' : value;
    row.appendChild(el('div', 'v', shown));
    inspector.appendChild(row);
  }
  function appendJSON(title, value) {
    if (value === undefined || value === null) return;
    inspector.appendChild(section(title));
    const pre = el('pre');
    pre.textContent = JSON.stringify(value, null, 2);
    inspector.appendChild(pre);
  }
  function appendLogic(logic, fallbackExpr, labelPrefix) {
    if (!logic || (logic.status !== 'parsed' && logic.status !== 'typed')) {
      const pre = el('pre', 'guard');
      pre.textContent = fallbackExpr || (logic && logic.expression) || '';
      inspector.appendChild(pre);
      if (logic && logic.error) appendKV('parse error', logic.error);
      return;
    }
    appendKV('parser', logic.status === 'parsed' ? 'DSL/Lark parsed' : 'typed contract clauses');
    const root = logic.root || {};
    if (root.operator) appendKV('root op', String(root.operator).toUpperCase());
    const clauses = logic.clauses || [];
    if (!clauses.length) {
      const pre = el('pre');
      pre.textContent = '(no symbolic clauses)';
      inspector.appendChild(pre);
      return;
    }
    const wrap = el('div', 'logic-list');
    clauses.forEach((clause, index) => {
      const row = el('div', 'logic-clause');
      row.appendChild(el('div', 'idx', (labelPrefix || 'C') + (index + 1)));
      const body = el('div');
      body.appendChild(el('div', 'clause-text', clause.text || ''));
      const kind = [clause.predicate_type || 'predicate', clause.source || '']
        .filter(Boolean)
        .join(' / ');
      body.appendChild(el('div', 'clause-kind', kind));
      row.appendChild(body);
      wrap.appendChild(row);
    });
    inspector.appendChild(wrap);
  }
  function appendEffects(effects) {
    if (!effects || !effects.length) return;
    inspector.appendChild(section('audit-only unsupported effect requirements'));
    const wrap = el('div', 'logic-list');
    effects.forEach((effect, index) => {
      const row = el('div', 'logic-clause');
      row.appendChild(el('div', 'idx', 'E' + (index + 1)));
      const body = el('div');
      body.appendChild(el('div', 'clause-text', effect.text || ''));
      const kind = [effect.effect_kind || 'effect', effect.source || '']
        .filter(Boolean)
        .join(' / ');
      body.appendChild(el('div', 'clause-kind', kind));
      row.appendChild(body);
      wrap.appendChild(row);
    });
    inspector.appendChild(wrap);
  }
  function openLightbox(src, alt) {
    lightboxImg.src = src;
    lightboxImg.alt = alt || '';
    lightbox.classList.add('open');
  }
})();
</script>
</body>
</html>
"""


def main() -> None:
    """Run the FSM visualization pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-visualize",
        description="Visualize an FSM as a graph image.",
    )
    parser.add_argument("--fsm", required=True, help="Path to serialized FSM JSON file")
    parser.add_argument(
        "--gold-fsm",
        default=None,
        help=(
            "Optional hand-authored gold FSM JSON. When provided with --format html, "
            "renders a split-screen gold-vs-explored comparison viewer."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output path. Defaults to output_docs/<app>_fsm.<format>, or "
            "output_docs/<app>/fsm.html for --format html."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["png", "svg", "pdf", "html"],
        default="png",
        help="Output format. 'html' produces a self-contained interactive viewer. (default: png)",
    )
    parser.add_argument(
        "--layout",
        choices=["dot", "neato", "fdp", "sfdp"],
        default="dot",
        help="Graphviz layout engine (default: dot)",
    )
    parser.add_argument(
        "--show-guards", action="store_true", help="Show guard expressions on edges"
    )
    parser.add_argument("--show-counts", action="store_true", help="Show observed_count on edges")
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't cluster states by Activity",
    )
    parser.add_argument(
        "--max-label-len",
        type=int,
        default=20,
        help="Max characters for labels (default: 20)",
    )
    parser.add_argument(
        "--include-sensitive-details",
        action="store_true",
        help="Include full state and transition details in HTML output",
    )
    parser.add_argument(
        "--screens-dir",
        type=str,
        default=None,
        help=(
            "Directory containing raw screenshot files named '<screen_id>.png' "
            "(or .jpg/.jpeg/.webp). In normal HTML mode this requires "
            "--include-sensitive-details and screenshots are embedded as base64. "
            "In --gold-fsm comparison mode screenshots are linked from the explored "
            "state sidebar."
        ),
    )

    args = parser.parse_args()

    fsm_path = Path(args.fsm)
    if not fsm_path.exists():
        logger.error(f"FSM file not found: {fsm_path}")
        raise SystemExit(1)

    gold_fsm_path: Path | None = None
    if args.gold_fsm:
        gold_fsm_path = Path(args.gold_fsm)
        if not gold_fsm_path.exists():
            logger.error(f"Gold FSM file not found: {gold_fsm_path}")
            raise SystemExit(1)
        if args.format != "html":
            logger.error("--gold-fsm is only supported with --format html")
            raise SystemExit(1)

    output_path = resolve_generated_output_path(
        args.output, default_output_path(fsm_path, args.format)
    )

    if args.format == "html":
        screens_dir: Path | None = None
        if args.screens_dir:
            screens_dir = Path(args.screens_dir)
            if not screens_dir.exists():
                logger.error(f"Screens directory not found: {screens_dir}")
                raise SystemExit(1)
        if gold_fsm_path is not None:
            render_fsm_compare_html(
                gold_fsm_path=gold_fsm_path,
                explored_fsm_path=fsm_path,
                output_path=output_path,
                screens_dir=screens_dir,
                max_label_len=args.max_label_len,
            )
            return
        render_fsm_html(
            fsm_path=fsm_path,
            output_path=output_path,
            show_guards=args.show_guards,
            show_counts=args.show_counts,
            max_label_len=args.max_label_len,
            include_sensitive_details=args.include_sensitive_details,
            screens_dir=screens_dir,
        )
        return

    render_fsm(
        fsm_path=fsm_path,
        output_path=output_path,
        fmt=args.format,
        layout=args.layout,
        show_guards=args.show_guards,
        show_counts=args.show_counts,
        cluster_activities=not args.no_cluster,
        max_label_len=args.max_label_len,
    )


if __name__ == "__main__":
    main()
