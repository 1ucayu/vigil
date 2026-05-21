"""CLI entry point: vigil-validate-fsm.

Replays every step of a trace against a built FSM and reports per-step
verdicts using validator-local reason codes. Mapping from FsmChecker output:

  - state_not_found          : source screen has no FSM state
  - action_signature_mismatch: outgoing edges share the action type but
                               canonical identity disagrees, OR FsmChecker
                               returned ACTION_AMBIGUOUS, OR FSM matched to a
                               different target than the trace observed
  - transition_not_in_fsm    : no outgoing edge of that action type at all
  - low_confidence           : matched transition's confidence < threshold
  - template_binding_missing : source state has a DYNAMIC SubFsmTemplate but
                               the action lacks the identity fields needed
                               to bind a specific item
  - selector_resolution_failed: trace metadata flagged the selector as
                               ambiguous or unresolvable

Reason codes are defined locally here; FsmChecker.VerifyReason is NOT
extended so the symbolic layer keeps its own narrower vocabulary.

Usage:
    python -m vigil.scripts.validate_fsm --fsm <fsm.json> --trace <trace.json>
    python -m vigil.scripts.validate_fsm --trace <trace.json>   # builds FSM on the fly
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from vigil.core.paths import redirect_docs_output_path
from vigil.models.action import Action
from vigil.models.fsm import AppFSM, ContainerType
from vigil.neuro.fsm_builder import FsmBuilder
from vigil.symbolic.fsm_checker import (
    FsmChecker,
    VerificationOutput,
    VerifyReason,
    VerifyResult,
)


class ValidationReason(StrEnum):
    """Validator-local reason codes (distinct from FsmChecker.VerifyReason)."""

    OK = "ok"
    STATE_NOT_FOUND = "state_not_found"
    ACTION_SIGNATURE_MISMATCH = "action_signature_mismatch"
    TRANSITION_NOT_IN_FSM = "transition_not_in_fsm"
    LOW_CONFIDENCE = "low_confidence"
    TEMPLATE_BINDING_MISSING = "template_binding_missing"
    SELECTOR_RESOLUTION_FAILED = "selector_resolution_failed"


@dataclass
class StepValidation:
    """Outcome for a single trace step."""

    step_index: int
    source_screen_id: str
    target_screen_id: str
    source_state_id: str | None
    expected_target_state_id: str | None
    matched_target_state_id: str | None
    action: dict[str, Any]
    verify_result: VerifyResult | None
    reason: ValidationReason
    detail: str = ""


@dataclass
class ValidationReport:
    """Aggregate report from validating a trace against an FSM."""

    app_package: str
    total_steps: int
    counts_by_reason: dict[str, int] = field(default_factory=dict)
    steps: list[StepValidation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_package": self.app_package,
            "total_steps": self.total_steps,
            "counts_by_reason": dict(self.counts_by_reason),
            "steps": [
                {
                    "step_index": s.step_index,
                    "source_screen_id": s.source_screen_id,
                    "target_screen_id": s.target_screen_id,
                    "source_state_id": s.source_state_id,
                    "expected_target_state_id": s.expected_target_state_id,
                    "matched_target_state_id": s.matched_target_state_id,
                    "action": s.action,
                    "verify_result": s.verify_result.value if s.verify_result else None,
                    "reason": s.reason.value,
                    "detail": s.detail,
                }
                for s in self.steps
            ],
        }


_SENTINELS = frozenset({"COLD_START_FAILED", "ACTION_FAILED", "LEFT_APP"})


def _build_screen_to_state(fsm: AppFSM) -> dict[str, str]:
    """Invert AbstractState.raw_screens to get a screen-id → state-id map.

    This is the authoritative mapping the builder produced — FSM construction
    is the only source of truth for which raw screens collapsed to which
    abstract state, so we recover it from the bundle instead of recomputing
    fingerprints (which would risk drifting from the builder).
    """
    mapping: dict[str, str] = {}
    for state in fsm.states.values():
        for screen_id in state.raw_screens:
            mapping[screen_id] = state.state_id
    return mapping


def _classify_deny(fsm: AppFSM, source_state_id: str, action: dict[str, Any]) -> ValidationReason:
    """Distinguish 'no edge of that action type' from 'edges exist but the
    action's canonical identity disagrees with all of them'."""
    proposed_type = action.get("type") or action.get("action_type")
    same_type_outgoing = [
        t
        for t in fsm.transitions
        if t.source == source_state_id
        and (t.action.get("type") or t.action.get("action_type")) == proposed_type
    ]
    if not same_type_outgoing:
        return ValidationReason.TRANSITION_NOT_IN_FSM
    return ValidationReason.ACTION_SIGNATURE_MISMATCH


def _template_binding_missing(fsm: AppFSM, source_state_id: str, action: dict[str, Any]) -> bool:
    """A dynamic container with a SubFsmTemplate needs at least one identity
    field on the click action (target_text / target_resource_id / target_selector)
    to bind a specific item. Bare ``{'type': 'click'}`` is a template-binding gap.

    Additionally requires that the source has at least one outgoing click edge
    whose target lies inside the template subgraph. Otherwise the click is
    not a template-binding attempt (it is chrome — e.g. a toolbar Navigate up
    or a switch) and must not be classified as ``template_binding_missing``.
    """
    state = fsm.states.get(source_state_id)
    if (
        state is None
        or state.container_type != ContainerType.DYNAMIC
        or not state.sub_fsm_template_id
    ):
        return False
    if (action.get("type") or action.get("action_type")) != "click":
        return False
    template = fsm.sub_fsm_templates.get(state.sub_fsm_template_id)
    if template is None:
        return False
    template_state_ids = set(template.states)
    has_template_entry_edge = any(
        t.source == source_state_id
        and t.target in template_state_ids
        and (t.action.get("type") or t.action.get("action_type")) == "click"
        for t in fsm.transitions
    )
    if not has_template_entry_edge:
        return False
    identity_fields = (
        "target",
        "target_text",
        "target_resource_id",
        "target_content_desc",
        "target_class",
        "target_selector",
        "text",
        "value",
    )
    return not any(action.get(f) for f in identity_fields)


def _selector_resolution_failed(trace_step: dict[str, Any]) -> bool:
    metadata = trace_step.get("metadata") or {}
    resolution = metadata.get("selector_resolution")
    return resolution in {"ambiguous", "failed"}


def _map_fsmchecker_reason(
    fsm: AppFSM,
    source_state_id: str,
    action: dict[str, Any],
    output: VerificationOutput,
) -> ValidationReason:
    """Translate a FsmChecker verdict into the validator's vocabulary."""
    # resolve_transition's template fallback emits this detail string when the
    # action cannot be bound to a concrete template edge — respect it directly.
    if "template_binding_missing" in (output.details or ""):
        return ValidationReason.TEMPLATE_BINDING_MISSING
    if output.reason is VerifyReason.STATE_UNKNOWN:
        return ValidationReason.STATE_NOT_FOUND
    if output.reason is VerifyReason.ACTION_AMBIGUOUS:
        if _template_binding_missing(fsm, source_state_id, action):
            return ValidationReason.TEMPLATE_BINDING_MISSING
        return ValidationReason.ACTION_SIGNATURE_MISMATCH
    if output.reason is VerifyReason.LOW_CONFIDENCE:
        return ValidationReason.LOW_CONFIDENCE
    if output.reason is VerifyReason.TRANSITION_INVALID:
        if _template_binding_missing(fsm, source_state_id, action):
            return ValidationReason.TEMPLATE_BINDING_MISSING
        return _classify_deny(fsm, source_state_id, action)
    # STATE_SIMILAR / GUARD_* / INVARIANT_* are not reachable here because
    # we drive verification from a known state id; fall through defensively.
    return ValidationReason.ACTION_SIGNATURE_MISMATCH


