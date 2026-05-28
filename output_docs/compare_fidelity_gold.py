from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APPS = {
    "market": {
        "trace": "data/apps/com_vigil_market_fidelity/traces/exploration_20260525_034917.json",
        "generated": "models/bundles/com_vigil_market_fidelity/fsm.json",
        "gold": "fidelity_app/vigilmarket/gold/fsm.json",
    },
    "bank": {
        "trace": "data/apps/com_vigil_bank_fidelity/traces/exploration_20260527_134615.json",
        "generated": "models/bundles/com_vigil_bank_fidelity/fsm.json",
        "gold": "fidelity_app/vigilbank/gold/fsm.json",
    },
    "chat": {
        "trace": "data/apps/com_vigil_chat_fidelity/traces/exploration_20260527_180737.json",
        "generated": "models/bundles/com_vigil_chat_fidelity/fsm.json",
        "gold": "fidelity_app/vigilchat/gold/fsm.json",
    },
    "clock": {
        "trace": "data/apps/com_vigil_clock_fidelity/traces/exploration_20260527_205812.json",
        "generated": "models/bundles/com_vigil_clock_fidelity/fsm.json",
        "gold": "fidelity_app/vigilclock/gold/fsm.json",
    },
}


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def action_patterns(actions: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for name in actions:
        parts = re.split(r"(<[^>]+>)", name)
        regex = (
            "^"
            + "".join(r"[^.]+" if part.startswith("<") else re.escape(part) for part in parts)
            + "$"
        )
        patterns.append((name, re.compile(regex)))
    return patterns


def normalize_action(action: str | None, patterns: list[tuple[str, re.Pattern[str]]]) -> str | None:
    if not action:
        return None
    for name, pattern in patterns:
        if pattern.match(action):
            return name
    return action


def screen_marker(trace: dict, screen_id: str | None) -> str | None:
    if not screen_id:
        return None
    screen = trace.get("screens", {}).get(screen_id, {})
    for element in screen.get("elements", []):
        if element.get("resource_id") == "screen_marker":
            value = element.get("text") or element.get("content_description")
            if isinstance(value, str) and value.startswith("screen:"):
                return value.split(":", 1)[1]
    return None


def state_marker(trace: dict, state: dict) -> str | None:
    raw_screen_ids = state.get("evidence", {}).get("raw_screen_ids")
    if raw_screen_ids is None:
        raw_screen_ids = state.get("raw_screens", [])
    markers = Counter(
        marker
        for marker in (screen_marker(trace, screen_id) for screen_id in raw_screen_ids)
        if marker
    )
    return markers.most_common(1)[0][0] if markers else None


def generated_semantics(
    trace: dict, generated: dict, patterns: list[tuple[str, re.Pattern[str]]]
) -> tuple[set[str], set[str], set[tuple[str, str, str]], dict[str, str]]:
    states = generated.get("states", {})
    if isinstance(states, list):
        states = {state.get("state_id") or state.get("id"): state for state in states}

    state_to_marker = {state_id: state_marker(trace, state) for state_id, state in states.items()}
    markers = {marker for marker in state_to_marker.values() if marker}
    actions: set[str] = set()
    edges: set[tuple[str, str, str]] = set()

    for transition in generated.get("transitions", []):
        action_payload = transition.get("action") or {}
        raw_action = (
            action_payload.get("resource_id")
            or action_payload.get("target_resource_id")
            or action_payload.get("target")
        )
        action = normalize_action(raw_action, patterns)
        if action:
            actions.add(action)

        provenance = transition.get("provenance") or []
        if provenance:
            for item in provenance:
                src = screen_marker(trace, item.get("source_screen_id"))
                tgt = screen_marker(trace, item.get("target_screen_id"))
                if src and tgt and action:
                    edges.add((src, action, tgt))
        else:
            src = state_to_marker.get(transition.get("source") or transition.get("from"))
            tgt = state_to_marker.get(transition.get("target") or transition.get("to"))
            if src and tgt and action:
                edges.add((src, action, tgt))

    return markers, actions, edges, state_to_marker


def gold_global_edges(gold: dict) -> set[tuple[str, str, str]]:
    nav = gold.get("global_navigation") or {}
    visible_on = nav.get("visible_on") or []
    out: set[tuple[str, str, str]] = set()
    for nav_action in nav.get("actions") or []:
        action = nav_action["action"]
        target = nav_action["to"]
        guard = nav_action.get("guard", "")
        for source in visible_on:
            if "current_screen ==" in guard:
                expected = guard.split("current_screen ==", 1)[1].strip()
                if source != expected:
                    continue
            if "current_screen not in" in guard:
                blocked = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", guard.split("{", 1)[-1]))
                if source in blocked:
                    continue
            out.add((source, action, target))
    return out


def _residual_split_reasons(
    trace: dict,
    generated: dict,
    state_to_marker: dict[str, str | None],
) -> dict[str, list[dict]]:
    """For every gold marker that has >1 generated states, classify each
    residual generated state's "why didn't it merge?" reason.

    Reasons:

    * ``label_field_difference``: the state's recomputed
      ``compute_quotient_label`` hash differs from at least one peer in
      the cluster. The first divergent field is named.
    * ``transition_refinement_conflict``: all label hashes match but
      states stayed separate (the post-quotient determinism guard
      isolated them because their high-trust ``(quotient_action_key ->
      target_block)`` maps disagreed).

    Returns a dict ``{marker -> [ {state_id, reason, detail} ]}``. No
    app-specific tokens.
    """
    try:
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )
    except Exception:
        return {}

    states = generated.get("states", {})
    if isinstance(states, list):
        states = {state.get("state_id") or state.get("id"): state for state in states}

    screens = trace.get("screens", {})

    marker_to_states: dict[str, list[str]] = {}
    for sid, marker in state_to_marker.items():
        if not marker:
            continue
        marker_to_states.setdefault(marker, []).append(sid)

    out: dict[str, list[dict]] = {}
    for marker, sids in marker_to_states.items():
        if len(sids) <= 1:
            continue
        per_state_hash: dict[str, str | None] = {}
        per_state_label: dict[str, dict | None] = {}
        for sid in sids:
            state = states.get(sid) or {}
            raw_ids = state.get("evidence", {}).get("raw_screen_ids") or state.get(
                "raw_screens", []
            )
            label = None
            for rsid in raw_ids:
                screen = screens.get(rsid)
                if isinstance(screen, dict) and (
                    screen.get("elements") or screen.get("interactable_elements")
                ):
                    label = compute_quotient_label(screen)
                    break
            per_state_label[sid] = label
            per_state_hash[sid] = signature_hash(label) if label is not None else None

        all_hashes = {h for h in per_state_hash.values() if h is not None}
        cluster: list[dict] = []
        if len(all_hashes) > 1:
            # Label difference: name the first diverging field per pair.
            reference_sid = sids[0]
            reference_label = per_state_label[reference_sid] or {}
            for sid in sids:
                label = per_state_label.get(sid) or {}
                if per_state_hash[sid] == per_state_hash[reference_sid]:
                    cluster.append({"state_id": sid, "reason": "label_match_with_reference"})
                    continue
                first_div = None
                for field, ref_val in sorted(reference_label.items()):
                    if label.get(field) != ref_val:
                        first_div = field
                        break
                cluster.append(
                    {
                        "state_id": sid,
                        "reason": "label_field_difference",
                        "field": first_div or "?",
                    }
                )
        else:
            # All labels identical => surviving splits are determinism
            # conflicts. Look up matching builder_diagnostic entries.
            diagnostics = [
                entry
                for entry in (generated.get("evolution_log") or [])
                if entry.get("action") == "builder_diagnostic"
                and entry.get("kind") == "quotient_residual_conflict"
            ]
            isolated = {
                sid
                for entry in diagnostics
                for grp in (entry.get("split_groups") or {}).values()
                for sid in grp
            }
            for sid in sids:
                cluster.append(
                    {
                        "state_id": sid,
                        "reason": (
                            "transition_refinement_conflict"
                            if sid in isolated
                            else "transition_refinement_conflict_or_other"
                        ),
                    }
                )
        out[marker] = cluster
    return out


def summarize(slug: str, paths: dict[str, str]) -> None:
    trace = load(paths["trace"])
    generated = load(paths["generated"])
    gold = load(paths["gold"])

    gold_states = {state["id"] for state in gold.get("states", [])}
    gold_actions = [action["name"] for action in gold.get("actions", [])]
    patterns = action_patterns(gold_actions)
    markers, actions, edges, state_to_marker = generated_semantics(trace, generated, patterns)

    explicit_edges = {
        (transition["from"], transition["action"], transition["to"])
        for transition in gold.get("transitions", [])
    }
    global_edges = gold_global_edges(gold)

    generated_states = generated.get("states", {})
    generated_state_count = len(generated_states)
    generated_transition_count = len(generated.get("transitions", []))

    split_counts = Counter(marker for marker in state_to_marker.values() if marker)

    print(f"APP {slug}")
    print(
        "trace",
        f"steps={trace.get('total_steps')}",
        f"screens={trace.get('unique_screens')}",
        f"duration={trace.get('duration_seconds'):.1f}s",
    )
    print(
        "fsm",
        f"generated_states={generated_state_count}",
        f"generated_transitions={generated_transition_count}",
        f"gold_states={len(gold_states)}",
        f"gold_actions={len(gold_actions)}",
        f"gold_explicit_transitions={len(explicit_edges)}",
        f"gold_global_transitions={len(global_edges)}",
    )
    print("markers", sorted(markers))
    print("missing_states", sorted(gold_states - markers))
    print("extra_markers", sorted(markers - gold_states))
    print("state_splits", sorted(split_counts.items()))
    print("missing_actions", sorted(set(gold_actions) - actions))
    print("extra_actions", sorted(actions - set(gold_actions))[:80])
    print("missing_explicit_edges", sorted(explicit_edges - edges))
    print("missing_global_edges", sorted(global_edges - edges)[:80])
    print("extra_edges_sample", sorted(edges - explicit_edges - global_edges)[:80])
    residual = _residual_split_reasons(trace, generated, state_to_marker)
    if residual:
        print("residual_split_reasons:")
        for marker in sorted(residual):
            print(f"  {marker}:")
            for entry in residual[marker]:
                print(f"    {entry}")
    print()


def main() -> None:
    for slug, paths in APPS.items():
        summarize(slug, paths)


if __name__ == "__main__":
    main()
