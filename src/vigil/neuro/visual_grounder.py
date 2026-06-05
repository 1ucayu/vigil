"""Focused screenshot/layout grounding for guard generation.

This module is a lightweight alternative to :mod:`vigil.neuro.semantic_grounder`.
It only enriches state annotations with visual layout summaries and icon labels;
it does not mine invariants, alter state identity, add transitions, or make
runtime decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.llm_client import LlmClient
from vigil.models.fsm import AppFSM, StateAnnotations
from vigil.neuro.app_prior import AppPrior

_VISUAL_SYSTEM_PROMPT = """\
You are annotating Android mobile GUI screenshots for a runtime verifier.
You receive one screenshot plus a compact accessibility element table.

Return ONLY valid JSON with this exact shape:
{
  "alt_text": "description focused on visual layout and visible controls",
  "layout_summary": "description of regions, lists/forms/dialogs, and hierarchy",
  "page_function": "stable slash/path such as chat/thread or banking/transfer/form",
  "expected_actions": ["semantic_action", "..."],
  "icon_labels": [
    {
      "element_id": "e_0001",
      "label": "snake_case_functional_label",
      "confidence": 0.0,
      "basis": "visual/accessibility reason"
    }
  ],
  "confidence": 0.0
}

Rules:
- Describe only what is visible in the screenshot/accessibility data.
- Icon labels are for textless or visually ambiguous controls only.
- Use functional labels such as back_button, send_button, overflow_menu,
  delete_button, add_item_button. Do not use color/shape-only labels.
- Do not invent transitions, guards, state ids, user intent values, or runtime verdicts.
- Keep labels stable across captures; avoid names containing row instance values unless
  the visible UI truly identifies a list item.
