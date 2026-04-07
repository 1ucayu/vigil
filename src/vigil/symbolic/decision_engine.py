"""Combined verification decision engine with tier routing.

Orchestrates the three-tier verification pipeline:
  Tier 1 (FSM structural) → Tier 2 (DSL semantic) → Tier 3 (micro-evolution)

Returns ALLOW / DENY / UNCERTAIN for each proposed action.
Tier 3 (evolution) is handled externally — DecisionEngine returns UNCERTAIN
for unknown states, and the caller decides whether to invoke evolution.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from vigil.core.config import VerificationConfig
from vigil.models.fsm import AppFSM
from vigil.models.state import RawScreen
from vigil.symbolic.dsl_evaluator import DSLEvaluator, IntentContext, ScreenContext
from vigil.symbolic.fsm_checker import (
    FsmChecker,
    VerificationOutput,
    VerifyReason,
    VerifyResult,
)


class DecisionEngine:
    """Master VERIFY function combining Tier 1 + Tier 2.

    Implements the decision logic from CLAUDE.md §4.2:
    1. Tier 1: FSM structural check (state localization + transition validity
       + reachability + confidence)
    2. Tier 2: DSL guard evaluation (if guard exists on the matching transition)
    3. Return ALLOW / DENY / UNCERTAIN

    Args:
        fsm: The app's verified FSM.
        config: Verification config (for confidence_threshold).
        grammar_path: Path to DSL grammar file. If None, uses default.
    """

    def __init__(
        self,
        fsm: AppFSM,
        config: VerificationConfig | None = None,
        grammar_path: str | None = None,
    ) -> None:
        self._fsm = fsm
        self._checker = FsmChecker(fsm, config)
        self._evaluator: DSLEvaluator | None = None
        try:
            self._evaluator = DSLEvaluator(grammar_path)
        except Exception:
            logger.warning("DSLEvaluator init failed — Tier 2 disabled")

    def verify(
        self,
        current_screen: RawScreen,
        proposed_action: dict[str, Any],
        intent_ctx: IntentContext | None = None,
        goal_state: str | None = None,
    ) -> VerificationOutput:
        """The master VERIFY function with full screen input.

        Args:
            current_screen: The current device screen.
            proposed_action: Action dict (e.g., {"type": "click", "target": "e_001"}).
            intent_ctx: User intent variables for guard binding.
            goal_state: Optional goal state for reachability checking.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        # Tier 1: structural FSM check (includes state localization)
        tier1_result = self._checker.verify(current_screen, proposed_action, goal_state)

        if tier1_result.result != VerifyResult.ALLOW:
            return tier1_result

        # Tier 2: DSL guard check
        if self._evaluator is None or tier1_result.current_state_id is None:
            return tier1_result

        transition = self._fsm.get_transition(tier1_result.current_state_id, proposed_action)
        if transition is None or transition.guard is None:
            return tier1_result

        screen_ctx = self._build_screen_context(current_screen)
        action_ctx = self._build_action_context(proposed_action, current_screen)

        guard_result = self._evaluator.evaluate(
            transition.guard,
            intent_ctx=intent_ctx,
            screen_ctx=screen_ctx,
            action_ctx=action_ctx,
        )

        if not guard_result.passed:
            return VerificationOutput(
                result=VerifyResult.DENY,
                reason=VerifyReason.GUARD_FAILED,
                current_state_id=tier1_result.current_state_id,
                target_state_id=tier1_result.target_state_id,
                confidence=tier1_result.confidence,
                details=(
                    f"Guard failed: {guard_result.guard_expression} → {guard_result.failure_reason}"
                ),
            )

        return tier1_result

    def verify_by_state(
        self,
        current_state_id: str,
        proposed_action: dict[str, Any],
        intent_ctx: IntentContext | None = None,
        screen_ctx: ScreenContext | None = None,
        action_ctx: dict[str, Any] | None = None,
        goal_state: str | None = None,
    ) -> VerificationOutput:
        """Verify when state is already known (skip localization).

        Args:
            current_state_id: The current FSM state ID.
            proposed_action: Action dict.
            intent_ctx: User intent variables for guard binding.
            screen_ctx: Screen context for guard evaluation.
            action_ctx: Action metadata for action_pred evaluation.
            goal_state: Optional goal state for reachability checking.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        # Tier 1: structural check
        tier1_result = self._checker.verify_by_state(current_state_id, proposed_action, goal_state)

        if tier1_result.result != VerifyResult.ALLOW:
            return tier1_result

        # Tier 2: DSL guard check
        if self._evaluator is None:
            return tier1_result

        transition = self._fsm.get_transition(current_state_id, proposed_action)
        if transition is None or transition.guard is None:
            return tier1_result

        if screen_ctx is None:
            screen_ctx = ScreenContext()

        guard_result = self._evaluator.evaluate(
            transition.guard,
            intent_ctx=intent_ctx,
            screen_ctx=screen_ctx,
            action_ctx=action_ctx,
        )

        if not guard_result.passed:
            return VerificationOutput(
                result=VerifyResult.DENY,
                reason=VerifyReason.GUARD_FAILED,
                current_state_id=tier1_result.current_state_id,
                target_state_id=tier1_result.target_state_id,
                confidence=tier1_result.confidence,
                details=(
                    f"Guard failed: {guard_result.guard_expression} → {guard_result.failure_reason}"
                ),
            )

        return tier1_result

    @staticmethod
    def _build_screen_context(screen: RawScreen) -> ScreenContext:
        """Convert RawScreen elements into ScreenContext for guard evaluation.

        Builds elements dict keyed by element_id, resource_id, and synthesized
        aliases (e.g., Switch_0, EditText_0) for elements without resource_id.
        Aliases use the same logic as DslGenerator._build_element_reference_table
        so that guards generated offline resolve correctly at runtime.
        """
        elements: dict[str, dict[str, Any]] = {}
        elements_by_id = {e.element_id: e for e in screen.elements}

        for e in screen.elements:
            props: dict[str, Any] = {
                "text": e.text or "",
                "content_description": e.content_description or "",
                "is_checked": e.is_checked,
                "is_enabled": e.is_enabled,
                "value": e.text or "",
            }

            # Add children info for container elements
            if e.children:
                child_texts: list[dict[str, str]] = []
                for cid in e.children:
                    child = elements_by_id.get(cid)
                    if child and child.text:
                        child_texts.append({"text": child.text})
                props["children"] = child_texts
                props["children_count"] = len(e.children)

            # Key by element_id
            elements[e.element_id] = props
            # Also key by resource_id if available
            if e.resource_id:
                elements[e.resource_id] = props

        # Build synthesized aliases for interactable elements without resource_id.
        # Must match DslGenerator._build_element_reference_table logic exactly:
        # only interactable elements, only when resource_id is empty,
        # counter increments per short class name.
        class_counts: dict[str, int] = {}
        for e in screen.elements:
            is_interactable = e.is_clickable or e.is_scrollable or e.is_editable or e.is_checkable
            if not is_interactable or e.resource_id:
                continue
            cls = e.class_name or ""
            short = cls.rsplit(".", 1)[-1] if "." in cls else cls
            if short:
                idx = class_counts.get(short, 0)
                class_counts[short] = idx + 1
                alias = f"{short}_{idx}"
                elements[alias] = elements[e.element_id]

        return ScreenContext(elements=elements)

    @staticmethod
    def _build_action_context(
        proposed_action: dict[str, Any],
        current_screen: RawScreen,
    ) -> dict[str, Any]:
        """Build action context for action_pred evaluation.

        Extracts the target element's text and resource_id from current_screen
        using the action's target element_id.
        """
        ctx: dict[str, Any] = {"action_type": proposed_action.get("type", "")}
        target_id = proposed_action.get("target")
        if target_id and current_screen:
            for e in current_screen.elements:
                if e.element_id == target_id:
                    ctx["target_text"] = e.text or ""
                    ctx["target_resource_id"] = e.resource_id or ""
                    ctx["target_content_desc"] = e.content_description or ""
                    break
        return ctx
