"""Screen-to-FSM state mapping via accessibility tree fingerprinting.

Maps the current device screen to a known FSM state using structural fingerprinting.
Falls back to similarity-based matching for Tier 3 evolution when exact match fails.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from vigil.models.fsm import AppFSM
from vigil.models.state import RawScreen


class LocateResult(StrEnum):
    """Outcome of a state localization attempt."""

    EXACT = "exact"
    SIMILAR = "similar"
    UNKNOWN = "unknown"


class StateLocation(BaseModel):
    """Result of mapping a screen to an FSM state.

    Attributes:
        result: Whether the match was exact, similar, or unknown.
        state_id: The matched FSM state ID (None if unknown).
        confidence: Match confidence (1.0 for exact, 0.7 for similar, 0.0 for unknown).
        matched_fingerprint: The fingerprint that was matched against.
    """

    result: LocateResult
    state_id: str | None = None
    confidence: float = 0.0
    matched_fingerprint: str | None = None


class StateLocator:
    """Maps a runtime screen to a known FSM state via fingerprint matching.

    Pre-builds a fingerprint index for O(1) exact lookup. Falls back to
    AppFSM.find_similar_state() for fuzzy matching.

    Args:
        fsm: The app's FSM to localize against.
    """

    _SIMILAR_CONFIDENCE = 0.7

    def __init__(self, fsm: AppFSM) -> None:
        self._fsm = fsm
        self._fp_index: dict[str, str] = {}
        for state in fsm.states.values():
            self._fp_index[state.fingerprint] = state.state_id

    def locate(self, screen: RawScreen) -> StateLocation:
        """Locate a live screen in the FSM.

        Args:
            screen: The current screen with parsed UI elements.

        Returns:
            StateLocation with match result, state_id, and confidence.
        """
        fp = screen.get_structural_fingerprint()
        return self.locate_by_fingerprint(fp)

    def locate_by_fingerprint(self, fingerprint: str) -> StateLocation:
        """Locate by pre-computed fingerprint (skip fingerprint computation).

        Args:
            fingerprint: A structural fingerprint string.

        Returns:
            StateLocation with match result, state_id, and confidence.
        """
        # Exact match via index — O(1)
        state_id = self._fp_index.get(fingerprint)
        if state_id is not None:
            return StateLocation(
                result=LocateResult.EXACT,
                state_id=state_id,
                confidence=1.0,
                matched_fingerprint=fingerprint,
            )

        # Fuzzy match via FSM similarity search
        similar_id = self._fsm.find_similar_state(fingerprint)
        if similar_id is not None:
            return StateLocation(
                result=LocateResult.SIMILAR,
                state_id=similar_id,
                confidence=self._SIMILAR_CONFIDENCE,
                matched_fingerprint=fingerprint,
            )

        # No match
        return StateLocation(
            result=LocateResult.UNKNOWN,
            confidence=0.0,
            matched_fingerprint=fingerprint,
        )
