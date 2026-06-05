"""LLM fallback for uncertain verification results.

When the symbolic verifier returns UNCERTAIN (unknown state, fuzzy state
match, or low transition confidence), this module can optionally consult
an LLM to produce a final ALLOW/DENY with full context.

The fallback is opt-in and orthogonal to Tier 1-2: the symbolic layer runs
first and returns UNCERTAIN only when it genuinely cannot decide. The LLM
then sees:
  - The current screen's key UI elements
  - The proposed action (and its target, if any)
  - The uncertainty reason from the symbolic verifier
  - The candidate transitions from the current state (if any)

On LLM failure or unparseable response, the original UNCERTAIN result is
returned unchanged — degrading to the default "user" fallback semantics.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from vigil.core.llm_client import LlmClient
from vigil.models.fsm import AppFSM
from vigil.models.state import RawScreen
from vigil.symbolic.fsm_checker import (
    VerificationOutput,
    VerifyReason,
    VerifyResult,
)

_SYSTEM_PROMPT = """\
You are the safety fallback for a mobile GUI verifier. A symbolic verifier \
could not make a definitive ALLOW/DENY decision about a proposed action on \
a mobile screen, and is asking you to judge it.

You will receive:
  - A summary of the current screen (known state name if any, interactable elements)
  - The proposed action (type, target, text)
  - The original uncertainty reason from the symbolic verifier
  - Candidate transitions from the current state (if the state is known)

Return ONLY a JSON object of the form:
  {"decision": "ALLOW" | "DENY", "reason": "<explanation>"}

Guidance:
  - Prefer ALLOW for routine navigation (back, home, tapping menu items) \