"""


def ground_fsm_visual_annotations(
    fsm: AppFSM,
    raw_screens: dict[str, dict[str, Any]],
    llm: LlmClient,
    app_prior: AppPrior | None = None,
    *,
    force: bool = False,
    max_states: int | None = None,
) -> list[dict[str, Any]]:
    """Annotate FSM states with LLM-derived visual layout and icon-label metadata.

    The FSM is modified in place. Returned rows are intended for audit reports under
    ``output_docs/``.
    """
    report: list[dict[str, Any]] = []
    attempted = 0

    for state_id, state in fsm.states.items():
        if max_states is not None and attempted >= max_states:
            break

        if (
            not force
            and state.annotations.alt_text
            and state.annotations.widget_aliases
            and state.annotations.generation_confidence > 0
        ):
            report.append(
                {
                    "state_id": state_id,
                    "status": "skipped_existing",
                    "screen_id": None,
                    "page_function": state.annotations.page_function,
                    "icon_labels": len(state.annotations.widget_aliases),
                }
            )
            continue

        attempted += 1
        obs = _first_observation(state.evidence.raw_screen_ids, raw_screens)
        if obs is None:
            report.append(
                {
                    "state_id": state_id,
                    "status": "skipped_no_observation",
                    "screen_id": None,
                }
            )
            continue

        screen_id = str(obs.get("screen_id") or state.evidence.raw_screen_ids[0])
        try:
            parsed = describe_screen_visuals(
                state_id=state_id,
                observation=obs,
                llm=llm,
                app_prior=app_prior,
            )
        except Exception as exc:
            logger.warning(f"Visual grounding failed for {state_id}: {exc}")
            report.append(
                {
                    "state_id": state_id,
                    "status": "failed",
                    "screen_id": screen_id,
                    "error": str(exc),
                }
            )
            continue

        _apply_visual_annotation(fsm, state_id, parsed)
        report.append(
            {
                "state_id": state_id,
                "status": "annotated",
                "screen_id": screen_id,
                "page_function": parsed.get("page_function", ""),
                "confidence": parsed.get("confidence", 0.0),
                "icon_labels": len(parsed.get("icon_labels") or []),
            }
        )

    return report


def describe_screen_visuals(
    *,
    state_id: str,
    observation: dict[str, Any],
    llm: LlmClient,
    app_prior: AppPrior | None = None,
) -> dict[str, Any]:
    """Ask the LLM for a visual/layout description of one observed screen."""
    prompt = _build_visual_prompt(state_id, observation, app_prior)
    image_path = _existing_screenshot_path(observation)
    if image_path is not None:
        response = llm.generate_with_images(
            _VISUAL_SYSTEM_PROMPT,
            prompt,
            [image_path],
            [f"state={state_id} screen={observation.get('screen_id', '')}"],
        )
    else:
        response = llm.generate(_VISUAL_SYSTEM_PROMPT, prompt)

    parsed = _parse_json(response)
    if not isinstance(parsed, dict):
        raise ValueError("visual grounding response was not a JSON object")
    return parsed


def write_visual_grounding_report(report: list[dict[str, Any]], path: Path) -> None:
    """Write a visual grounding audit report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _first_observation(
    screen_ids: list[str],
    raw_screens: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for sid in screen_ids:
        obs = raw_screens.get(sid)
        if obs is not None:
            return obs
    return None


def _apply_visual_annotation(fsm: AppFSM, state_id: str, parsed: dict[str, Any]) -> None:
    state = fsm.states[state_id]
    current = state.annotations

    alt_text = str(parsed.get("alt_text") or "").strip()
    layout_summary = str(parsed.get("layout_summary") or "").strip()
    if layout_summary and layout_summary not in alt_text:
        alt_text = f"{alt_text}\nLayout: {layout_summary}".strip()
    if not alt_text:
        alt_text = current.alt_text

    widget_aliases = _normalize_icon_labels(parsed.get("icon_labels"))
    state.annotations = StateAnnotations(
        display_name=current.display_name,
        alt_text=alt_text,
        page_function=str(parsed.get("page_function") or current.page_function or ""),
        expected_actions=[
            str(item) for item in (parsed.get("expected_actions") or []) if str(item).strip()
        ]
        or current.expected_actions,
        widget_aliases=widget_aliases or current.widget_aliases,
        generation_confidence=_coerce_confidence(
            parsed.get("confidence", current.generation_confidence)
        ),
    )


def _normalize_icon_labels(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = [
            {"element_id": key, "label": value, "confidence": 0.5, "basis": "dict_response"}
            for key, value in raw.items()
        ]
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("element_id") or "").strip()
        label = _slug_label(str(item.get("label") or ""))
        if not element_id or not label or element_id in seen:
            continue
        seen.add(element_id)
        out.append(
            {
                "element_id": element_id,
                "label": label,
                "confidence": _coerce_confidence(item.get("confidence")),
                "basis": str(item.get("basis") or ""),
            }
        )
    return out


def _slug_label(value: str) -> str:
    chars: list[str] = []
    prev_us = False
    for ch in value.strip().lower():
        if ch.isalnum():
            chars.append(ch)
            prev_us = False
        elif not prev_us:
            chars.append("_")
            prev_us = True
    return "".join(chars).strip("_")


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _build_visual_prompt(
    state_id: str,
    observation: dict[str, Any],
    app_prior: AppPrior | None,
) -> str:
    elements = observation.get("interactable_elements") or observation.get("elements") or []
    parts = [
        f"state_id: {state_id}",
        f"screen_id: {observation.get('screen_id', '')}",
        f"activity: {observation.get('activity_name', '')}",
        f"page_title: {observation.get('page_title', '')}",
        "Focus on layout, visible grouping, forms/lists/dialogs, and functional icon labels.",
    ]

    if app_prior is not None:
        parts.append(_static_prior_summary(app_prior))

    parts.append("Interactable/accessibility elements:")
    parts.extend(_element_lines(elements))
    return "\n".join(part for part in parts if part)


def _element_lines(elements: list[dict[str, Any]]) -> list[str]:
    if not elements:
        return ["  (no elements)"]

    lines: list[str] = []
    for element in elements:
        eid = str(element.get("element_id") or "?")
        cls = str(element.get("class_name") or "").rsplit(".", 1)[-1]
        rid = str(element.get("resource_id") or "")
        text = str(element.get("text") or "")
        desc = str(element.get("content_description") or "")
        bounds = element.get("bounds")
        flags: list[str] = []
        for key, name in (
            ("is_clickable", "click"),
            ("is_editable", "edit"),
            ("is_checkable", "checkable"),
            ("is_checked", "checked"),
            ("is_scrollable", "scroll"),
            ("is_enabled", "enabled"),
        ):
            if element.get(key):
                flags.append(name)

        pieces = [f"  - {eid}", f"class={cls}"]
        if rid:
            pieces.append(f"rid={rid}")
        if text:
            pieces.append(f"text={text!r}")
        if desc:
            pieces.append(f"desc={desc!r}")
        if bounds:
            pieces.append(f"bounds={bounds}")
        if flags:
            pieces.append(f"flags={','.join(flags)}")
        lines.append(" ".join(pieces))
    return lines


def _static_prior_summary(app_prior: AppPrior) -> str:
    parts = [f"app_prior_package: {app_prior.package_name}"]
    if app_prior.activities:
        labels = [
            (
                f"{activity.name} label={activity.label or ''} "
                f"function={activity.predicted_function or ''}"
            )
            for activity in app_prior.activities
        ]
        parts.append("activities: " + "; ".join(labels))
    if app_prior.permissions:
        parts.append("permissions: " + ", ".join(app_prior.permissions))
    if app_prior.string_arrays:
        names = list(app_prior.string_arrays)
        parts.append("string_arrays: " + ", ".join(names))
    return "\n".join(parts)


def _existing_screenshot_path(observation: dict[str, Any]) -> Path | None:
    raw_path = observation.get("screenshot_path")
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if path.exists():
        return path
    return None


def _parse_json(response: str) -> Any | None:
    text = response.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
