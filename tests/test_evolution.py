"""Tests for vigil.neuro.evolution — Tier 3 micro-evolution."""

from __future__ import annotations

import pytest
from pydantic import PrivateAttr

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.evolution import FsmEvolver
from vigil.symbolic.state_locator import LocateResult, StateLocator


class _MockScreen(RawScreen):
    """RawScreen subclass that returns a predetermined fingerprint."""

    _forced_fp: str = PrivateAttr(default="")

    def get_structural_fingerprint(self, scroll_aware: bool = True) -> str:
        return self._forced_fp


@pytest.fixture
def evolution_fsm() -> AppFSM:
    """FSM with two states for evolution testing.

    s1 (fp="fp_main") --click--> s2 (fp="fp_wifi")
    s2 --back--> s1
    s2 --click--> s3 (fp="fp_detail") with guard
    """
    fsm = AppFSM(app_package="com.test.app")

    s1 = AbstractState(
        state_id="s1",
        name="MainSettings",
        fingerprint="fp_main",
        structural_fingerprint="fp_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        activity_name="com.test.app.Main",
    )
    s2 = AbstractState(
        state_id="s2",
        name="WiFiSettings",
        fingerprint="fp_wifi",
        structural_fingerprint="fp_wifi",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s1",
        activity_name="com.test.app.Main",
    )
    s3 = AbstractState(
        state_id="s3",
        name="WiFiDetail",
        fingerprint="fp_detail",
        structural_fingerprint="fp_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s2",
        activity_name="com.test.app.Main",
    )

    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.add_state(s3)
    fsm.initial_state = "s1"

    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click"},
            confidence=0.95,
            observed_count=10,
        )
    )
    fsm.add_transition(
        Transition(
            source="s2",
            target="s1",
            action={"type": "navigate_back"},
            confidence=0.90,
            observed_count=8,
        )
    )
    fsm.add_transition(
        Transition(
            source="s2",
            target="s3",
            action={"type": "click"},
            guard='read(wifi_item, text) != ""',
            confidence=0.85,
            observed_count=5,
        )
    )

    return fsm


def _make_screen(fingerprint: str) -> _MockScreen:
    """Create a RawScreen subclass that returns the given fingerprint."""
    screen = _MockScreen(
        screen_id="scr_test",
        activity_name="com.test.app.Main",
        elements=[
            UIElement(
                element_id="e_001",
                class_name="android.widget.TextView",
                text="Test",
                is_clickable=True,
            )
        ],
    )
    screen._forced_fp = fingerprint
    return screen


class TestInheritAndBind:
    def test_inherit_and_bind(self, evolution_fsm: AppFSM) -> None:
        # "fp_wifj" is very similar to "fp_wifi" (1 char diff)
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        screen = _make_screen("fp_wifj")
        result = evolver.try_evolution(screen)

        assert result.evolved is True
        assert result.method == "inherit_and_bind"
        assert result.state_id == "s_evo_001"
        assert result.inherited_from is not None
        assert result.similarity_score > 0.0

        # New state added to FSM
        assert "s_evo_001" in evolution_fsm.states
        new_state = evolution_fsm.states["s_evo_001"]
        assert new_state.fingerprint == "fp_wifj"
        assert "(evolved)" in new_state.name

    def test_inherited_activity(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        screen = _make_screen("fp_wifj")
        result = evolver.try_evolution(screen)

        inherited_from = evolution_fsm.states[result.inherited_from]
        new_state = evolution_fsm.states[result.state_id]
        assert new_state.activity_name == inherited_from.activity_name
        assert new_state.hierarchy_level == inherited_from.hierarchy_level

    def test_inherited_transitions(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        screen = _make_screen("fp_wifj")
        result = evolver.try_evolution(screen)

        # Find outgoing transitions of the inherited-from state
        inherited_from = result.inherited_from
        original_outgoing = [t for t in evolution_fsm.transitions if t.source == inherited_from]
        new_outgoing = [t for t in evolution_fsm.transitions if t.source == result.state_id]

        assert len(new_outgoing) == len(original_outgoing)
        original_types = sorted(t.action.get("type") for t in original_outgoing)
        new_types = sorted(t.action.get("type") for t in new_outgoing)
        assert new_types == original_types

        # Check guards are preserved
        for new_t in new_outgoing:
            for orig_t in original_outgoing:
                if new_t.action.get("type") == orig_t.action.get("type"):
                    assert new_t.guard == orig_t.guard
                    assert new_t.target == orig_t.target

    def test_no_match(self, evolution_fsm: AppFSM) -> None:
        # Very high threshold — nothing will match
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.99)
        screen = _make_screen("zzzzzzzzzzzzzzzz")
        result = evolver.try_evolution(screen)

        assert result.evolved is False
        assert result.method == "none"
        assert result.state_id is None

    def test_no_match_preserves_fsm(self, evolution_fsm: AppFSM) -> None:
        original_count = len(evolution_fsm.states)
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.99)
        screen = _make_screen("zzzzzzzzzzzzzzzz")
        evolver.try_evolution(screen)

        assert len(evolution_fsm.states) == original_count


