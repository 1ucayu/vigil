"""Combined verification decision engine with tier routing.

Orchestrates the three-tier verification pipeline:
  Tier 1 (FSM structural) → Tier 2 (DSL semantic) → Tier 3 (micro-evolution)

Returns ALLOW / DENY / UNCERTAIN for each proposed action. If an LlmFallback
is configured, any UNCERTAIN result is routed through the LLM to produce a
final ALLOW/DENY; LLM failures preserve the UNCERTAIN result.

Tier 3 (evolution) is handled externally — DecisionEngine returns UNCERTAIN
for unknown states, and the caller decides whether to invoke evolution.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from vigil.core.config import VerificationConfig
from vigil.models.fsm import AppFSM
from vigil.models.state import RawScreen
from vigil.symbolic.dsl_evaluator import (
    DSLEvaluator,
    GuardStatus,
    IntentContext,
    ScreenContext,
)
from vigil.symbolic.fsm_checker import (
    FsmChecker,
    VerificationOutput,
    VerifyReason,
    VerifyResult,
)
from vigil.symbolic.intent_extractor import IntentExtractor
from vigil.symbolic.invariant_checker import InvariantChecker
from vigil.symbolic.llm_fallback import LlmFallback


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
        intent_extractor: Optional IntentExtractor for auto-resolving
            $intent.* variables from a raw user instruction.
        llm_fallback: Optional LlmFallback. When present, any UNCERTAIN
            result produced by Tier 1-2 is routed through the LLM to
            produce a final ALLOW/DENY. On LLM failure the original
            UNCERTAIN result is preserved.
    """

    def __init__(
        self,
        fsm: AppFSM,
        config: VerificationConfig | None = None,
        grammar_path: str | None = None,
        intent_extractor: IntentExtractor | None = None,
        llm_fallback: LlmFallback | None = None,
    ) -> None:
        self._fsm = fsm
        self._checker = FsmChecker(fsm, config)
        self._evaluator: DSLEvaluator | None = None
        self._invariant_checker: InvariantChecker | None = None
        self._intent_extractor = intent_extractor
        self._llm_fallback = llm_fallback
        try:
            self._evaluator = DSLEvaluator(grammar_path)
            self._invariant_checker = InvariantChecker(fsm, evaluator=self._evaluator)
        except Exception:
            logger.warning("DSLEvaluator init failed — Tier 2 disabled")

    def verify(
        self,
        current_screen: RawScreen,
        proposed_action: dict[str, Any],
        intent_ctx: IntentContext | None = None,
        goal_state: str | None = None,
        raw_instruction: str | None = None,
    ) -> VerificationOutput:
        """The master VERIFY function with full screen input.

        Args:
            current_screen: The current device screen.
            proposed_action: Action dict (e.g., {"type": "click", "target": "e_001"}).
            intent_ctx: User intent variables for guard binding.
            goal_state: Optional goal state for reachability checking.
            raw_instruction: User's natural language instruction. If provided
                and intent_ctx is None, triggers auto-extraction via IntentExtractor.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        # Tier 1: structural FSM check (includes state localization)
        tier1_result = self._checker.verify(current_screen, proposed_action, goal_state)

        if tier1_result.result == VerifyResult.UNCERTAIN:
            return self._apply_llm_fallback(
                tier1_result, current_screen, proposed_action, raw_instruction
            )
        if tier1_result.result != VerifyResult.ALLOW:
            return tier1_result

        # Tier 2: DSL guard check
        if self._evaluator is None or tier1_result.current_state_id is None:
            return tier1_result

        screen_ctx = self._build_screen_context(current_screen)
        action_ctx = self._build_action_context(proposed_action, current_screen)

        transition = self._fsm.get_transition(tier1_result.current_state_id, proposed_action)
        intent_ctx = self._resolve_intent(
            intent_ctx, raw_instruction, tier1_result.current_state_id
        )

        if transition is not None and transition.guard is not None:
            guard_result = self._evaluator.evaluate(
                transition.guard,
                intent_ctx=intent_ctx,
                screen_ctx=screen_ctx,
                action_ctx=action_ctx,
            )
            routed = self._route_guard_result(guard_result, tier1_result)
            if routed is not None:
                if routed.result == VerifyResult.UNCERTAIN:
                    return self._apply_llm_fallback(
                        routed, current_screen, proposed_action, raw_instruction
                    )
                return routed

        return tier1_result

    def verify_by_state(
        self,
        current_state_id: str,
        proposed_action: dict[str, Any],
        intent_ctx: IntentContext | None = None,
        screen_ctx: ScreenContext | None = None,
        action_ctx: dict[str, Any] | None = None,
        goal_state: str | None = None,
        raw_instruction: str | None = None,
    ) -> VerificationOutput:
        """Verify when state is already known (skip localization).

        Args:
            current_state_id: The current FSM state ID.
            proposed_action: Action dict.
            intent_ctx: User intent variables for guard binding.
            screen_ctx: Screen context for guard evaluation.
            action_ctx: Action metadata for action_pred evaluation.
            goal_state: Optional goal state for reachability checking.
            raw_instruction: User's natural language instruction. If provided
                and intent_ctx is None, triggers auto-extraction via IntentExtractor.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        # Tier 1: structural check
        tier1_result = self._checker.verify_by_state(current_state_id, proposed_action, goal_state)

        if tier1_result.result == VerifyResult.UNCERTAIN:
            return self._apply_llm_fallback(tier1_result, None, proposed_action, raw_instruction)
        if tier1_result.result != VerifyResult.ALLOW:
            return tier1_result

        # Tier 2: DSL guard check
        if self._evaluator is None:
            return tier1_result

        transition = self._fsm.get_transition(current_state_id, proposed_action)
        intent_ctx = self._resolve_intent(intent_ctx, raw_instruction, current_state_id)

        if screen_ctx is None:
            screen_ctx = ScreenContext()

        if transition is not None and transition.guard is not None:
            guard_result = self._evaluator.evaluate(
                transition.guard,
                intent_ctx=intent_ctx,
                screen_ctx=screen_ctx,
                action_ctx=action_ctx,
            )
            routed = self._route_guard_result(guard_result, tier1_result)
            if routed is not None:
                if routed.result == VerifyResult.UNCERTAIN:
                    return self._apply_llm_fallback(routed, None, proposed_action, raw_instruction)
                return routed

        return tier1_result

    def post_arrival_check(
        self,
        target_state_id: str,
        observed_target_screen: RawScreen,
        intent: IntentContext | None = None,
    ) -> VerificationOutput:
        """Check target-state invariants after the target screen is observed.

        Pre-action verification cannot read successor-only UI elements. This
        method is the public post-arrival hook for enforcing state invariants
        once the caller supplies the observed target screen.
        """
        del intent  # State invariants currently read screen state only.
        if target_state_id not in self._fsm.states:
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.STATE_UNKNOWN,
                target_state_id=target_state_id,
                details=f"Target state {target_state_id} is not in the FSM",
            )

        screen_ctx = self._build_screen_context(observed_target_screen)
        invariant_result = self._check_invariants(
            target_state_id=target_state_id,
            screen_ctx=screen_ctx,
        )
        if invariant_result is not None:
            return invariant_result
        return VerificationOutput(
            result=VerifyResult.ALLOW,
            reason=VerifyReason.TRANSITION_VALID,
            target_state_id=target_state_id,
            details=f"All invariants passed for {target_state_id}",
        )

    def get_required_variables(self, state_id: str) -> set[str]:
        """Get $intent.* variable names needed by a state's outgoing guards.

        Convenience method that delegates to IntentExtractor.collect_required_variables.

        Args:
            state_id: FSM state ID.

        Returns:
            Set of variable names (without $intent. prefix).
        """
        return IntentExtractor.collect_required_variables(self._fsm, state_id)

    def _route_guard_result(
        self,
        guard_result: Any,
        tier1_result: VerificationOutput,
    ) -> VerificationOutput | None:
        """Map a three-valued GuardResult to a VerificationOutput.

        Returns None when the guard is TRUE (ALLOW preserved upstream),
        DENY when FALSE, UNCERTAIN when UNKNOWN.
        """
        if guard_result.status is GuardStatus.TRUE:
            return None
        if guard_result.status is GuardStatus.UNKNOWN:
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.GUARD_INCONCLUSIVE,
                current_state_id=tier1_result.current_state_id,
                target_state_id=tier1_result.target_state_id,
                confidence=tier1_result.confidence,
                details=(
                    f"Guard inconclusive: {guard_result.guard_expression}"
                    f" — {guard_result.failure_reason}"
                ),
            )
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

    def _check_invariants(
        self,
        target_state_id: str,
        screen_ctx: ScreenContext,
    ) -> VerificationOutput | None:
        """Enforce invariant map I on an observed state.

        Skipped when no invariant checker is configured. Returns None when all
        invariants hold (TRUE) or none exist, DENY on any FALSE,
        UNCERTAIN on any UNKNOWN (with no FALSE).
        """
        if self._invariant_checker is None:
            return None
        target_state = self._fsm.states.get(target_state_id)
        if target_state is None or not target_state.state_invariants:
            return None

        inv = self._invariant_checker.check_state(target_state_id, screen_ctx)
        if inv.failed > 0:
            first_expr, first_reason = inv.failed_invariants[0]
            return VerificationOutput(
                result=VerifyResult.DENY,
                reason=VerifyReason.INVARIANT_FAILED,
                target_state_id=target_state_id,
                details=f"Invariant failed on {target_state_id}: {first_expr} — {first_reason}",
            )
        if inv.unknown > 0:
            first_expr, first_reason = inv.unknown_invariants[0]
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.INVARIANT_INCONCLUSIVE,
                target_state_id=target_state_id,
                details=f"Invariant inconclusive on {target_state_id}:"
                f" {first_expr} — {first_reason}",
            )
        return None

    def _apply_llm_fallback(
        self,
        uncertain_result: VerificationOutput,
        current_screen: RawScreen | None,
        proposed_action: dict[str, Any],
        raw_instruction: str | None,
    ) -> VerificationOutput:
        """Route an UNCERTAIN result through the LLM fallback if configured.

        If no fallback is attached, returns the input unchanged — preserving
        the default "user" fallback semantics where callers handle UNCERTAIN
        themselves.
        """
        if self._llm_fallback is None:
            return uncertain_result
        return self._llm_fallback.resolve(
            uncertain_result,
            current_screen,
            proposed_action,
            raw_instruction=raw_instruction,
        )

    def _resolve_intent(
        self,
        intent_ctx: IntentContext | None,
        raw_instruction: str | None,
        state_id: str,
    ) -> IntentContext | None:
        """Resolve intent context: explicit > auto-extract > None.

        Args:
            intent_ctx: Explicitly provided intent (takes priority).
            raw_instruction: User instruction for auto-extraction.
            state_id: Current state ID for variable collection.

        Returns:
            Resolved IntentContext or None.
        """
        if intent_ctx is not None:
            return intent_ctx
        if raw_instruction is not None and self._intent_extractor is not None:
            return self._intent_extractor.extract(raw_instruction, state_id)
        return None

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
                "value": e.text or "",
                "is_clickable": e.is_clickable,
                "is_long_clickable": e.is_long_clickable,
                "is_checkable": e.is_checkable,
                "is_checked": e.is_checked,
                "is_enabled": e.is_enabled,
                "is_editable": e.is_editable,
                "is_scrollable": e.is_scrollable,
                "is_focusable": getattr(e, "is_focusable", False),
                "is_focused": getattr(e, "is_focused", False),
                "is_selected": getattr(e, "is_selected", False),
                "is_password": getattr(e, "is_password", False),
                "class_name": e.class_name or "",
                "resource_id": e.resource_id or "",
                "bounds": e.bounds,
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
