"""Stage 2.5: Semantic Grounding.

Enriches abstract states with LLM-generated semantic metadata:
1. State descriptions (alt text, page function, expected actions)
2. Icon semantic labels (for text-less clickable elements)
3. State invariant mining (exact/range invariants -> derives static/dynamic)

All LLM calls are multimodal (accessibility tree + screenshot).
Activity prior from Stage 0 provides top-down cross-validation.

This stage REPLACES the rule-based container classifier in StateAbstractor.
Static/dynamic is now derived from invariant properties, not heuristic rules.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.llm_client import LlmClient
from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    StateSemanticProfile,
)
from vigil.neuro.app_prior import AppPrior
from vigil.symbolic.dsl_evaluator import DSLEvaluator, ScreenContext

_DESC_SYSTEM_PROMPT = """\
You are annotating mobile app UI states for a runtime verification system.
You will receive a screenshot and an accessibility tree of one screen.
Return ONLY valid JSON with these fields:
{
  "alt_text": "1-2 sentence description a blind user would understand",
  "page_function": "hierarchical/slash/path (e.g. settings/wifi/list)",
  "expected_actions": ["semantic_action_name", ...]
}
Do NOT hallucinate elements not visible in the screenshot.
Use a consistent hierarchical vocabulary for page_function."""

_ICON_SYSTEM_PROMPT = """\
You are labeling unlabeled clickable UI elements in a mobile app screenshot.
Each element has no text and no accessibility label — you must infer its \
function from its visual appearance at the given screen coordinates.
Return ONLY valid JSON: {"element_id": "snake_case_label", ...}
Use functional names (e.g. delete_button, share_icon, back_arrow), \
not visual descriptions (e.g. red_circle). Labels must be 2-3 words, \
semantically stable across app versions."""

_INVARIANT_SYSTEM_PROMPT = """\
You are mining structural invariants for a mobile app UI state.
Given one observation of a screen's accessibility tree, propose 3-8 \
invariant expressions that should ALWAYS be true regardless of when/where \
this screen is visited.

Use ONLY these DSL predicates:
  read(element_alias, property) op value
  count(element_alias) op value
  value(element_alias) op value

Where op is one of: == != > < >= <=
element_alias is a resource_id or synthesized alias (e.g. Switch_0), \
NEVER a raw e_XXXX id.

Focus on structural invariants (element existence, count ranges, type \
checks), NOT specific content values that change between visits.