on known screens.
  - Prefer DENY if the action looks destructive, risky, or clearly off-task.
  - If the screen is unknown and the action is non-reversible (pay, send, \
delete, confirm purchase), prefer DENY.
  - Do not explain outside the JSON. Do not use markdown or code fences."""

_RETRY_PROMPT_SUFFIX = (
    "\n\nYour previous response was not valid JSON. "
    'Return ONLY a raw JSON object of the form {"decision": "...", "reason": "..."}.'
)


class LlmFallback:
    """Convert UNCERTAIN verification results into ALLOW/DENY via LLM.

    Intended to be plugged into DecisionEngine. When DecisionEngine's
    Tier 1-2 symbolic pipeline returns UNCERTAIN, it calls `resolve()`
    which queries the LLM with structured context.

    Args:
        llm_client: Configured LLM client (must support .generate()).
        fsm: The app's FSM, used to include transition context in the prompt.
    """

    def __init__(self, llm_client: LlmClient, fsm: AppFSM) -> None:
        self._llm = llm_client
        self._fsm = fsm

    def resolve(
        self,
        uncertain_result: VerificationOutput,
        current_screen: RawScreen | None,
        proposed_action: dict[str, Any],
        raw_instruction: str | None = None,
    ) -> VerificationOutput:
        """Ask the LLM to decide ALLOW/DENY for an UNCERTAIN result.

        Args:
            uncertain_result: The symbolic verifier's UNCERTAIN output.
            current_screen: The device screen (may be None when caller has
                only a state_id).
            proposed_action: The action being considered.
            raw_instruction: User's natural-language instruction, if any.

        Returns:
            A new VerificationOutput with ALLOW/DENY if the LLM produced a
            parseable decision. Otherwise the input UNCERTAIN is returned
            unchanged.
        """
        if uncertain_result.result != VerifyResult.UNCERTAIN:
            return uncertain_result

        prompt = self._build_user_prompt(
            uncertain_result, current_screen, proposed_action, raw_instruction
        )

        try:
            response = self._llm.generate(_SYSTEM_PROMPT, prompt)
        except Exception as exc:
            logger.warning(f"LLM fallback failed: {exc} — preserving UNCERTAIN")
            return uncertain_result

        decision, reason = self._parse_decision(response)
        if decision is None:
            logger.debug("First LLM fallback response was not parseable, retrying")
            try:
                response = self._llm.generate(_SYSTEM_PROMPT, prompt + _RETRY_PROMPT_SUFFIX)
            except Exception as exc:
                logger.warning(f"LLM fallback retry failed: {exc} — preserving UNCERTAIN")
                return uncertain_result
            decision, reason = self._parse_decision(response)

        if decision is None:
            logger.warning(f"LLM fallback returned unparseable response: {response!r}")
            return uncertain_result

        original_reason = uncertain_result.reason.value
        details = (
            f"LLM fallback: {reason}" if reason else "LLM fallback decision"
        ) + f" (original uncertainty: {original_reason})"

        return VerificationOutput(
            result=decision,
            reason=VerifyReason.LLM_FALLBACK,
            current_state_id=uncertain_result.current_state_id,
            target_state_id=uncertain_result.target_state_id,
            confidence=uncertain_result.confidence,
            details=details,
        )

    def _build_user_prompt(
        self,
        uncertain_result: VerificationOutput,
        current_screen: RawScreen | None,
        proposed_action: dict[str, Any],
        raw_instruction: str | None,
    ) -> str:
        lines: list[str] = []

        if raw_instruction:
            lines.append(f'User instruction: "{raw_instruction}"')

        state_id = uncertain_result.current_state_id
        if state_id and state_id in self._fsm.states:
            s = self._fsm.states[state_id]
            activity_name = s.android_context.activity_name
            activity = f" @ {activity_name}" if activity_name else ""
            lines.append(f"Current state: {s.name} ({state_id}){activity}")
        else:
            lines.append("Current state: UNKNOWN (no FSM state match)")

        if current_screen is not None:
            elem_summaries = self._summarize_elements(current_screen)
            if elem_summaries:
                lines.append("Interactable elements on screen:")
                lines.extend(f"  - {s}" for s in elem_summaries)

        action_type = proposed_action.get("type", "?")
        target_id = proposed_action.get("target")
        target_text = self._resolve_target_text(target_id, current_screen)
        action_line = f"Proposed action: {action_type}"
        if target_id:
            action_line += f" on target={target_id}"
        if target_text:
            action_line += f' (text="{target_text}")'
        lines.append(action_line)

        lines.append(
            f"Symbolic uncertainty reason: {uncertain_result.reason.value} — "
            f"{uncertain_result.details or 'no detail'}"
        )

        if state_id:
            candidates = self._describe_candidate_transitions(state_id)
            if candidates:
                lines.append("Candidate transitions from this state:")
                lines.extend(f"  - {c}" for c in candidates)

        lines.append('Respond with JSON only: {"decision": "ALLOW" | "DENY", "reason": "..."}')
        return "\n".join(lines)

    @staticmethod
    def _summarize_elements(screen: RawScreen) -> list[str]:
        summaries: list[str] = []
        for e in screen.elements:
            is_interactable = e.is_clickable or e.is_scrollable or e.is_editable or e.is_checkable
            if not is_interactable:
                continue
            label = e.text or e.content_description or ""
            short_class = (e.class_name or "").rsplit(".", 1)[-1]
            tag = f"{short_class}[{e.element_id}]"
            if label:
                tag += f' "{label}"'
            if e.resource_id:
                tag += f" rid={e.resource_id}"
            if e.is_checkable:
                tag += f" checked={e.is_checked}"
            summaries.append(tag)
        return summaries

    @staticmethod
    def _resolve_target_text(target_id: str | None, screen: RawScreen | None) -> str | None:
        if target_id is None or screen is None:
            return None
        for e in screen.elements:
            if e.element_id == target_id:
                return e.text or e.content_description or None
        return None

    def _describe_candidate_transitions(self, state_id: str) -> list[str]:
        out: list[str] = []
        for t in self._fsm.transitions:
            if t.source != state_id:
                continue
            action_type = t.action.get("type", "?")
            target_name = (
                self._fsm.states[t.target].name if t.target in self._fsm.states else t.target
            )
            guard_part = f"  guard={t.guard}" if t.guard else ""
            out.append(f"{action_type} → {target_name}{guard_part}")
        return out

    @staticmethod
    def _parse_decision(response: str) -> tuple[VerifyResult | None, str]:
        text = response.strip()
        if text.startswith("```"):
            lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None, ""
        if not isinstance(parsed, dict):
            return None, ""
        decision_raw = str(parsed.get("decision", "")).strip().upper()
        reason = str(parsed.get("reason", "")).strip()
        if decision_raw == "ALLOW":
            return VerifyResult.ALLOW, reason
        if decision_raw == "DENY":
            return VerifyResult.DENY, reason
        return None, reason