def validate_fsm(
    fsm: AppFSM,
    trace_path: Path,
    confidence_threshold: float | None = None,
) -> ValidationReport:
    """Replay every step of ``trace_path`` against ``fsm`` and return a report.

    The validator does NOT rebuild the FSM. It uses the FSM's own
    ``raw_screens`` lists to map trace screens to abstract states, so the
    verdicts reflect exactly the canonical action / state identity the
    builder used.
    """
    trace_data = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    raw_traces = [
        t for t in trace_data.get("traces", []) if t.get("target_state_id") not in _SENTINELS
    ]
    screen_to_state = _build_screen_to_state(fsm)

    from vigil.core.config import VerificationConfig

    config = VerificationConfig(
        confidence_threshold=confidence_threshold if confidence_threshold is not None else 0.7
    )
    checker = FsmChecker(fsm, config=config)

    steps: list[StepValidation] = []
    counts: Counter[str] = Counter()

    for idx, trace in enumerate(raw_traces):
        source_sid = trace.get("source_screen_id", "")
        target_sid = trace.get("target_screen_id", "")
        action_data = trace.get("action", {}) or {}

        try:
            action = Action(**action_data)
            action_dict = action.to_fsm_dict()
        except Exception:  # pragma: no cover - defensive; bad action shape
            action_dict = dict(action_data)

        source_state_id = screen_to_state.get(source_sid)
        expected_target_state_id = screen_to_state.get(target_sid)

        if source_state_id is None:
            step = StepValidation(
                step_index=trace.get("step_number", idx),
                source_screen_id=source_sid,
                target_screen_id=target_sid,
                source_state_id=None,
                expected_target_state_id=expected_target_state_id,
                matched_target_state_id=None,
                action=action_dict,
                verify_result=None,
                reason=ValidationReason.STATE_NOT_FOUND,
                detail=f"Source screen {source_sid!r} is not bound to any FSM state",
            )
            steps.append(step)
            counts[step.reason.value] += 1
            continue

        if _selector_resolution_failed(trace):
            step = StepValidation(
                step_index=trace.get("step_number", idx),
                source_screen_id=source_sid,
                target_screen_id=target_sid,
                source_state_id=source_state_id,
                expected_target_state_id=expected_target_state_id,
                matched_target_state_id=None,
                action=action_dict,
                verify_result=None,
                reason=ValidationReason.SELECTOR_RESOLUTION_FAILED,
                detail="Trace metadata flagged the selector as ambiguous/failed",
            )
            steps.append(step)
            counts[step.reason.value] += 1
            continue

        output = checker.verify_by_state(source_state_id, action_dict)
        matched_target = output.target_state_id
        target_screen_unmapped = (
            bool(target_sid) and target_sid not in _SENTINELS and expected_target_state_id is None
        )

        if output.result is VerifyResult.ALLOW:
            # An ALLOW with no matched target means resolve_transition fell
            # through a degenerate template path (no concrete edge bound).
            # The validator must not report ok in that case.
            if matched_target is None:
                source_state = fsm.states.get(source_state_id)
                if (
                    source_state is not None
                    and source_state.container_type == ContainerType.DYNAMIC
                    and source_state.sub_fsm_template_id
                ):
                    reason = ValidationReason.TEMPLATE_BINDING_MISSING
                    detail = (
                        "FsmChecker returned ALLOW but no concrete template edge "
                        f"bound the action on dynamic container {source_state_id}"
                    )
                else:
                    reason = ValidationReason.ACTION_SIGNATURE_MISMATCH
                    detail = (
                        "FsmChecker returned ALLOW but matched_target_state_id is None; "
                        "action identity does not uniquely select an outgoing edge"
                    )
            # Sanity: if the FSM matched a transition to a different target
            # than the trace observed, the action's canonical identity does
            # not uniquely determine the actually-taken edge.
            elif target_screen_unmapped:
                reason = ValidationReason.ACTION_SIGNATURE_MISMATCH
                detail = f"Trace target screen {target_sid!r} is not bound to any FSM state"
            elif (
                expected_target_state_id is not None and matched_target != expected_target_state_id
            ):
                reason = ValidationReason.ACTION_SIGNATURE_MISMATCH
                detail = (
                    f"FSM matched action to {matched_target} but trace observed "
                    f"{expected_target_state_id}"
                )
            else:
                reason = ValidationReason.OK
                detail = output.details
        else:
            reason = _map_fsmchecker_reason(fsm, source_state_id, action_dict, output)
            detail = output.details

        steps.append(
            StepValidation(
                step_index=trace.get("step_number", idx),
                source_screen_id=source_sid,
                target_screen_id=target_sid,
                source_state_id=source_state_id,
                expected_target_state_id=expected_target_state_id,
                matched_target_state_id=matched_target,
                action=action_dict,
                verify_result=output.result,
                reason=reason,
                detail=detail,
            )
        )
        counts[reason.value] += 1

    return ValidationReport(
        app_package=fsm.app_package,
        total_steps=len(raw_traces),
        counts_by_reason=dict(counts),
        steps=steps,
    )


