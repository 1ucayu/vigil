"""Screen-to-FSM state mapping via accessibility tree fingerprinting.

Maps the current device screen to a known FSM state using structural fingerprinting.
Falls back to similarity-based matching for Tier 3 evolution when exact match fails.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel

from vigil.models.fsm import AppFSM
from vigil.models.state import RawScreen

_REFINED_SECONDARY_MARKER = "::secondary:"


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
        # Refined-sibling index keyed by (base_fingerprint, secondary_hash) so
        # a live screen with the same secondary signature but a DIFFERENT base
        # structural fingerprint cannot falsely localize to a refined sibling.
        # The builder guarantees every refined fingerprint encodes a single
        # concrete secondary hash (see _refine_conflicting_successors policy).
        self._refined_fp_index: dict[tuple[str, str], str] = {}
        for state in fsm.states.values():
            self._fp_index[state.fingerprint] = state.state_id
            self._index_refined_sibling(state.fingerprint, state.state_id)
            if state.structural_fingerprint:
                self._fp_index[state.structural_fingerprint] = state.state_id
                self._index_refined_sibling(state.structural_fingerprint, state.state_id)

    def _index_refined_sibling(self, fingerprint: str, state_id: str) -> None:
        if _REFINED_SECONDARY_MARKER not in fingerprint:
            return
        base_fp, _, secondary_hash = fingerprint.rpartition(_REFINED_SECONDARY_MARKER)
        if not base_fp or not secondary_hash:
            return
        # Builder stores 16-char hashes; RawScreen.get_structural_fingerprint
        # returns 12-char hashes (same prefix, narrower truncation). Normalize
        # the index key to the live-screen length so locate() can match.
        live_base_fp = base_fp[:12]
        key = (live_base_fp, secondary_hash)
        existing = self._refined_fp_index.get(key)
        if existing is not None and existing != state_id:
            # Two refined siblings with the same (base_fp, secondary_hash) would
            # be indistinguishable; mark the slot poisoned by storing an empty
            # string and let lookups fall through to UNKNOWN/SIMILAR.
            self._refined_fp_index[key] = ""
            return
        self._refined_fp_index[key] = state_id

    def locate(self, screen: RawScreen) -> StateLocation:
        """Locate a live screen in the FSM.

        Args:
            screen: The current screen with parsed UI elements.

        Returns:
            StateLocation with match result, state_id, and confidence.
        """
        fp = screen.get_structural_fingerprint()
        location = self.locate_by_fingerprint(fp)
        if location.result is not LocateResult.UNKNOWN:
            return location

        # Refined-sibling fallback: require the live screen's structural
        # fingerprint to match the refined state's *base* fingerprint AND
        # its concrete secondary hash. Both must agree — secondary hash
        # alone is unsafe because unrelated screens can share an activity
        # name / anchor set.
        secondary_hash = self._secondary_feature_signature_hash(screen)
        refined_state_id = self._refined_fp_index.get((fp, secondary_hash))
        if refined_state_id:
            refined = self._fsm.states.get(refined_state_id)
            matched = refined.structural_fingerprint if refined else None
            return StateLocation(
                result=LocateResult.EXACT,
                state_id=refined_state_id,
                confidence=1.0,
                matched_fingerprint=matched or (refined.fingerprint if refined else fp),
            )

        return location

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

    @staticmethod
    def _secondary_feature_signature_hash(screen: RawScreen) -> str:
        text_anchors = sorted(
            {
                (el.text or "").strip()
                for el in screen.elements
                if (el.text or "").strip() and not el.is_editable
            }
        )
        desc_anchors = sorted(
            {
                (el.content_description or "").strip()
                for el in screen.elements
                if (el.content_description or "").strip()
            }
        )
        signature = (
            screen.activity_name or "",
            bool(screen.metadata.get("has_modal")),
            tuple(text_anchors),
            tuple(desc_anchors),
        )
        return hashlib.sha256(repr(signature).encode()).hexdigest()[:12]
