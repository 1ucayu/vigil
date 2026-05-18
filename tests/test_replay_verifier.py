"""Tests for vigil.neuro.replay_verifier — Stage 5 confidence updates."""

from __future__ import annotations

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.neuro.replay_verifier import ReplayVerifier


def _make_fsm_with_two_transitions() -> AppFSM:
    fsm = AppFSM(app_package="com.test.app")
    for sid in ("s1", "s2", "s3"):
        fsm.add_state(
            AbstractState(
                state_id=sid,
                name=sid,
                fingerprint=f"fp_{sid}",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
    fsm.add_transition(
        Transition(source="s1", target="s2", action={"type": "click"}, confidence=0.0)
    )
    fsm.add_transition(
        Transition(source="s2", target="s3", action={"type": "click"}, confidence=0.0)
    )
    fsm.initial_state = "s1"
    return fsm


class TestReplayVerifier:
    def test_all_success_confidence_one(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        verifier = ReplayVerifier(fsm, trials=3, replay_hook=lambda *_: True)
        rho = verifier.verify_transition(fsm.transitions[0])
        assert rho == pytest.approx(1.0)
        assert fsm.transitions[0].confidence == pytest.approx(1.0)

    def test_partial_success_confidence_fraction(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        calls = {"n": 0}

        def hook(_fsm: AppFSM, _t: Transition, _trial: int) -> bool:
            calls["n"] += 1
            return calls["n"] <= 2  # 2 successes, 1 failure

        verifier = ReplayVerifier(fsm, trials=3, replay_hook=hook)
        rho = verifier.verify_transition(fsm.transitions[0])
        assert rho == pytest.approx(2 / 3)
        assert fsm.transitions[0].confidence == pytest.approx(2 / 3)

    def test_all_failure_keeps_uncertain(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        verifier = ReplayVerifier(fsm, trials=3, replay_hook=lambda *_: False)
        rho = verifier.verify_transition(fsm.transitions[0])
        assert rho == 0.0
        # Default VerificationConfig.confidence_threshold is 0.7 so this stays
        # below the high-trust ALLOW gate.
        assert fsm.transitions[0].confidence < 0.7

    def test_hook_exception_reraises_without_mutating_confidence(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        fsm.transitions[0].confidence = 0.42
        calls = {"n": 0}

        def hook(_fsm: AppFSM, _t: Transition, _trial: int) -> bool:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("flaky")
            return True

        verifier = ReplayVerifier(fsm, trials=3, replay_hook=hook)
        with pytest.raises(RuntimeError, match="flaky"):
            verifier.verify_transition(fsm.transitions[0])
        assert fsm.transitions[0].confidence == pytest.approx(0.42)

    def test_default_hook_configuration_raises(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        with pytest.raises(ValueError, match="replay_hook"):
            ReplayVerifier(fsm, trials=2)

    def test_false_returns_count_as_replay_failures(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        verifier = ReplayVerifier(fsm, trials=2, replay_hook=lambda *_: False)
        rho = verifier.verify_transition(fsm.transitions[0])
        assert rho == 0.0
        assert fsm.transitions[0].confidence == 0.0

    def test_verify_all_walks_every_transition(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        verifier = ReplayVerifier(fsm, trials=2, replay_hook=lambda *_: True)
        results = verifier.verify_all()
        assert len(results) == 2
        for v in results.values():
            assert v == pytest.approx(1.0)

    def test_invalid_trials_rejected(self) -> None:
        fsm = _make_fsm_with_two_transitions()
        with pytest.raises(ValueError):
            ReplayVerifier(fsm, trials=0)
