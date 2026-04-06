#!/usr/bin/env python3
"""Validate FSM correctness by replaying exploration traces.

Every action in the exploration trace actually happened on the device.
Therefore the FSM should ALLOW (or at least not DENY) all of them.
Any DENY indicates a bug in FSM construction (missing state or transition).

Usage:
    python scripts/validate_fsm.py <fsm.json> <trace.json>
    python scripts/validate_fsm.py models/bundles/settings/fsm.json \
        data/apps/settings/traces/exploration_20260403_132125.json

Exit codes:
    0 = all steps ALLOW (or UNCERTAIN for unmapped screens)
    1 = at least one DENY found (FSM construction bug)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vigil.models.action import Action
from vigil.models.fsm import AppFSM
from vigil.neuro.fsm_builder import FsmBuilder
from vigil.symbolic.fsm_checker import FsmChecker, VerifyResult

# ANSI codes
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate FSM against exploration traces.",
    )
    parser.add_argument("fsm", help="Path to FSM JSON")
    parser.add_argument("trace", help="Path to exploration trace JSON")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print every step result, not just failures",
    )
    args = parser.parse_args()

    # Load FSM and trace
    fsm = AppFSM.deserialize(args.fsm)
    trace_data = json.loads(Path(args.trace).read_text())

    # Rebuild screen_id → state_id mapping using same logic as FsmBuilder
    raw_screens = trace_data.get("screens", {})
    builder = FsmBuilder(fsm.app_package)
    fp_to_state_id, _states = builder._build_states(raw_screens)
    sid_to_state_id = builder._build_screen_mapping(raw_screens, fp_to_state_id)

    checker = FsmChecker(fsm)

    # Stats
    total = 0
    allow = 0
    deny = 0
    uncertain = 0
    unmapped = 0
    self_loop_skip = 0
    deny_details: list[str] = []

    traces = trace_data.get("traces", [])
    traces.sort(key=lambda t: t.get("step_number", 0))

    for trace in traces:
        source_sid = trace.get("source_screen_id", "")
        target_sid = trace.get("target_screen_id", "")
        source_state = sid_to_state_id.get(source_sid)
        target_state = sid_to_state_id.get(target_sid)

        if source_state is None:
            unmapped += 1
            continue

        # Build action dict (same as FsmBuilder._build_transitions)
        action_data = trace.get("action", {})
        action = Action(**action_data)
        action_dict = action.to_fsm_dict()

        # Skip self-loops (FSM builder excludes them by default)
        if source_state == target_state:
            self_loop_skip += 1
            continue

        result = checker.verify_by_state(source_state, action_dict)
        total += 1

        if result.result == VerifyResult.ALLOW:
            allow += 1
            if args.verbose:
                target_label = target_state or target_sid
                state = fsm.states.get(source_state)
                src_name = state.name if state else source_state
                print(
                    f"  {_GREEN}✓{_RESET} step {trace['step_number']:3d}: "
                    f"{src_name} ({source_state}) --{action_dict['type']}--> {target_label}"
                )
        elif result.result == VerifyResult.DENY:
            deny += 1
            state = fsm.states.get(source_state)
            src_name = state.name if state else source_state
            detail = (
                f"  {_RED}✗{_RESET} step {trace['step_number']:3d}: "
                f"{src_name} ({source_state}) --{action_dict['type']}--> "
                f"{target_state or target_sid} | {result.reason.value}"
            )
            deny_details.append(detail)
            print(detail)
        else:
            uncertain += 1
            if args.verbose:
                state = fsm.states.get(source_state)
                src_name = state.name if state else source_state
                print(
                    f"  {_YELLOW}?{_RESET} step {trace['step_number']:3d}: "
                    f"{src_name} ({source_state}) --{action_dict['type']}--> "
                    f"{target_state or target_sid} | {result.reason.value}"
                )

    # Summary
    print(f"\n{_BOLD}{'=' * 55}{_RESET}")
    print(f"{_BOLD}FSM Validation: {fsm.app_package}{_RESET}")
    print(f"  States in FSM:  {len(fsm.states)}")
    print(f"  Transitions:    {len(fsm.transitions)}")
    print(f"  Trace steps:    {len(traces)}")
    print(f"{'=' * 55}")
    print(f"  Tested:       {total}")
    print(f"  {_GREEN}ALLOW{_RESET}:        {allow}")
    print(f"  {_RED}DENY{_RESET}:         {deny}  {'← BUGS' if deny else ''}")
    print(f"  {_YELLOW}UNCERTAIN{_RESET}:    {uncertain}")
    print(f"  Unmapped:     {unmapped}")
    print(f"  Self-loops:   {self_loop_skip} (skipped)")
    print(f"{'=' * 55}")

    if deny:
        print(f"\n{_RED}⚠ {deny} DENY results found — FSM construction has bugs.{_RESET}")
        print("Common causes:")
        print("  - Fingerprint merging too aggressive (distinct screens merged)")
        print("  - Post-processing removed a valid state")
        print("  - Transition action_type mismatch between trace and FSM")
        sys.exit(1)
    else:
        coverage = allow / total * 100 if total else 0
        print(f"\n{_GREEN}✓ No DENY results. Coverage: {coverage:.0f}% ALLOW{_RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
