"""Tier 3: Online Micro-Evolution engine.

Handles previously unseen UI states at runtime. Uses structural similarity matching
to inherit guards from similar known states (inherit_and_bind). Results are cached
back into the FSM bundle for monotonically increasing coverage.

No LLM calls — inherit_and_bind is purely structural.
"""

from __future__ import annotations

from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from loguru import logger
from pydantic import BaseModel

from vigil.models.fsm import AbstractState, AppFSM, Transition
from vigil.models.state import RawScreen


class EvolutionResult(BaseModel):
    """Result of a micro-evolution attempt.

    Attributes:
        evolved: Whether a new state was added to FSM.
        state_id: The new or inherited state_id.
        method: "inherit_and_bind" or "none".
        similarity_score: Best similarity score found.
        inherited_from: State_id of the similar state used as template.
    """

    evolved: bool
    state_id: str | None = None
    method: str = ""
    similarity_score: float = 0.0
    inherited_from: str | None = None


class FsmEvolver:
    """Tier 3: Online micro-evolution for unseen UI states.

    When StateLocator returns UNKNOWN:
    1. Compare screen fingerprint against all known states using
       structural similarity (SequenceMatcher on hex fingerprint strings)
    2. If similarity > threshold -> inherit_and_bind:
       - Create new AbstractState copying the similar state's properties
       - Copy all outgoing transitions (with guards and confidence)
       - Add new state + transitions to FSM
       - Return evolved=True
    3. If no similar state found -> Return evolved=False
    4. New states are cached in the FSM — next time this screen appears,
       StateLocator will find it via exact match (Tier 1)

    This creates a learning loop: FSM coverage monotonically increases.

    Args:
        fsm: The app's FSM to evolve.
        similarity_threshold: Minimum similarity for inherit_and_bind (default 0.80).
    """

    def __init__(
        self,
        fsm: AppFSM,
        similarity_threshold: float = 0.80,
    ) -> None:
        self._fsm = fsm
        self._threshold = similarity_threshold
        self._evolution_count = 0

    def try_evolution(self, screen: RawScreen) -> EvolutionResult:
        """Attempt to evolve the FSM for an unseen screen.

        Computes structural similarity between the screen's fingerprint and
        all known states. If a match above threshold is found, creates a new
        state by inheriting from the similar state.

        Args:
            screen: The unseen screen to evolve for.

        Returns:
            EvolutionResult indicating whether evolution occurred.
        """
        screen_fp = screen.get_structural_fingerprint()

        # Find best similar state above threshold
        best_state_id: str | None = None
        best_score = 0.0

        for state in self._fsm.states.values():
            score = self._compute_similarity(screen_fp, state.fingerprint)
            if score > best_score:
                best_score = score
                best_state_id = state.state_id

        if best_state_id is None or best_score < self._threshold:
            logger.debug(
                f"No similar state found for fp={screen_fp[:8]}... (best={best_score:.2f})"
            )
            return EvolutionResult(evolved=False, method="none", similarity_score=best_score)

        # inherit_and_bind
        similar_state = self._fsm.states[best_state_id]
        self._evolution_count += 1
        new_state_id = f"s_evo_{self._evolution_count:03d}"

        new_state = AbstractState(
            state_id=new_state_id,
            name=f"{similar_state.name} (evolved)",
            fingerprint=screen_fp,
            hierarchy_level=similar_state.hierarchy_level,
            parent_state=similar_state.parent_state,
            activity_name=similar_state.activity_name,
            container_type=similar_state.container_type,
            container_resource_id=similar_state.container_resource_id,
            item_skeleton_hash=similar_state.item_skeleton_hash,
        )
        self._fsm.add_state(new_state)

        # Copy outgoing transitions from similar state
        for t in self._fsm.transitions:
            if t.source == best_state_id:
                new_t = Transition(
                    source=new_state_id,
                    target=t.target,
                    action=t.action.copy(),
                    guard=t.guard,
                    confidence=t.confidence,
                    observed_count=0,
                )
                self._fsm.add_transition(new_t)

        # Copy incoming navigate_back transitions
        for t in self._fsm.transitions:
            if t.target == best_state_id and t.action.get("type") == "navigate_back":
                new_t = Transition(
                    source=t.source,
                    target=new_state_id,
                    action=t.action.copy(),
                    guard=t.guard,
                    confidence=t.confidence,
                    observed_count=0,
                )
                self._fsm.add_transition(new_t)

        # Log evolution event
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "new_state_id": new_state_id,
            "inherited_from": best_state_id,
            "similarity_score": round(best_score, 4),
            "screen_fingerprint": screen_fp,
            "method": "inherit_and_bind",
        }
        self._fsm.evolution_log.append(log_entry)
        logger.info(f"Evolved: {new_state_id} from {best_state_id} (similarity={best_score:.2f})")

        return EvolutionResult(
            evolved=True,
            state_id=new_state_id,
            method="inherit_and_bind",
            similarity_score=best_score,
            inherited_from=best_state_id,
        )

    @staticmethod
    def _compute_similarity(fp1: str, fp2: str) -> float:
        """Compute structural similarity between two fingerprints.

        Uses SequenceMatcher ratio on the hex strings as a prototype.
        Returns a score in [0.0, 1.0].

        For production: compare pre-hash component tuples using Jaccard
        similarity on the set of (class, resource_id, depth) tuples.
        """
        return SequenceMatcher(None, fp1, fp2).ratio()

    def get_evolution_log(self) -> list[dict[str, Any]]:
        """Return the FSM's evolution log entries."""
        return self._fsm.evolution_log

    def cache_to_disk(self, path: str) -> None:
        """Re-serialize the FSM with any evolved states included."""
        self._fsm.serialize(path)
