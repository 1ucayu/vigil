"""Tier 3: Online Micro-Evolution engine.

Handles previously unseen UI states at runtime. Uses structural similarity matching
to inherit guards from similar known states (inherit_and_bind). Results are cached
back into the FSM bundle for monotonically increasing coverage.

No LLM calls — inherit_and_bind is purely structural.

Inherited transitions are intentionally low-trust: their replay confidence is
capped at ``INHERITED_TRANSITION_CONFIDENCE`` (< default verification threshold
0.7), so they route to UNCERTAIN until replay validation promotes them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel

from vigil.models.fsm import AbstractState, AppFSM, Transition
from vigil.models.state import RawScreen

# Cap on confidence for inherited/evolved transitions. Kept below
# VerificationConfig.confidence_threshold (default 0.7) so that, until replay
# verification updates the value upward, every inherited edge routes to
# UNCERTAIN via FsmChecker's confidence check.
INHERITED_TRANSITION_CONFIDENCE: float = 0.5


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
       structural similarity (Jaccard on component tuples)
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
        raw_screens: dict[str, RawScreen] | None = None,
        similarity_threshold: float = 0.80,
        inherited_confidence: float | None = None,
    ) -> None:
        self._fsm = fsm
        self._raw_screens = raw_screens or {}
        self._threshold = similarity_threshold
        self._inherited_confidence = (
            inherited_confidence
            if inherited_confidence is not None
            else INHERITED_TRANSITION_CONFIDENCE
        )
        self._evolution_count = 0
        # Precompute per-state component sets from raw_screens. States whose
        # raw_screens are not available score 0.0 at match time (logged+skipped).
        self._state_components: dict[str, set[tuple[str, str, int]]] = {}
        for sid, state in fsm.states.items():
            comps: set[tuple[str, str, int]] = set()
            for rsid in state.raw_screens:
                rs = self._raw_screens.get(rsid)
                if rs is None:
                    continue
                comps.update(self._extract_components(rs))
            if comps:
                self._state_components[sid] = comps

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
        screen_components = self._extract_components(screen)

        best_state_id: str | None = None
        best_score = 0.0

        for state in self._fsm.states.values():
            if state.structural_fingerprint and state.structural_fingerprint == screen_fp:
                best_state_id = state.state_id
                best_score = 1.0
                break
            score = self._compute_similarity_jaccard(screen_components, state.state_id)
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
                    confidence=min(t.confidence, self._inherited_confidence),
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
                    confidence=min(t.confidence, self._inherited_confidence),
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
            "inherited_confidence_cap": self._inherited_confidence,
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
    def _extract_components(screen: RawScreen) -> set[tuple[str, str, int]]:
        """Extract structural component set from a RawScreen."""
        components: set[tuple[str, str, int]] = set()
        for e in screen.elements:
            components.add((e.class_name, e.resource_id or "", e.depth))
        return components

    def _compute_similarity_jaccard(
        self,
        screen_components: set[tuple[str, str, int]],
        state_id: str,
    ) -> float:
        """Jaccard similarity between screen components and a known state's
        cached component set.

        Returns 0.0 if the state has no cached components (e.g., raw_screens
        weren't provided at evolver construction).
        """
        state_comps = self._state_components.get(state_id)
        if not state_comps or not screen_components:
            return 0.0
        intersection = len(screen_components & state_comps)
        union = len(screen_components | state_comps)
        return intersection / union if union > 0 else 0.0

    def get_evolution_log(self) -> list[dict[str, Any]]:
        """Return the FSM's evolution log entries."""
        return self._fsm.evolution_log

    def cache_to_disk(self, path: str) -> None:
        """Re-serialize the FSM with any evolved states included."""
        self._fsm.serialize(path)
