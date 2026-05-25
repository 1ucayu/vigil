"""Tests for scripts/validate_fsm.py — FSM validation against traces."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from vigil.neuro.fsm_builder import FsmBuilder
from vigil.scripts.validate_fsm import (
    ValidationReason,
    validate_fsm,
    validate_trace_against_built_fsm,
)


def _make_screen(
    sid: str,
    activity: str,
    title: str,
    *,
    extra_elements: list[dict[str, Any]] | None = None,
    has_modal: bool = False,
) -> dict[str, Any]:
    base_elements: list[dict[str, Any]] = [
        {
            "element_id": "e_title",
            "class_name": "android.widget.TextView",
            "resource_id": "com.test:id/title",
            "text": title,
            "depth": 1,
            "is_clickable": False,
        }
    ]
    if extra_elements:
        base_elements.extend(extra_elements)
    return {
        "screen_id": sid,
        "activity_name": activity,
        "metadata": {"has_modal": has_modal, "page_title": title},
        "interactable_elements": base_elements,
    }


@pytest.fixture
def synthetic_trace(tmp_path: Path) -> Path:
    """A trace with three structurally distinct screens linked by clear edges."""
    data = {
        "app_package": "com.test.app",
        "screens": {
            "scr_001": _make_screen(
                "scr_001",
                ".MainActivity",
                "Home",
                extra_elements=[
                    {
                        "element_id": "e_go",
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/btn_go",
                        "text": "Go",
                        "depth": 2,
                        "is_clickable": True,
                    }
                ],
            ),
            "scr_002": _make_screen(
                "scr_002",
                ".SettingsActivity",
                "Settings",
                extra_elements=[
                    {
                        "element_id": "e_toggle",
                        "class_name": "android.widget.Switch",
                        "resource_id": "com.test:id/toggle",
                        "text": "On",
                        "depth": 2,
                        "is_clickable": True,
                        "is_checkable": True,
                    }
                ],
            ),
            "scr_003": _make_screen(
                "scr_003",
                ".DetailActivity",
                "Detail",
                extra_elements=[
                    {
                        "element_id": "e_icon",
                        "class_name": "android.widget.ImageView",
                        "resource_id": "com.test:id/icon",
                        "depth": 2,
                        "is_clickable": True,
                    }
                ],
            ),
        },
        "traces": [
            {
                "step_number": 1,
                "source_screen_id": "scr_001",
                "target_screen_id": "scr_002",
                "action": {
                    "action_type": "click",
                    "target_element_id": "e_go",
                },
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_002",
                "target_screen_id": "scr_003",
                "action": {
                    "action_type": "click",
                    "target_element_id": "e_toggle",
                },
            },
            {
                "step_number": 3,
                "source_screen_id": "scr_003",
                "target_screen_id": "scr_002",
                "action": {"action_type": "navigate_back"},
            },
        ],
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(data))
    return path


class TestRoundTripAllOk:
    def test_built_fsm_validates_its_own_trace(self, synthetic_trace: Path) -> None:
        report = validate_trace_against_built_fsm(synthetic_trace, "com.test.app")
        assert report.total_steps == 3
        assert report.counts_by_reason == {ValidationReason.OK.value: 3}
        assert all(step.reason is ValidationReason.OK for step in report.steps)


class TestStateNotFound:
    def test_unknown_source_screen_is_state_not_found(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        for state in fsm.states.values():
            if "scr_001" in state.raw_screens:
                state.raw_screens.remove("scr_001")

        report = validate_fsm(fsm, synthetic_trace)
        reasons = [s.reason for s in report.steps if s.source_screen_id == "scr_001"]
        assert reasons and reasons[0] is ValidationReason.STATE_NOT_FOUND


class TestTransitionNotInFsm:
    def test_missing_edge_is_transition_not_in_fsm(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        # Drop every outgoing click edge from s_001 so the action type no longer
        # exists at all — validator should report transition_not_in_fsm, not a
        # canonical-identity mismatch.
        fsm.transitions = [
            t
            for t in fsm.transitions
            if not (t.source == "s_001" and t.action.get("type") == "click")
        ]
        report = validate_fsm(fsm, synthetic_trace)
        reasons = [s.reason for s in report.steps if s.source_state_id == "s_001"]
        assert ValidationReason.TRANSITION_NOT_IN_FSM in reasons


class TestActionSignatureMismatch:
    def test_same_action_type_different_widget_is_signature_mismatch(self, tmp_path: Path) -> None:
        """If the FSM only has a click on btn_A, a trace step clicking btn_B
        (same action type, different resource_id) must surface as
        ``action_signature_mismatch`` — not as ``transition_not_in_fsm``."""
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": _make_screen(
                    "scr_001",
                    ".MainActivity",
                    "Home",
                    extra_elements=[
                        {
                            "element_id": "e_a",
                            "class_name": "android.widget.Button",
                            "resource_id": "com.test:id/btn_a",
                            "text": "A",
                            "depth": 2,
                            "is_clickable": True,
                        }
                    ],
                ),
                "scr_002": _make_screen("scr_002", ".SettingsActivity", "Settings"),
            },
            "traces": [
                {
                    "step_number": 1,
                    "source_screen_id": "scr_001",
                    "target_screen_id": "scr_002",
                    "action": {
                        "action_type": "click",
                        "target_element_id": "e_a",
                    },
                }
            ],
        }
        trace_path = tmp_path / "trace.json"
        trace_path.write_text(json.dumps(data))

        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(trace_path)

        # Synthesize a new trace whose click targets a DIFFERENT widget.
        other_data = deepcopy(data)
        other_data["screens"]["scr_001"]["interactable_elements"].append(
            {
                "element_id": "e_b",
                "class_name": "android.widget.Button",
                "resource_id": "com.test:id/btn_b",
                "text": "B",
                "depth": 2,
                "is_clickable": True,
            }
        )
        other_data["traces"][0]["action"]["target_element_id"] = "e_b"
        other_trace = tmp_path / "trace_b.json"
        other_trace.write_text(json.dumps(other_data))

        report = validate_fsm(fsm, other_trace)
        # Same source screen is in the FSM (scr_001 raw_screens), so this is
        # not state_not_found. The action's canonical identity differs.
        assert any(
            s.reason is ValidationReason.ACTION_SIGNATURE_MISMATCH for s in report.steps
        ), report.counts_by_reason

    def test_allow_to_unseen_target_screen_is_signature_mismatch(self, tmp_path: Path) -> None:
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": _make_screen(
                    "scr_001",
                    ".MainActivity",
                    "Home",
                    extra_elements=[
                        {
                            "element_id": "e_go",
                            "class_name": "android.widget.Button",
                            "resource_id": "com.test:id/btn_go",
                            "text": "Go",
                            "depth": 2,
                            "is_clickable": True,
                        }
                    ],
                ),
                "scr_002": _make_screen("scr_002", ".SettingsActivity", "Settings"),
            },
            "traces": [
                {
                    "step_number": 1,
                    "source_screen_id": "scr_001",
                    "target_screen_id": "scr_002",
                    "action": {
                        "action_type": "click",
                        "target_element_id": "e_go",
                    },
                }
            ],
        }
        trace_path = tmp_path / "trace.json"
        trace_path.write_text(json.dumps(data))

        fsm = FsmBuilder("com.test.app").build_from_trace(trace_path)

        unseen_data = deepcopy(data)
        unseen_data["screens"]["scr_unseen"] = _make_screen(
            "scr_unseen", ".BrandNewActivity", "Brand New"
        )
        unseen_data["traces"][0]["target_screen_id"] = "scr_unseen"
        unseen_trace = tmp_path / "trace_unseen_target.json"
        unseen_trace.write_text(json.dumps(unseen_data))

        report = validate_fsm(fsm, unseen_trace)
        first = report.steps[0]
        assert first.reason is ValidationReason.ACTION_SIGNATURE_MISMATCH
        assert report.counts_by_reason == {ValidationReason.ACTION_SIGNATURE_MISMATCH.value: 1}


class TestLowConfidence:
    def test_low_confidence_transition_is_flagged(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        for t in fsm.transitions:
            t.confidence = 0.1
        report = validate_fsm(fsm, synthetic_trace, confidence_threshold=0.7)
        assert any(
            s.reason is ValidationReason.LOW_CONFIDENCE for s in report.steps
        ), report.counts_by_reason


class TestSelectorResolutionFailed:
    def test_metadata_flag_short_circuits_to_selector_failure(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        trace_data = json.loads(synthetic_trace.read_text())
        trace_data["traces"][0].setdefault("metadata", {})["selector_resolution"] = "ambiguous"
        synthetic_trace.write_text(json.dumps(trace_data))

        report = validate_fsm(fsm, synthetic_trace)
        first = next(s for s in report.steps if s.step_index == 1)
        assert first.reason is ValidationReason.SELECTOR_RESOLUTION_FAILED


class TestCli:
    def test_cli_round_trip(self, synthetic_trace: Path, tmp_path: Path) -> None:
        from vigil.scripts.validate_fsm import main

        out = tmp_path / "report.json"
        rc = main(
            [
                "--trace",
                str(synthetic_trace),
                "--app",
                "com.test.app",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = json.loads(out.read_text())
        assert payload["total_steps"] == 3
        assert payload["counts_by_reason"] == {ValidationReason.OK.value: 3}


# ── Follow-up: ALLOW with no matched target must not report ok ──


class TestTemplateBindingMissingPath:
    """When resolve_transition returns ALLOW but no concrete matched edge exists,
    validate_fsm must classify the step as TEMPLATE_BINDING_MISSING for dynamic
    container/template states, never as ok."""

    def test_dynamic_container_no_representative_edge_flagged(self, tmp_path: Path) -> None:
        from vigil.models.fsm import (
            AbstractState,
            AppFSM,
            ContainerType,
            HierarchyLevel,
            SubFsmTemplate,
            Transition,
        )

        # Hand-build a minimal FSM whose container state has a template but
        # no representative edge with the proposed identity. Replay a click
        # carrying identity that does NOT match any concrete edge.
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s_list",
                name="List",
                fingerprint="fp_list",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.DYNAMIC,
                sub_fsm_template_id="tmpl_1",
                raw_screens=["scr_001"],
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="s_detail",
                name="Detail",
                fingerprint="fp_detail",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                raw_screens=["scr_002"],
            )
        )
        fsm.sub_fsm_templates["tmpl_1"] = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
            states={"s_detail": fsm.states["s_detail"]},
        )
        # Concrete edge with a known identity that the replayed action will NOT
        # match.
        fsm.add_transition(
            Transition(
                source="s_list",
                target="s_detail",
                action={"type": "click", "target_text": "Known Item"},
                confidence=0.9,
            )
        )

        # Synthetic trace: replay a click with a DIFFERENT identity.
        trace_data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": {"screen_id": "scr_001", "activity_name": ".ListActivity"},
                "scr_002": {"screen_id": "scr_002", "activity_name": ".DetailActivity"},
            },
            "traces": [
                {
                    "step_number": 1,
                    "source_screen_id": "scr_001",
                    "target_screen_id": "scr_002",
                    "action": {
                        "action_type": "click",
                        "target_element_id": "e_x",
                        "target_text": "Unknown Mystery Item",
                    },
                }
            ],
        }
        path = tmp_path / "trace.json"
        path.write_text(json.dumps(trace_data))

        report = validate_fsm(fsm, path)
        # The step must NOT be ok. Because s_list has container_type=DYNAMIC
        # and a sub_fsm_template_id, it should be TEMPLATE_BINDING_MISSING.
        reasons = [s.reason for s in report.steps]
        assert ValidationReason.OK not in reasons
        assert ValidationReason.TEMPLATE_BINDING_MISSING in reasons


_SETTINGS_TRACE = (
    Path(__file__).parent.parent
    / "data/apps/com_android_settings/traces/exploration_20260420_164556.json"
)


class TestSettingsTraceRegression:
    """Hard-assert deterministic-topology invariants on the real Settings
    trace; report OK count and reason counts as diagnostic output.
    """

    @pytest.mark.skipif(
        not _SETTINGS_TRACE.exists(),
        reason=f"Settings trace not present at {_SETTINGS_TRACE}",
    )
    def test_settings_trace_topology_invariants(self, capsys: pytest.CaptureFixture) -> None:
        from collections import Counter

        from vigil.models.fsm import ContainerType, canonical_action_key

        fsm = FsmBuilder("com.android.settings").build_from_trace(_SETTINGS_TRACE)

        # 1. Zero dangling state/template references.
        live_state_ids = set(fsm.states.keys())
        dangling = [
            t
            for t in fsm.transitions
            if t.source not in live_state_ids or t.target not in live_state_ids
        ]
        assert not dangling, f"dangling transitions: {dangling[:3]}"
        for tmpl in fsm.sub_fsm_templates.values():
            assert (
                tmpl.source_state_id in live_state_ids
            ), f"template {tmpl.template_id} sources missing state"
            for sid in tmpl.states:
                assert (
                    sid in live_state_ids
                ), f"template {tmpl.template_id} references missing state {sid}"

        # 2. No state has sub_fsm_template_id while container_type != DYNAMIC.
        for state in fsm.states.values():
            if state.sub_fsm_template_id is not None:
                assert state.container_type == ContainerType.DYNAMIC, (
                    f"stale template id on non-DYNAMIC state {state.state_id}: "
                    f"container_type={state.container_type!r}, "
                    f"sub_fsm_template_id={state.sub_fsm_template_id!r}"
                )

        # 2b. No orphan templates — every template_id in sub_fsm_templates
        #     must be referenced by at least one state's sub_fsm_template_id.
        referenced_template_ids = {
            s.sub_fsm_template_id for s in fsm.states.values() if s.sub_fsm_template_id is not None
        }
        for tid in fsm.sub_fsm_templates:
            assert (
                tid in referenced_template_ids
            ), f"orphan Sub-FSM template {tid!r} not referenced by any state"

        # 3. Structural determinism: for every (source, canonical_action_key)
        #    in high-trust transitions, at most one distinct target.
        groups: dict[tuple[str, tuple[tuple[str, object], ...]], set[str]] = {}
        for t in fsm.transitions:
            if t.low_trust:
                continue
            groups.setdefault((t.source, canonical_action_key(t.action)), set()).add(t.target)
        for (src, key), targets in groups.items():
            assert len(targets) <= 1, (
                f"non-deterministic transition: source={src} key={key} "
                f"targets={sorted(targets)}"
            )

        # Diagnostic-only: report OK/reason counts; do not assert on the floor.
        report = validate_fsm(fsm, _SETTINGS_TRACE)
        reasons = Counter(s.reason for s in report.steps)
        total = len(report.steps)
        ok = reasons.get(ValidationReason.OK, 0)
        with capsys.disabled():
            print(
                f"\n[settings regression] total={total} ok={ok} " f"reasons={reasons.most_common()}"
            )


_FIDELITY_TRACE = (
    Path(__file__).parent.parent
    / "data/apps/com_vigil_market_fidelity/traces/exploration_20260525_034917.json"
)


@pytest.mark.skipif(
    not _FIDELITY_TRACE.exists(),
    reason="fidelity trace not present in this checkout",
)
class TestFidelityTraceRoundTrip:
    """The fidelity-app trace is the calibration target for action identity.
    Building the FSM from it and validating against the same trace must
    classify every step as OK — no action_signature_mismatch, no
    contradictory stable identity fields in the serialized transitions.
    """

    def test_fidelity_trace_validates_107_of_107(self) -> None:
        report = validate_trace_against_built_fsm(_FIDELITY_TRACE, app_package="com.vigil.market")
        from collections import Counter

        reasons = Counter(s.reason for s in report.steps)
        assert reasons == Counter({ValidationReason.OK: 107}), reasons

    def test_fidelity_fsm_has_no_identity_contradictions(self) -> None:
        builder = FsmBuilder("com.vigil.market")
        fsm = builder.build_from_trace(_FIDELITY_TRACE)
        contradictions = []
        for t in fsm.transitions:
            rid = t.action.get("resource_id") or ""
            trid = t.action.get("target_resource_id") or ""
            if rid and trid and rid != trid:
                contradictions.append((t.source, t.target, rid, trid))
        assert contradictions == [], (
            f"serialized transitions contradict on resource_id vs "
            f"target_resource_id: {contradictions}"
        )