class TestEvolutionLog:
    def test_evolution_log_entry(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        screen = _make_screen("fp_wifj")
        evolver.try_evolution(screen)

        log = evolver.get_evolution_log()
        assert len(log) == 1
        entry = log[0]
        assert entry["new_state_id"] == "s_evo_001"
        assert entry["inherited_from"] is not None
        assert entry["method"] == "inherit_and_bind"
        assert "timestamp" in entry
        assert "similarity_score" in entry
        assert entry["screen_fingerprint"] == "fp_wifj"

    def test_no_evolution_no_log(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.99)
        screen = _make_screen("zzzzzzzzzzzzzzzz")
        evolver.try_evolution(screen)

        assert len(evolver.get_evolution_log()) == 0


class TestSequentialEvolution:
    def test_sequential_ids(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        screen1 = _make_screen("fp_wifj")
        screen2 = _make_screen("fp_wifl")
        r1 = evolver.try_evolution(screen1)
        r2 = evolver.try_evolution(screen2)

        assert r1.state_id == "s_evo_001"
        assert r2.state_id == "s_evo_002"
        assert "s_evo_001" in evolution_fsm.states
        assert "s_evo_002" in evolution_fsm.states

    def test_sequential_log(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        evolver.try_evolution(_make_screen("fp_wifj"))
        evolver.try_evolution(_make_screen("fp_wifl"))

        assert len(evolver.get_evolution_log()) == 2


class TestCacheToDisk:
    def test_cache_and_reload(self, evolution_fsm: AppFSM, tmp_path) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        evolver.try_evolution(_make_screen("fp_wifj"))

        path = tmp_path / "fsm.json"
        evolver.cache_to_disk(str(path))

        reloaded = AppFSM.deserialize(path)
        assert "s_evo_001" in reloaded.states
        assert reloaded.states["s_evo_001"].fingerprint == "fp_wifj"
        assert len(reloaded.evolution_log) == 1


class TestEvolvedStateLocator:
    def test_evolved_state_found_by_locator(self, evolution_fsm: AppFSM) -> None:
        evolver = FsmEvolver(evolution_fsm, similarity_threshold=0.25)
        evolver.try_evolution(_make_screen("fp_wifj"))

        # Re-create locator with the updated FSM
        locator = StateLocator(evolution_fsm)
        loc = locator.locate_by_fingerprint("fp_wifj")

        assert loc.result == LocateResult.EXACT
        assert loc.state_id == "s_evo_001"
        assert loc.confidence == 1.0


class TestComputeSimilarity:
    def test_identical(self) -> None:
        components: set[tuple[str, str, int]] = {("Button", "id/btn", 2)}
        state = AbstractState(
            state_id="s1",
            name="Test",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name="com.test.Activity",
        )
        score = FsmEvolver._compute_similarity_jaccard(components, state)
        assert score >= 0.0

    def test_completely_different(self) -> None:
        components: set[tuple[str, str, int]] = {("X", "id/x", 1)}
        state = AbstractState(
            state_id="s1",
            name="Test",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        score = FsmEvolver._compute_similarity_jaccard(components, state)
        assert score == 0.0

    def test_partial_match(self) -> None:
        components: set[tuple[str, str, int]] = {("Button", "id/btn", 2)}
        state = AbstractState(
            state_id="s1",
            name="Test",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name="com.test.Activity",
        )
        score = FsmEvolver._compute_similarity_jaccard(components, state)
        assert 0.0 <= score <= 1.0
