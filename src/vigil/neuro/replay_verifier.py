"""Stage 5: FSM Verification via Replay.

Enumerates bounded-length paths via symbolic execution, converts to test cases,
replays on real device via uiautomator2. Each transition gets a confidence score
``rho(s, a, s') = success_count / trial_count``.

This module provides a small, injectable surface that supports unit tests
without touching a device. Real-device hooks (uiautomator2 driver, ADB)
plug in via the ``replay_hook`` callable; the default raises
``ValueError`` so misconfigurations fail loudly.
"""

from __future__ import annotations

from collections.abc import Callable

from vigil.models.fsm import AppFSM, Transition

ReplayHook = Callable[[AppFSM, Transition, int], bool]


def _no_device_hook(_fsm: AppFSM, _t: Transition, _trial: int) -> bool:
    raise NotImplementedError(
        "No replay_hook configured; inject a callable that drives the device."
    )


class ReplayVerifier:
    """Run bounded replay trials per transition and update Transition.confidence.

    Args:
        fsm: The app's FSM whose transitions will be verified.
        trials: Number of replay attempts per transition (default 3).
        replay_hook: Callable ``(fsm, transition, trial_index) -> bool`` returning
            True on a successful replay. A hook is required so missing device
            wiring cannot silently rewrite confidence scores to 0.0.
    """

    def __init__(
        self,
        fsm: AppFSM,
        trials: int = 3,
        replay_hook: ReplayHook | None = None,
    ) -> None:
        if trials < 1:
            raise ValueError("trials must be >= 1")
        if replay_hook is None or replay_hook is _no_device_hook:
            raise ValueError("replay_hook must be configured before replay verification")
        self._fsm = fsm
        self._trials = trials
        self._replay_hook = replay_hook

    def verify_transition(self, transition: Transition) -> float:
        """Replay ``transition`` ``trials`` times and update its confidence.

        Returns ``rho = success_count / trials`` and writes the value to
        ``transition.confidence`` in place. Hook exceptions are infrastructure
        failures: they propagate and leave ``transition.confidence`` unchanged.
        Only a clean ``False`` return is counted as a replay failure.
        """
        successes = 0
        for trial in range(self._trials):
            if self._replay_hook(self._fsm, transition, trial):
                successes += 1
        rho = successes / self._trials
        transition.confidence = rho
        return rho

    def verify_all(self) -> dict[tuple[str, str, str], float]:
        """Verify every transition. Keyed by (source, target, action_type)."""
        results: dict[tuple[str, str, str], float] = {}
        for t in self._fsm.transitions:
            action_type = str(t.action.get("type", ""))
            results[(t.source, t.target, action_type)] = self.verify_transition(t)
        return results
