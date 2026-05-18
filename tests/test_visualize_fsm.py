"""Tests for the interactive HTML FSM visualization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    StateSemanticProfile,
    Transition,
)
from vigil.scripts.visualize_fsm import _fsm_to_view_dict, render_fsm_html

_SAFE_STATE_FIELDS = {
    "state_id",
    "name",
    "hierarchy_level",
    "parent_state",
    "activity_name",
    "container_type",
    "sub_fsm_template_id",
}

_GUARD = "read(secret_field, enabled) == true"


def _extract_payload(html: str) -> dict[str, Any]:
    marker = "const FSM_DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n\n(function()", start)
    return json.loads(html[start:end])


def _render_html(fsm: AppFSM, tmp_path: Path, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    fsm_path = tmp_path / "fsm.json"
    out = tmp_path / "fsm.html"
    fsm.serialize(fsm_path)

    render_fsm_html(fsm_path, out, **kwargs)

    html = out.read_text(encoding="utf-8")
    return html, _extract_payload(html)


def _sensitive_fsm() -> AppFSM:
    fsm = AppFSM(app_package="com.example.sensitive")

    s1 = AbstractState(
        state_id="s1",
        name="Main",
        fingerprint="fp_secret_main",
        structural_fingerprint="struct_secret_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        activity_name="com.example.MainActivity",
        invariants=["secret invariant"],
        raw_screens=["raw_screen_secret"],
        container_type=ContainerType.DYNAMIC,
        container_resource_id="container_secret",
        semantic_profile=StateSemanticProfile(
            alt_text="secret alt text",
            page_function="secret page function",
            expected_actions=["secret action"],
            icon_labels={"secret_icon": "secret icon label"},
            generation_confidence=0.91,
        ),
        state_invariants=["secret state invariant"],
        invariant_confidence=0.84,
        sub_fsm_template_id="tmpl_secret",
    )
    s2 = AbstractState(
        state_id="s2",
        name="Detail",
        fingerprint="fp_secret_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s1",
        activity_name="com.example.MainActivity",
    )
    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.initial_state = "s1"
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={
                "type": "click",
                "target": "wifi_entry",
                "target_text": "private network",
                "bounds": [1, 2, 3, 4],
            },
            guard=_GUARD,
            confidence=0.91,
            observed_count=3,
        )
    )
    return fsm


def test_view_dict_summary(sample_fsm: AppFSM) -> None:
    view = _fsm_to_view_dict(sample_fsm)
    assert view["app_package"] == "com.android.settings"
    assert view["initial_state"] == "s1"
    assert view["summary"] == {"num_states": 3, "num_transitions": 2}
    state_ids = {s["state_id"] for s in view["states"]}
    assert state_ids == {"s1", "s2", "s3"}
    assert all(set(s) == _SAFE_STATE_FIELDS for s in view["states"])
    assert view["transitions"][0]["source"] == "s1"
    assert view["transitions"][0]["action"] == {"type": "click"}
    assert "guard" not in view["transitions"][1]
    assert "com.android.settings.Settings" in view["activity_colors"]
    json.dumps(view)


def test_render_fsm_html(sample_fsm: AppFSM, tmp_path: Path) -> None:
    html, payload = _render_html(sample_fsm, tmp_path)

    assert len(html) > 1000
    assert payload["app_package"] == "com.android.settings"
    assert {s["state_id"] for s in payload["states"]} == {"s1", "s2", "s3"}
    assert payload["transitions"][0]["action"] == {"type": "click"}
    assert "wifi_entry" not in html
    assert 'id="sidebar"' in html
    assert "Click a state to view details" in html
    assert "const FSM_DATA =" in html


def test_render_fsm_html_redacts_sensitive_fields_by_default(tmp_path: Path) -> None:
    html, payload = _render_html(_sensitive_fsm(), tmp_path)

    state = payload["states"][0]
    transition = payload["transitions"][0]

    assert set(state) == _SAFE_STATE_FIELDS
    assert transition == {
        "source": "s1",
        "target": "s2",
        "action": {"type": "click"},
        "confidence": 0.91,
        "observed_count": 3,
    }
    for key in ("raw_screens", "semantic_profile", "icon_labels", "invariants"):
        assert key not in html
    for secret_value in (
        "raw_screen_secret",
        "secret alt text",
        "secret_icon",
        "secret invariant",
        "wifi_entry",
        "private network",
        _GUARD,
    ):
        assert secret_value not in html


def test_render_fsm_html_can_opt_into_sensitive_details(tmp_path: Path) -> None:
    html, payload = _render_html(_sensitive_fsm(), tmp_path, include_sensitive_details=True)

    state = payload["states"][0]
    transition = payload["transitions"][0]

    assert state["raw_screens"] == ["raw_screen_secret"]
    assert state["semantic_profile"]["icon_labels"] == {"secret_icon": "secret icon label"}
    assert state["invariants"] == ["secret invariant"]
    assert transition["action"]["target"] == "wifi_entry"
    assert transition["action"]["target_text"] == "private network"
    assert "guard" not in transition
    assert "raw_screens" in html
    assert "semantic_profile" in html
    assert "icon_labels" in html
    assert _GUARD not in html


def test_render_fsm_html_show_guards_controls_guard_output(tmp_path: Path) -> None:
    default_html, default_payload = _render_html(_sensitive_fsm(), tmp_path)
    assert _GUARD not in default_html
    assert "guard" not in default_payload["transitions"][0]

    guarded_html, guarded_payload = _render_html(
        _sensitive_fsm(),
        tmp_path,
        show_guards=True,
    )
    assert _GUARD in guarded_html
    assert guarded_payload["transitions"][0]["guard"] == _GUARD


def test_render_fsm_html_self_loop_transition_renders(tmp_path: Path) -> None:
    fsm = AppFSM(app_package="com.example.loop")
    state = AbstractState(
        state_id="s1",
        name="Loop",
        fingerprint="fp_loop",
        hierarchy_level=HierarchyLevel.ACTIVITY,
    )
    fsm.add_state(state)
    fsm.initial_state = "s1"
    fsm.add_transition(
        Transition(
            source="s1",
            target="s1",
            action={"type": "click", "target": "refresh_button"},
            confidence=0.8,
            observed_count=2,
        )
    )

    html, payload = _render_html(fsm, tmp_path)

    assert payload["transitions"][0]["source"] == "s1"
    assert payload["transitions"][0]["target"] == "s1"
    assert "l.source === l.target" in html
    assert "parallelOffset" in html


def test_render_fsm_html_large_graph_uses_grid_initial_layout(tmp_path: Path) -> None:
    fsm = AppFSM(app_package="com.example.large")
    for i in range(101):
        state_id = f"s{i}"
        fsm.add_state(
            AbstractState(
                state_id=state_id,
                name=f"State {i}",
                fingerprint=f"fp_{i}",
                hierarchy_level=HierarchyLevel.FRAGMENT,
            )
        )
    fsm.initial_state = "s0"

    html, payload = _render_html(fsm, tmp_path)

    assert payload["summary"]["num_states"] == 101
    assert "if (total > 100)" in html
    assert "requestAnimationFrame(runSimulationChunk)" in html
    assert "5000 / nodes.length" in html
