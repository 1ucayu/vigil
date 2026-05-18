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


def _screen(
    screen_id: str,
    activity_name: str,
    fingerprint: str,
    elements: list[UIElement],
) -> _MockScreen:
    s = _MockScreen(
        screen_id=screen_id,
        activity_name=activity_name,
        elements=elements,
    )
    s._forced_fp = fingerprint
    return s


def _wifi_elements() -> list[UIElement]:
    """Canonical element set for a WiFi-settings-like page."""
    return [
        UIElement(
            element_id="e1",
            class_name="android.widget.Switch",
            resource_id="id/wifi_toggle",
            is_clickable=True,
            depth=2,
        ),
        UIElement(
            element_id="e2",
            class_name="android.widget.TextView",
            resource_id="id/wifi_title",
            depth=2,
        ),
        UIElement(
            element_id="e3",
            class_name="android.widget.ListView",
            resource_id="id/networks",
            is_scrollable=True,
            depth=3,
        ),
        UIElement(
            element_id="e4", class_name="android.widget.TextView", resource_id="id/ssid", depth=4
        ),
    ]


def _main_elements() -> list[UIElement]:
    """Element set for the 'main settings' page (disjoint from wifi)."""
    return [
        UIElement(
            element_id="m1", class_name="android.widget.ImageView", resource_id="id/icon", depth=2
        ),
        UIElement(
            element_id="m2", class_name="android.widget.TextView", resource_id="id/header", depth=2
        ),
    ]


