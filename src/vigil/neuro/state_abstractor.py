"""Stage 2: State Abstraction.

Uses rule-based structural fingerprinting (in RawScreen.get_structural_fingerprint
and FsmBuilder._build_states) to map raw screens to abstract states.

Container classification (static/dynamic) is NOT done here — it is derived from
invariant mining in Stage 2.5 (SemanticGrounder.mine_invariants), which produces
formally verified classifications instead of heuristic rules.
"""

from __future__ import annotations


class StateAbstractor:
    """Placeholder for future LLM-assisted state abstraction.

    Currently, state abstraction is handled by structural fingerprinting in
    RawScreen.get_structural_fingerprint() and FsmBuilder._build_states().
    Container classification is deferred to Stage 2.5 (SemanticGrounder).
    """
