"""Combined verification decision engine with tier routing.

Orchestrates the three-tier verification pipeline:
  Tier 1 (FSM structural) → Tier 2 (DSL semantic) → Tier 3 (micro-evolution)

Returns ALLOW / DENY / UNCERTAIN for each proposed action.
"""