Return ONLY a JSON array of invariant strings:
["count(com.app:id/list) >= 1", "read(action_bar_title, text) != \\"\\""]"""


class SemanticGrounder:
    """Stage 2.5: Enrich FSM states with semantic metadata via multimodal LLM.

    Args:
        llm: Configured LLM client for multimodal generation.
        grammar_path: Path to DSL grammar file (for invariant validation).
    """

    def __init__(
        self,
        llm: LlmClient,
        grammar_path: str | None = None,
    ) -> None:
        self._llm = llm
        self._evaluator: DSLEvaluator | None = None
        try:
            self._evaluator = DSLEvaluator(grammar_path)
        except Exception:
            logger.warning("DSLEvaluator init failed — invariant verification disabled")

    def ground_all_states(
        self,
        fsm: AppFSM,
        raw_screens: dict[str, dict[str, Any]],
        app_prior: AppPrior | None = None,
        trace_data: dict[str, Any] | None = None,
    ) -> AppFSM:
        """Run all grounding sub-tasks on every state in the FSM.

        Args:
            fsm: The app FSM to enrich (modified in place and returned).
            raw_screens: Map of screen_id -> screen data dict (from trace JSON).
                Each entry should have at least 'screenshot_path', 'xml_tree_path',
                and 'interactable_elements'.
            app_prior: Optional app prior from Stage 0 for cross-validation.
            trace_data: Optional full trace data for multi-observation access.

        Returns:
            The same FSM with enriched state metadata.
        """
        for state_id, state in fsm.states.items():
            logger.info(f"Grounding state {state_id} ({state.name})")

            observations = self._collect_observations(state, raw_screens)
            if not observations:
                logger.warning(f"No observations for state {state_id}, skipping")
                continue

            profile = self.generate_state_description(state, observations, app_prior)
            icon_labels = self.annotate_icons(state, observations)
            if icon_labels:
                profile.icon_labels = icon_labels

            state.semantic_profile = profile

            invariants, confidence, container_type = self.mine_invariants(state, observations)
            state.state_invariants = invariants
            state.invariant_confidence = confidence
            if container_type != ContainerType.NONE:
                state.container_type = container_type

        return fsm

    def generate_state_description(
        self,
        state: AbstractState,
        observations: list[dict[str, Any]],
        app_prior: AppPrior | None = None,
    ) -> StateSemanticProfile:
        """Generate semantic description for a state via multimodal LLM."""
        obs = observations[0]
        prompt_parts: list[str] = []

        if app_prior and state.activity_name:
            activity_info = _find_activity(app_prior, state.activity_name)
            if activity_info:
                prompt_parts.append(f"This screen belongs to Activity: {activity_info.name}")
                if activity_info.label:
                    prompt_parts.append(f"Activity label: {activity_info.label}")

        element_table = _build_element_table(obs)
        prompt_parts.append(f"Accessibility tree elements:\n{element_table}")
        prompt_parts.append(
            "Generate a JSON annotation for this screen with: "
            "alt_text, page_function, expected_actions."
        )
        user_prompt = "\n\n".join(prompt_parts)

        screenshot_path = obs.get("screenshot_path")
        if screenshot_path and Path(screenshot_path).exists():
            response = self._llm.generate_with_images(
                _DESC_SYSTEM_PROMPT,
                user_prompt,
                [Path(screenshot_path)],
                ["Current screen:"],
            )
        else:
            response = self._llm.generate(_DESC_SYSTEM_PROMPT, user_prompt)

        parsed = _parse_json(response)
        if parsed is None:
            logger.warning(f"Failed to parse state description for {state.state_id}")
            return StateSemanticProfile()

        confidence = self._compute_description_confidence(parsed, state, app_prior)

        return StateSemanticProfile(
            alt_text=str(parsed.get("alt_text", "")),
            page_function=str(parsed.get("page_function", "")),
            expected_actions=parsed.get("expected_actions", []),
            generation_confidence=confidence,
        )

    def annotate_icons(
        self,
        state: AbstractState,
        observations: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Label clickable elements that have no text or content_description."""
        obs = observations[0]
        elements = obs.get("interactable_elements", [])

        anonymous: list[dict[str, Any]] = []
        for e in elements:
            has_text = bool(e.get("text"))
            has_desc = bool(e.get("content_description"))
            is_clickable = e.get("is_clickable", False)
            if is_clickable and not has_text and not has_desc:
                anonymous.append(e)

        if not anonymous:
            return {}

        lines: list[str] = []
        for e in anonymous:
            eid = e.get("element_id", "?")
            bounds = e.get("bounds", [0, 0, 0, 0])
            cls = e.get("class_name", "")
            rid = e.get("resource_id", "")
            lines.append(f"  {eid}: class={cls}, rid={rid}, bounds={bounds}")

        user_prompt = (
            "The following clickable UI elements have no text or accessibility "
            "label. Based on their visual appearance at these coordinates, "
            "provide a semantic label for each.\n\n" + "\n".join(lines)
        )

        screenshot_path = obs.get("screenshot_path")
        if screenshot_path and Path(screenshot_path).exists():
            response = self._llm.generate_with_images(
                _ICON_SYSTEM_PROMPT,
                user_prompt,
                [Path(screenshot_path)],
            )
        else:
            response = self._llm.generate(_ICON_SYSTEM_PROMPT, user_prompt)

        parsed = _parse_json(response)
        if not isinstance(parsed, dict):
            logger.warning(f"Failed to parse icon annotations for {state.state_id}")
            return {}

        valid_ids = {e.get("element_id") for e in anonymous}
        return {k: str(v) for k, v in parsed.items() if k in valid_ids}

    def mine_invariants(
        self,
        state: AbstractState,
        observations: list[dict[str, Any]],
    ) -> tuple[list[str], float, ContainerType]:
        """Mine and verify structural invariants from multiple observations.

        Uses cross-visit statistical diff when >=2 observations available,
        falls back to LLM-only approach for single observation.
        """
        if not observations:
            return [], 0.0, ContainerType.NONE

        if len(observations) >= 2:
            candidates, confidence = self._compute_cross_visit_invariants(observations)
            if candidates:
                container_type = self._derive_container_type(candidates, observations)
                return candidates, confidence, container_type

        candidates = self._propose_invariants(observations[0])
        if not candidates:
            return [], 0.0, ContainerType.NONE

        if len(observations) == 1:
            container_type = self._soft_predict_container_type(candidates)
            return candidates, 0.5, container_type

        validated, confidence = self._verify_invariants(candidates, observations)
        container_type = self._derive_container_type(validated, observations)
        return validated, confidence, container_type

    def _compute_cross_visit_invariants(
        self,
        observations: list[dict[str, Any]],
    ) -> tuple[list[str], float]:
        """Mine invariants from cross-visit structural diff.

        Compares element properties across K visits to find stable properties.
        """
        prop_tracker: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))

        for obs in observations:
            elements = obs.get("interactable_elements", [])
            for e in elements:
                alias = e.get("resource_id") or ""
                if not alias:
                    cls = (e.get("class_name") or "").rsplit(".", 1)[-1]
                    alias = f"{cls}_{e.get('element_id', '')}"
                if not alias:
                    continue

                prop_tracker[alias]["text"].append(e.get("text") or "")
                prop_tracker[alias]["is_checked"].append(e.get("is_checked", False))
                prop_tracker[alias]["is_enabled"].append(e.get("is_enabled", True))
                children = e.get("children", [])
                prop_tracker[alias]["children_count"].append(len(children))

        candidates: list[str] = []
        n = len(observations)

        for alias, props in prop_tracker.items():
            for prop_name, values in props.items():
                if len(values) < n:
                    continue
                unique = set(str(v) for v in values)
                if len(unique) != 1:
                    continue

                val = values[0]
                if prop_name == "text" and val:
                    candidates.append(f'read({alias}, text) == "{val}"')
                elif prop_name == "is_enabled" and val is True:
                    candidates.append(f"read({alias}, is_enabled) == true")
                elif prop_name == "children_count" and isinstance(val, int) and val > 0:
                    candidates.append(f"count({alias}) == {val}")

        confidence = min(1.0, n / 10)
        return candidates, round(confidence, 2)

    def _propose_invariants(self, observation: dict[str, Any]) -> list[str]:
        """Ask LLM to propose candidate invariant expressions."""
        element_table = _build_element_table(observation)
        user_prompt = (
            f"Accessibility tree elements:\n{element_table}\n\n"
            "Propose 3-8 structural invariant expressions for this screen."
        )

        screenshot_path = observation.get("screenshot_path")
        if screenshot_path and Path(screenshot_path).exists():
            response = self._llm.generate_with_images(
                _INVARIANT_SYSTEM_PROMPT,
                user_prompt,
                [Path(screenshot_path)],
            )
        else:
            response = self._llm.generate(_INVARIANT_SYSTEM_PROMPT, user_prompt)

        parsed = _parse_json(response)
        if not isinstance(parsed, list):
            logger.warning("LLM invariant proposal was not a JSON array")
            return []
        return [str(inv) for inv in parsed if isinstance(inv, str) and inv.strip()]

    def _verify_invariants(
        self,
        candidates: list[str],
        observations: list[dict[str, Any]],
    ) -> tuple[list[str], float]:
        """Verify each candidate invariant against all observations."""
        if self._evaluator is None:
            return candidates, 0.5

        validated: list[str] = []
        total_checks = 0
        total_passed = 0

        for inv in candidates:
            passed_all = True
            for obs in observations:
                ctx = _build_screen_context_from_obs(obs)
                result = self._evaluator.evaluate(inv, screen_ctx=ctx)
                total_checks += 1
                if result.passed:
                    total_passed += 1
                else:
                    passed_all = False
                    break
            if passed_all:
                validated.append(inv)

        confidence = total_passed / total_checks if total_checks > 0 else 0.0
        return validated, round(confidence, 3)

    @staticmethod
    def _derive_container_type(
        invariants: list[str],
        observations: list[dict[str, Any]],
    ) -> ContainerType:
        """Derive container type from invariant properties and observation variance."""
        count_pattern = re.compile(r"count\(.+\)\s*(==)\s*(\d+)")
        range_pattern = re.compile(r"count\(.+\)\s*(>=|>)\s*(\d+)")

        has_exact_count = False
        has_range_count = False

        for inv in invariants:
            if count_pattern.search(inv):
                has_exact_count = True
            elif range_pattern.search(inv):
                has_range_count = True

        if has_exact_count:
            return ContainerType.STATIC
        if has_range_count:
            return ContainerType.DYNAMIC
        return ContainerType.NONE

    @staticmethod
    def _soft_predict_container_type(candidates: list[str]) -> ContainerType:
        """Soft prediction from single observation (low confidence)."""
        for inv in candidates:
            if "count(" in inv and ">=" in inv:
                return ContainerType.DYNAMIC
            if "count(" in inv and "==" in inv:
                return ContainerType.STATIC
        return ContainerType.NONE

    @staticmethod
    def _compute_description_confidence(
        parsed: dict[str, Any],
        state: AbstractState,
        app_prior: AppPrior | None,
    ) -> float:
        """Cross-validate page_function with Activity prior."""
        if app_prior is None or not state.activity_name:
            return 0.7

        activity_info = _find_activity(app_prior, state.activity_name)
        if activity_info is None or activity_info.predicted_function is None:
            return 0.7

        page_function = str(parsed.get("page_function", ""))
        predicted = activity_info.predicted_function
        if not page_function or not predicted:
            return 0.7

        if _functions_consistent(page_function, predicted):
            return 1.0

        logger.warning(
            f"State {state.state_id}: page_function={page_function!r} "
            f"conflicts with prior={predicted!r}"
        )
        return 0.5

    @staticmethod
    def _collect_observations(
        state: AbstractState,
        raw_screens: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Collect all observation dicts for a state's raw screens."""
        observations: list[dict[str, Any]] = []
        for sid in state.raw_screens:
            if sid in raw_screens:
                observations.append(raw_screens[sid])
        if not observations and raw_screens:
            first_key = next(iter(raw_screens))
            observations.append(raw_screens[first_key])
        return observations


def _find_activity(prior: AppPrior, activity_name: str) -> Any | None:
    """Find an ActivityInfo by name in the prior."""
    for a in prior.activities:
        if a.name == activity_name:
            return a
    return None


def _functions_consistent(page_fn: str, predicted: str) -> bool:
    """Check if page_function is consistent with Activity predicted_function."""
    page_parts = set(page_fn.lower().strip("/").split("/"))
    pred_parts = set(predicted.lower().strip("/").split("/"))
    return bool(page_parts & pred_parts)


def _build_element_table(obs: dict[str, Any]) -> str:
    """Build a concise element summary table from an observation dict."""
    elements = obs.get("interactable_elements", [])
    if not elements:
        return "(no interactable elements)"

    lines: list[str] = []
    for e in elements:
        eid = e.get("element_id", "?")
        cls = (e.get("class_name") or "").rsplit(".", 1)[-1]
        text = e.get("text") or ""
        rid = e.get("resource_id") or ""
        desc = e.get("content_description") or ""
        parts = [f"{cls}[{eid}]"]
        if rid:
            parts.append(f"rid={rid}")
        if text:
            parts.append(f'text="{text}"')
        if desc:
            parts.append(f'desc="{desc}"')
        flags: list[str] = []
        if e.get("is_clickable"):
            flags.append("click")
        if e.get("is_scrollable"):
            flags.append("scroll")
        if e.get("is_checkable"):
            flags.append(f"check={'✓' if e.get('is_checked') else '✗'}")
        if flags:
            parts.append(f"[{','.join(flags)}]")
        lines.append("  " + " ".join(parts))
    return "\n".join(lines)


def _build_screen_context_from_obs(obs: dict[str, Any]) -> ScreenContext:
    """Build a ScreenContext from a raw observation dict for guard evaluation."""
    elements: dict[str, dict[str, Any]] = {}
    for e in obs.get("interactable_elements", []):
        eid = e.get("element_id", "")
        props: dict[str, Any] = {
            "text": e.get("text") or "",
            "content_description": e.get("content_description") or "",
            "is_checked": e.get("is_checked", False),
            "is_enabled": e.get("is_enabled", True),
            "value": e.get("text") or "",
        }
        elements[eid] = props
        rid = e.get("resource_id")
        if rid:
            elements[rid] = props

        children = e.get("children", [])
        if children:
            props["children_count"] = len(children)

    return ScreenContext(elements=elements)


def _parse_json(response: str) -> Any | None:
    """Parse LLM response as JSON, stripping markdown fences if present."""
    text = response.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
