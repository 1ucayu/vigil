"""``vigil-validate-trace``: quality summary for native exploration traces.

Reads a trace JSON produced by :mod:`vigil.neuro.explorer` and computes
diagnostics that flag issues likely to compromise downstream FSM building,
DSL guard generation, or runtime symbolic verification:

  - sentinel rates (COLD_START_FAILED, ACTION_FAILED, LEFT_APP)
  - drift rate from ``nav_stats``
  - action_failed rate
  - LEFT_APP breakdown by scope category
  - scroll transition count
  - meaningful self-loop count (scroll/toggle/input)
  - ambiguous selector count
  - skipped risky action count
  - input side-effect warnings (``cleared=False``)
  - untrusted target count
  - repeated action ratio using ``N(s, a)`` visit counts
  - state merge risk groups (same name, different structural fingerprint)

The validator is generic — it does not know anything about specific apps,
only the trace schema. Synthetic traces in unit tests exercise every metric.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "max_sentinel_rate": 0.4,
    "max_drift_rate": 0.3,
    "max_action_failed_rate": 0.3,
}

_MEANINGFUL_SELF_LOOPS = frozenset({"scroll_up", "scroll_down", "input_text"})
_SCROLL_TYPES = frozenset({"scroll_up", "scroll_down"})


def _action_type(action: dict[str, Any]) -> str:
    return (action.get("action_type") or action.get("type") or "").lower()


def _action_key(action: dict[str, Any]) -> str:
    """Compact stable identity for repeated-action accounting."""
    rid = action.get("target_resource_id") or action.get("resource_id") or ""
    text = action.get("target_text") or ""
    cd = action.get("target_content_desc") or ""
    cls = action.get("target_class_name") or action.get("target_class") or ""
    return "|".join([_action_type(action), rid, text, cd, cls])


def validate_trace(data: dict[str, Any]) -> dict[str, Any]:
    """Compute the quality summary for an already-loaded trace dict.

    Returns a JSON-serializable summary plus a list of warnings whose
    presence indicates the trace falls below the validator's defaults.
    """
    traces: list[dict[str, Any]] = list(data.get("traces", []))
    screens: dict[str, dict[str, Any]] = data.get("screens", {}) or {}
    nav_stats: dict[str, Any] = data.get("nav_stats", {}) or {}
    action_attempts: list[dict[str, Any]] = list(data.get("action_attempts", []) or [])
    total = len(traces)

    sentinel_counter: Counter[str] = Counter()
    left_app_by_scope: Counter[str] = Counter()
    scroll_transitions = 0
    meaningful_self_loops = 0
    ambiguous_selectors = 0
    skipped_risky = 0
    skipped_unsafe_input = 0
    input_side_effect_warnings = 0
    action_failed = 0
    action_count: Counter[tuple[str, str]] = Counter()
    attempt_status_counter: Counter[str] = Counter()
    risk_severity_counter: Counter[str] = Counter()

    # ActionAttempt records — never FSM transitions, but informative.
    for a in action_attempts:
        status = (a.get("status") or "").strip()
        attempt_status_counter[status] += 1
        meta = a.get("metadata") or {}
        sev = meta.get("severity")
        if sev:
            risk_severity_counter[str(sev)] += 1
        if status == "skipped_risky":
            skipped_risky += 1
        elif status == "ambiguous_selector":
            ambiguous_selectors += 1
        elif status == "skipped_unsafe_input":
            skipped_unsafe_input += 1

    SENTINELS = {"COLD_START_FAILED", "ACTION_FAILED", "LEFT_APP"}  # noqa: N806

    for t in traces:
        tgt = t.get("target_state_id") or ""
        action = t.get("action") or {}
        src = t.get("source_state_id") or ""
        md = t.get("metadata") or {}

        if tgt in SENTINELS:
            sentinel_counter[tgt] += 1
            if tgt == "ACTION_FAILED":
                action_failed += 1
            if tgt == "LEFT_APP":
                reason = md.get("left_app_reason") or md.get("scope_post") or "unknown"
                left_app_by_scope[reason] += 1
            if md.get("selector_resolution") == "ambiguous":
                ambiguous_selectors += 1
            continue

        atype = _action_type(action)
        if atype in _SCROLL_TYPES:
            scroll_transitions += 1
        if src and src == tgt and atype in _MEANINGFUL_SELF_LOOPS:
            meaningful_self_loops += 1
        if md.get("selector_resolution") == "ambiguous":
            ambiguous_selectors += 1
        if md.get("risk_tags"):
            # An entry with risk_tags AND no execution means the explorer
            # decided to skip; trust the explorer's decision and count it.
            skipped_risky += 1
        if md.get("cleared") is False:
            input_side_effect_warnings += 1
        action_count[(src, _action_key(action))] += 1

    repeated_pairs = sum(1 for n in action_count.values() if n > 1)
    repeated_action_ratio = (repeated_pairs / len(action_count)) if action_count else 0.0

    # State merge risk groups: same ``state.name`` but distinct structural
    # fingerprints across raw screens. Derived purely from the screens dict.
    name_to_fps: dict[str, set[str]] = defaultdict(set)
    for _sid, scr in screens.items():
        page_title = (scr.get("page_title") or "").strip()
        struct_fp = scr.get("structural_fingerprint") or scr.get("fingerprint") or ""
        if page_title and struct_fp:
            name_to_fps[page_title].add(struct_fp)
    merge_risk_groups = {
        name: sorted(list(fps)) for name, fps in name_to_fps.items() if len(fps) > 1
    }

    drift_count = int(nav_stats.get("drift_count", 0))
    untrusted_targets = int(nav_stats.get("untrusted_targets", 0))
    sentinel_total = sum(sentinel_counter.values())

    summary: dict[str, Any] = {
        "total_steps": total,
        "sentinel_rate": (sentinel_total / total) if total else 0.0,
        "sentinel_breakdown": dict(sentinel_counter),
        "drift_rate": (drift_count / total) if total else 0.0,
        "action_failed_rate": (action_failed / total) if total else 0.0,
        "left_app_by_scope": dict(left_app_by_scope),
        "scroll_transition_count": scroll_transitions,
        "meaningful_self_loop_count": meaningful_self_loops,
        "ambiguous_selector_count": ambiguous_selectors,
        "skipped_risky_count": skipped_risky,
        "skipped_unsafe_input_count": skipped_unsafe_input,
        "input_side_effect_warnings": input_side_effect_warnings,
        "untrusted_target_count": untrusted_targets,
        "repeated_action_ratio": round(repeated_action_ratio, 4),
        "state_merge_risk_groups": merge_risk_groups,
        "action_attempt_status_breakdown": dict(attempt_status_counter),
        "action_attempt_severity_breakdown": dict(risk_severity_counter),
        "nav_stats": nav_stats,
    }

    warnings: list[str] = []
    if summary["sentinel_rate"] > _DEFAULT_THRESHOLDS["max_sentinel_rate"]:
        warnings.append(
            f"sentinel_rate {summary['sentinel_rate']:.2f} exceeds threshold "
            f"{_DEFAULT_THRESHOLDS['max_sentinel_rate']}"
        )
    if summary["drift_rate"] > _DEFAULT_THRESHOLDS["max_drift_rate"]:
        warnings.append(
            f"drift_rate {summary['drift_rate']:.2f} exceeds threshold "
            f"{_DEFAULT_THRESHOLDS['max_drift_rate']}"
        )
    if summary["action_failed_rate"] > _DEFAULT_THRESHOLDS["max_action_failed_rate"]:
        warnings.append(
            f"action_failed_rate {summary['action_failed_rate']:.2f} exceeds threshold "
            f"{_DEFAULT_THRESHOLDS['max_action_failed_rate']}"
        )
    if summary["ambiguous_selector_count"]:
        warnings.append(
            f"{summary['ambiguous_selector_count']} ambiguous selector(s) "
            "detected — selectors should uniquely identify their target"
        )
    if summary["input_side_effect_warnings"]:
        warnings.append(
            f"{summary['input_side_effect_warnings']} INPUT_TEXT step(s) "
            "did not clear original text before setting"
        )
    if summary["state_merge_risk_groups"]:
        warnings.append(
            f"{len(summary['state_merge_risk_groups'])} state name(s) span "
            "multiple structural fingerprints — FSM builder may either drop "
            "merges or risk false merges"
        )
    if summary["action_attempt_severity_breakdown"].get("hard_block"):
        warnings.append(
            f"{summary['action_attempt_severity_breakdown']['hard_block']} "
            "hard-block risk attempt(s) were refused — review for app-level "
            "safety surface coverage"
        )
    if summary["skipped_unsafe_input_count"]:
        warnings.append(
            f"{summary['skipped_unsafe_input_count']} INPUT_TEXT attempt(s) "
            "were refused because clear-before-set could not be guaranteed"
        )
    summary["warnings"] = warnings
    return summary


def _render_text(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"total_steps:                {summary['total_steps']}")
    lines.append(f"sentinel_rate:              {summary['sentinel_rate']:.3f}")
    if summary["sentinel_breakdown"]:
        for k, v in sorted(summary["sentinel_breakdown"].items()):
            lines.append(f"  {k:<26}{v}")
    lines.append(f"drift_rate:                 {summary['drift_rate']:.3f}")
    lines.append(f"action_failed_rate:         {summary['action_failed_rate']:.3f}")
    if summary["left_app_by_scope"]:
        lines.append("left_app_by_scope:")
        for k, v in sorted(summary["left_app_by_scope"].items()):
            lines.append(f"  {k:<26}{v}")
    lines.append(f"scroll_transition_count:    {summary['scroll_transition_count']}")
    lines.append(f"meaningful_self_loop_count: {summary['meaningful_self_loop_count']}")
    lines.append(f"ambiguous_selector_count:   {summary['ambiguous_selector_count']}")
    lines.append(f"skipped_risky_count:        {summary['skipped_risky_count']}")
    lines.append(f"input_side_effect_warnings: {summary['input_side_effect_warnings']}")
    lines.append(f"untrusted_target_count:     {summary['untrusted_target_count']}")
    lines.append(f"repeated_action_ratio:      {summary['repeated_action_ratio']:.3f}")
    if summary["state_merge_risk_groups"]:
        lines.append("state_merge_risk_groups:")
        for name, fps in summary["state_merge_risk_groups"].items():
            lines.append(f"  {name}: {len(fps)} fingerprints")
    if summary["warnings"]:
        lines.append("")
        lines.append("WARNINGS:")
        for w in summary["warnings"]:
            lines.append(f"  - {w}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vigil-validate-trace",
        description="Quality summary for exploration trace JSON files.",
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="Path to an exploration trace JSON.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human-readable summary.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        logger.error(f"trace file not found: {args.input}")
        return 2
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error(f"invalid JSON in {args.input}: {exc}")
        return 2

    summary = validate_trace(data)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(_render_text(summary))

    return 1 if summary["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())