def validate_trace_against_built_fsm(
    trace_path: Path,
    app_package: str,
    confidence_threshold: float | None = None,
) -> ValidationReport:
    """Build the FSM from ``trace_path`` and immediately validate the same trace.

    Convenience helper for round-trip integrity checks (every observed step
    should map to OK when the FSM is fresh from the same trace).
    """
    builder = FsmBuilder(app_package)
    fsm = builder.build_from_trace(trace_path)
    return validate_fsm(fsm, trace_path, confidence_threshold=confidence_threshold)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vigil-validate-fsm",
        description="Replay a trace against a built FSM and report per-step verdicts.",
    )
    parser.add_argument("--fsm", type=Path, help="Path to a serialized FSM bundle (JSON).")
    parser.add_argument(
        "--trace", type=Path, required=True, help="Path to the exploration trace JSON."
    )
    parser.add_argument(
        "--app",
        type=str,
        default="com.unknown",
        help="App package (used only when --fsm is not provided and the FSM is built on the fly).",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=None,
        help="Override the FsmChecker confidence threshold (default 0.7).",
    )
    parser.add_argument("--output", type=Path, help="Optional path to write the JSON report.")
    args = parser.parse_args(argv)

    if args.fsm is not None:
        fsm = AppFSM.deserialize(args.fsm)
        report = validate_fsm(fsm, args.trace, confidence_threshold=args.confidence_threshold)
    else:
        report = validate_trace_against_built_fsm(
            args.trace, args.app, confidence_threshold=args.confidence_threshold
        )

    payload = report.to_dict()
    if args.output:
        output_path = redirect_docs_output_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2))
    else:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")

    has_problem = any(reason != ValidationReason.OK.value for reason in report.counts_by_reason)
    return 1 if has_problem else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
