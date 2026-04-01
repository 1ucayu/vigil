"""Tier 1: FSM Structural Verification (< 5 ms).

Pure symbolic checks against the FSM graph:
- Transition validity: is the proposed action legal from the current state?
- Reachability: can we still reach the goal state? O(V+E)
- Invariant check: are any state invariants violated?
- Confidence check: is this transition well-tested?
"""
