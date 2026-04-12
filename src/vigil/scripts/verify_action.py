"""CLI demo: vigil-verify-action.

Demonstrates Vigil's end-to-end runtime verification.

Usage:
    # Verify a single action by state ID:
    vigil-verify-action --fsm models/bundles/settings/fsm.json \\
        --state s_001 --action '{"type": "click"}' --goal s_003

    # Verify with intent context:
    vigil-verify-action --fsm models/bundles/settings/fsm.json \\
        --state s_005 --action '{"type": "click", "target": "e_0042"}' \\
        --intent '{"wifi_name": "HKU_WiFi"}'

    # Verify a full trajectory:
    vigil-verify-action --fsm models/bundles/settings/fsm.json \\
        --state s_001 \\
        --trajectory '[{"type":"click"},{"type":"click"},{"type":"navigate_back"}]'

    # List all states and transitions (inspect mode):
    vigil-verify-action --fsm models/bundles/settings/fsm.json --inspect
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

# ANSI color codes
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_DIM = "\033[2m"


def _resolve_goal(fsm, goal_str: str) -> str:
    """Resolve goal by state name first, fall back to state_id."""
    for state in fsm.states.values():
        if state.name == goal_str:
            return state.state_id
    return goal_str


def _state_label(fsm, state_id: str | None) -> str:
    """Format a state as 'StateName (state_id)'."""
    if state_id is None:
        return "unknown"
    state = fsm.states.get(state_id)
    if state:
        return f"{state.name} ({state_id})"
    return state_id


def _print_result(fsm, result) -> None:
    """Print a single verification result with ANSI colors."""
    if result.result.value == "allow":
        tag = f"{_GREEN}✓ ALLOW{_RESET}"
    elif result.result.value == "deny":
        tag = f"{_RED}✗ DENY{_RESET}"
    else:
        tag = f"{_YELLOW}? UNCERTAIN{_RESET}"

    print(f"\n  {tag} — {result.reason.value}")
    if result.current_state_id:
        print(f"    Current: {_state_label(fsm, result.current_state_id)}")
    if result.target_state_id:
        print(f"    Target:  {_state_label(fsm, result.target_state_id)}")
    if result.confidence > 0:
        print(f"    Confidence: {result.confidence:.2f}")
    if result.details:
        print(f"    Details: {_DIM}{result.details}{_RESET}")


def _cmd_inspect(fsm) -> None:
    """Print all FSM states and transitions."""
    print(f"\n{_BOLD}FSM: {fsm.app_package}{_RESET}")
    print(f"  Version: {fsm.version}")
    print(f"  States: {len(fsm.states)}")
    print(f"  Transitions: {len(fsm.transitions)}")
    if fsm.initial_state:
        print(f"  Initial: {_state_label(fsm, fsm.initial_state)}")

    print(f"\n{_BOLD}States:{_RESET}")
    for s in fsm.states.values():
        activity = f" @ {s.activity_name}" if s.activity_name else ""
        print(f"  {_CYAN}{s.state_id}{_RESET}  {s.name}{activity}")
        if s.invariants:
            for inv in s.invariants:
                print(f"    invariant: {inv}")

    print(f"\n{_BOLD}Transitions:{_RESET}")
    for t in fsm.transitions:
        action_type = t.action.get("type", "?")
        guard_str = f"  guard={t.guard}" if t.guard else ""
        conf_str = f"  conf={t.confidence:.2f}" if t.confidence > 0 else ""
        print(f"  {t.source} → {t.target}  [{action_type}]{guard_str}{conf_str}")

    if fsm.evolution_log:
        print(f"\n{_BOLD}Evolution Log ({len(fsm.evolution_log)} entries):{_RESET}")
        for entry in fsm.evolution_log:
            print(
                f"  {entry.get('new_state_id')} ← {entry.get('inherited_from')}  "
                f"sim={entry.get('similarity_score', 0):.2f}  "
                f"{entry.get('timestamp', '')}"
            )


def _cmd_trajectory(fsm, args) -> None:
    """Verify a full trajectory."""
    from vigil.symbolic.trajectory_verifier import TrajectoryStep, TrajectoryVerifier

    actions_raw = json.loads(args.trajectory)
    steps = [TrajectoryStep(action=a) for a in actions_raw]
    goal = _resolve_goal(fsm, args.goal) if args.goal else None

    config = None
    if args.confidence is not None:
        from vigil.core.config import VerificationConfig

        config = VerificationConfig(confidence_threshold=args.confidence)
    verifier = TrajectoryVerifier(fsm, config=config)
    result = verifier.verify_trajectory(args.state, steps, goal_state=goal)

    print(f"\n{_BOLD}Trajectory Verification ({result.total_steps} steps){_RESET}")
    for i, step_result in enumerate(result.step_results):
        action_type = steps[i].action.get("type", "?")
        if step_result.result.value == "allow":
            tag = f"{_GREEN}✓{_RESET}"
        elif step_result.result.value == "deny":
            tag = f"{_RED}✗{_RESET}"
        else:
            tag = f"{_YELLOW}?{_RESET}"
        target = _state_label(fsm, step_result.target_state_id)
        print(f"  Step {i}: {tag} [{action_type}] → {target}")

    if result.overall_result.value == "allow":
        overall_tag = f"{_GREEN}✓ ALLOW{_RESET}"
    elif result.overall_result.value == "deny":
        overall_tag = f"{_RED}✗ DENY{_RESET}"
    else:
        overall_tag = f"{_YELLOW}? UNCERTAIN{_RESET}"
    print(f"\n  Overall: {overall_tag}")
    print(f"  Furthest valid step: {result.furthest_valid_step}")


def _build_llm_fallback(fsm, args):
    """Build an LlmFallback from the default config, or None on failure."""
    if not args.llm_fallback:
        return None
    from vigil.core.config import VigilConfig
    from vigil.core.llm_client import LlmClient
    from vigil.symbolic.llm_fallback import LlmFallback

    config_path = Path(args.config) if args.config else Path("configs/default.yaml")
    if not config_path.exists():
        print(
            f"{_YELLOW}Warning: {config_path} not found, using default LLM config{_RESET}",
            file=sys.stderr,
        )
        vigil_config = VigilConfig()
    else:
        vigil_config = VigilConfig.from_yaml(config_path)
    try:
        llm = LlmClient(vigil_config.llm)
    except Exception as exc:  # missing API key, proxy not running, etc.
        print(
            f"{_RED}Error: could not init LlmClient for fallback: {exc}{_RESET}",
            file=sys.stderr,
        )
        sys.exit(1)
    return LlmFallback(llm, fsm)


def _cmd_verify(fsm, args) -> None:
    """Verify a single action."""
    from vigil.symbolic.decision_engine import DecisionEngine
    from vigil.symbolic.dsl_evaluator import IntentContext

    action = json.loads(args.action)
    goal = _resolve_goal(fsm, args.goal) if args.goal else None
    intent_ctx = None
    if args.intent:
        intent_vars = json.loads(args.intent)
        intent_ctx = IntentContext(variables=intent_vars)

    config = None
    if args.confidence is not None:
        from vigil.core.config import VerificationConfig

        config = VerificationConfig(confidence_threshold=args.confidence)
    llm_fallback = _build_llm_fallback(fsm, args)
    engine = DecisionEngine(fsm, config=config, llm_fallback=llm_fallback)

    if args.state:
        result = engine.verify_by_state(args.state, action, intent_ctx=intent_ctx, goal_state=goal)
    elif args.screen:
        from vigil.core.ui_parser import parse_hierarchy_xml
        from vigil.models.state import RawScreen

        xml_path = Path(args.screen)
        if not xml_path.exists():
            print(f"{_RED}Error: screen XML not found: {xml_path}{_RESET}", file=sys.stderr)
            sys.exit(1)
        xml_text = xml_path.read_text(encoding="utf-8")
        elements = parse_hierarchy_xml(xml_text)
        screen = RawScreen(screen_id=xml_path.stem, elements=elements)
        result = engine.verify(screen, action, intent_ctx=intent_ctx, goal_state=goal)
    else:
        print(f"{_RED}Error: --state or --screen required{_RESET}", file=sys.stderr)
        sys.exit(1)

    _print_result(fsm, result)


def main() -> None:
    """Vigil runtime verification CLI."""
    parser = argparse.ArgumentParser(
        prog="vigil-verify-action",
        description="Verify a proposed action against a Vigil FSM.",
    )
    parser.add_argument("--fsm", required=True, help="Path to FSM JSON")

    # Input mode
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--state", help="Current FSM state ID")
    input_group.add_argument("--screen", help="Path to screen XML for auto-localization")
    input_group.add_argument("--inspect", action="store_true", help="List all states/transitions")

    # Action specification
    parser.add_argument("--action", help='Action JSON (e.g., \'{"type": "click"}\')')
    parser.add_argument("--trajectory", help="Trajectory JSON array of actions")

    # Optional context
    parser.add_argument("--goal", help="Goal state ID or name")
    parser.add_argument("--intent", help='Intent variables JSON (e.g., \'{"wifi_name": "HKU"}\')')
    parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="Override confidence threshold (default: 0.7). Use 0 to skip confidence checks.",
    )
    parser.add_argument(
        "--llm-fallback",
        action="store_true",
        help="On UNCERTAIN, consult the LLM for a final ALLOW/DENY call.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to VigilConfig YAML (for --llm-fallback). Default: configs/default.yaml",
    )

    args = parser.parse_args()

    # Configure logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    # Load FSM
    from vigil.models.fsm import AppFSM

    fsm_path = Path(args.fsm)
    if not fsm_path.exists():
        print(f"{_RED}Error: FSM file not found: {fsm_path}{_RESET}", file=sys.stderr)
        sys.exit(1)
    fsm = AppFSM.deserialize(fsm_path)

    # Dispatch
    if args.inspect:
        _cmd_inspect(fsm)
    elif args.trajectory:
        if not args.state:
            print(f"{_RED}Error: --state required for trajectory mode{_RESET}", file=sys.stderr)
            sys.exit(1)
        _cmd_trajectory(fsm, args)
    elif args.action:
        _cmd_verify(fsm, args)
    else:
        msg = f"{_RED}Error: --action, --trajectory, or --inspect required{_RESET}"
        print(msg, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