@pytest.fixture
def evolution_fsm_and_screens() -> tuple[AppFSM, dict[str, RawScreen]]:
    """FSM with raw_screens linked to each state so the evolver can compute Jaccard.

    s1 (MainSettings)  — linked to rs_main
    s2 (WiFiSettings)  — linked to rs_wifi
    s3 (WiFiDetail)    — linked to rs_detail
    """
    fsm = AppFSM(app_package="com.test.app")

    s1 = AbstractState(
        state_id="s1",
        name="MainSettings",
        fingerprint="fp_main",
        structural_fingerprint="fp_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        activity_name="com.test.app.Main",
        raw_screens=["rs_main"],
    )
    s2 = AbstractState(
        state_id="s2",
        name="WiFiSettings",
        fingerprint="fp_wifi",
        structural_fingerprint="fp_wifi",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s1",
        activity_name="com.test.app.Main",
        raw_screens=["rs_wifi"],
    )
    s3 = AbstractState(
        state_id="s3",
        name="WiFiDetail",
        fingerprint="fp_detail",
        structural_fingerprint="fp_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s2",
        activity_name="com.test.app.Main",
        raw_screens=[],  # deliberately empty for 0-score test
    )

    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.add_state(s3)
    fsm.initial_state = "s1"

    fsm.add_transition(
        Transition(
            source="s1", target="s2", action={"type": "click"}, confidence=0.95, observed_count=10
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

    raw_screens: dict[str, RawScreen] = {
        "rs_main": _screen("rs_main", "com.test.app.Main", "fp_main", _main_elements()),
        "rs_wifi": _screen("rs_wifi", "com.test.app.Main", "fp_wifi", _wifi_elements()),
    }
    return fsm, raw_screens


class TestInheritAndBind:
    def test_inherit_and_bind(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        # Same elements as wifi → Jaccard 1.0 with s2
        screen = _screen("scr_new", "com.test.app.Main", "fp_wifi_new", _wifi_elements())
        result = evolver.try_evolution(screen)

        assert result.evolved is True
        assert result.method == "inherit_and_bind"
        assert result.state_id == "s_evo_001"
        assert result.inherited_from == "s2"
        assert result.similarity_score == pytest.approx(1.0)

        new_state = fsm.states["s_evo_001"]
        assert new_state.fingerprint == "fp_wifi_new"
        assert "(evolved)" in new_state.name

    def test_inherited_activity(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        screen = _screen("scr_new", "com.test.app.Main", "fp_wifi_new", _wifi_elements())
        result = evolver.try_evolution(screen)

        inherited_from = fsm.states[result.inherited_from]
        new_state = fsm.states[result.state_id]
        assert new_state.activity_name == inherited_from.activity_name
        assert new_state.hierarchy_level == inherited_from.hierarchy_level

    def test_inherited_transitions(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        screen = _screen("scr_new", "com.test.app.Main", "fp_wifi_new", _wifi_elements())
        result = evolver.try_evolution(screen)

        inherited_from = result.inherited_from
        original_outgoing = [t for t in fsm.transitions if t.source == inherited_from]
        new_outgoing = [t for t in fsm.transitions if t.source == result.state_id]

        assert len(new_outgoing) == len(original_outgoing)
        original_types = sorted(t.action.get("type") for t in original_outgoing)
        new_types = sorted(t.action.get("type") for t in new_outgoing)
        assert new_types == original_types

        for new_t in new_outgoing:
            for orig_t in original_outgoing:
                if new_t.action.get("type") == orig_t.action.get("type"):
                    assert new_t.guard == orig_t.guard
                    assert new_t.target == orig_t.target

    def test_no_match(self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        # Wholly disjoint elements from any known state
        disjoint = [
            UIElement(element_id="x1", class_name="zzz.Foo", resource_id="id/foo", depth=7),
        ]
        screen = _screen("scr_new", "com.other", "fp_new", disjoint)
        result = evolver.try_evolution(screen)

        assert result.evolved is False
        assert result.method == "none"
        assert result.state_id is None

    def test_no_match_preserves_fsm(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        original_count = len(fsm.states)
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.99)
        screen = _screen(
            "scr_new",
            "com.other",
            "fp_new",
            [
                UIElement(element_id="x1", class_name="zzz.Foo", depth=7),
            ],
        )
        evolver.try_evolution(screen)

        assert len(fsm.states) == original_count


class TestExactFingerprintShortCircuit:
    def test_exact_fingerprint_match(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.99)
        # Reuse s2's fingerprint — short-circuit to score=1.0
        screen = _screen("scr_new", "com.test.app.Main", "fp_wifi", _wifi_elements())
        result = evolver.try_evolution(screen)
        assert result.evolved is True
        assert result.similarity_score == pytest.approx(1.0)
        assert result.inherited_from == "s2"


class TestEvolutionLog:
    def test_evolution_log_entry(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        evolver.try_evolution(
            _screen("s_new", "com.test.app.Main", "fp_wifi_new", _wifi_elements())
        )

        log = evolver.get_evolution_log()
        assert len(log) == 1
        entry = log[0]
        assert entry["new_state_id"] == "s_evo_001"
        assert entry["inherited_from"] == "s2"
        assert entry["method"] == "inherit_and_bind"
        assert "timestamp" in entry
        assert "similarity_score" in entry
        assert entry["screen_fingerprint"] == "fp_wifi_new"

    def test_no_evolution_no_log(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.99)
        evolver.try_evolution(
            _screen(
                "s_new",
                "com.other",
                "fp_new",
                [
                    UIElement(element_id="x1", class_name="zzz.Foo", depth=7),
                ],
            )
        )
        assert len(evolver.get_evolution_log()) == 0


class TestSequentialEvolution:
    def test_sequential_ids(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        r1 = evolver.try_evolution(_screen("a", "com.test.app.Main", "fp_wifi_a", _wifi_elements()))
        r2 = evolver.try_evolution(_screen("b", "com.test.app.Main", "fp_wifi_b", _wifi_elements()))

        assert r1.state_id == "s_evo_001"
        assert r2.state_id == "s_evo_002"
        assert "s_evo_001" in fsm.states
        assert "s_evo_002" in fsm.states


class TestCacheToDisk:
    def test_cache_and_reload(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]], tmp_path
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        evolver.try_evolution(_screen("a", "com.test.app.Main", "fp_wifi_a", _wifi_elements()))

        path = tmp_path / "fsm.json"
        evolver.cache_to_disk(str(path))

        reloaded = AppFSM.deserialize(path)
        assert "s_evo_001" in reloaded.states
        assert reloaded.states["s_evo_001"].fingerprint == "fp_wifi_a"
        assert len(reloaded.evolution_log) == 1


class TestEvolvedStateLocator:
    def test_evolved_state_found_by_locator(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        evolver.try_evolution(_screen("a", "com.test.app.Main", "fp_wifi_a", _wifi_elements()))

        locator = StateLocator(fsm)
        loc = locator.locate_by_fingerprint("fp_wifi_a")

        assert loc.result == LocateResult.EXACT
        assert loc.state_id == "s_evo_001"
        assert loc.confidence == 1.0


class TestComputeSimilarity:
    def test_identical_components(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        # s2 has rs_wifi → same components as wifi_elements
        wifi_comps = FsmEvolver._extract_components(raw_screens["rs_wifi"])
        score = evolver._compute_similarity_jaccard(wifi_comps, "s2")
        assert score == pytest.approx(1.0)

    def test_disjoint_components(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        foreign = {("zzz.Foo", "id/foo", 7)}
        score = evolver._compute_similarity_jaccard(foreign, "s2")
        assert score == 0.0

    def test_partial_overlap(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        # 4 wifi components; take 2 + 2 foreign → 2 intersect / 6 union = 1/3
        wifi_comps = FsmEvolver._extract_components(raw_screens["rs_wifi"])
        picked = set(list(wifi_comps)[:2])
        foreign = {("zzz.A", "id/a", 9), ("zzz.B", "id/b", 9)}
        score = evolver._compute_similarity_jaccard(picked | foreign, "s2")
        assert score == pytest.approx(2 / 6)

    def test_state_without_raw_screens_scores_zero(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, raw_screens = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, raw_screens=raw_screens, similarity_threshold=0.5)
        wifi_comps = FsmEvolver._extract_components(raw_screens["rs_wifi"])
        # s3 has raw_screens=[] → no cached components → 0.0
        score = evolver._compute_similarity_jaccard(wifi_comps, "s3")
        assert score == 0.0

    def test_evolver_without_raw_screens_all_zero(
        self, evolution_fsm_and_screens: tuple[AppFSM, dict[str, RawScreen]]
    ) -> None:
        fsm, _ = evolution_fsm_and_screens
        evolver = FsmEvolver(fsm, similarity_threshold=0.5)  # no raw_screens
        screen = _screen("scr_new", "com.test.app.Main", "fp_wifi_new", _wifi_elements())
        result = evolver.try_evolution(screen)
        assert result.evolved is False


# ── Low-trust inherited transitions ──────────────────────────────


from vigil.neuro.evolution import INHERITED_TRANSITION_CONFIDENCE  # noqa: E402


class TestInheritedConfidenceCap:
    def test_inherited_transition_confidence_is_capped(self) -> None:
        """Inherited edges must stay below the default 0.7 ALLOW threshold."""
        from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
        from vigil.models.state import RawScreen, UIElement
        from vigil.neuro.evolution import FsmEvolver

        fsm = AppFSM(app_package="com.test.app")
        src = AbstractState(
            state_id="s1",
            name="Src",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            raw_screens=["rs1"],
        )
        dst = AbstractState(
            state_id="s2",
            name="Dst",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        fsm.add_state(src)
        fsm.add_state(dst)
        # Original transition has confidence=1.0 (fully replay-validated).
        fsm.add_transition(
            Transition(source="s1", target="s2", action={"type": "click"}, confidence=1.0)
        )

        # Build a raw screen whose components match s1 exactly (similarity = 1.0).
        elements = [
            UIElement(
                element_id="e0",
                class_name="android.widget.TextView",
                resource_id="rid_a",
                depth=1,
            ),
            UIElement(
                element_id="e1",
                class_name="android.widget.Button",
                resource_id="rid_b",
                depth=2,
            ),
        ]
        rs_known = RawScreen(screen_id="rs1", elements=elements)
        rs_unseen = RawScreen(screen_id="rs_new", elements=elements)
        # Force the unseen screen to a different fingerprint so inherit path runs.
        rs_unseen.activity_name = "different"

        evolver = FsmEvolver(fsm, raw_screens={"rs1": rs_known}, similarity_threshold=0.5)
        result = evolver.try_evolution(rs_unseen)
        assert result.evolved is True

        evolved_transitions = [t for t in fsm.transitions if t.source == result.state_id]
        assert evolved_transitions
        for t in evolved_transitions:
            assert t.confidence <= INHERITED_TRANSITION_CONFIDENCE
