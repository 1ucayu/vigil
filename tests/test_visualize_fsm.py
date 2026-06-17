"""Tests for the interactive HTML FSM visualization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vigil.core.paths import OUTPUT_DOCS_DIR, resolve_generated_output_path
from vigil.models.fsm import (
    AbstractState,
    AndroidStateContext,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    StateAbstraction,
    StateAnnotations,
    StateEvidence,
    StateIdentity,
    StateInvariant,
    Transition,
)
from vigil.scripts.visualize_fsm import (
    _fsm_to_view_dict,
    default_output_path,
    render_fsm_compare_html,
    render_fsm_html,
)

_SAFE_STATE_FIELDS = {
    "state_id",
    "name",
    "hierarchy_level",
    "parent_state",
    "kind",
    "android_context",
    "abstraction",
}

_GUARD = "read(secret_field, enabled) == true"


def _extract_payload(html: str) -> dict[str, Any]:
    marker = "const FSM_DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n\n(function()", start)
    return json.loads(html[start:end])


def _extract_compare_payload(html: str) -> dict[str, Any]:
    marker = "const COMPARE_DATA = "
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
        hierarchy_level=HierarchyLevel.ACTIVITY,
        identity=StateIdentity(
            functional_hash="fp_secret_main",
            structural_hash="struct_secret_main",
        ),
        android_context=AndroidStateContext(activity_name="com.example.MainActivity"),
        evidence=StateEvidence(raw_screen_ids=["raw_screen_secret"]),
        abstraction=StateAbstraction(
            container_type=ContainerType.DYNAMIC,
            container_selector={"resource_id": "container_secret"},
            template_id="tmpl_secret",
        ),
        annotations=StateAnnotations(
            alt_text="secret alt text",
            page_function="secret page function",
            expected_actions=["secret action"],
            widget_aliases=[{"element_id": "secret_icon", "label": "secret icon label"}],
            generation_confidence=0.91,
        ),
        invariant_specs=[
            StateInvariant(
                expr="secret state invariant",
                confidence=0.84,
                source="mined_multivisit",
            )
        ],
        legacy_invariants=["secret invariant"],
    )
    s2 = AbstractState(
        state_id="s2",
        name="Detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s1",
        identity=StateIdentity(functional_hash="fp_secret_detail"),
        android_context=AndroidStateContext(activity_name="com.example.MainActivity"),
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


def test_default_output_path_uses_output_docs(sample_fsm: AppFSM, tmp_path: Path) -> None:
    fsm_path = tmp_path / "fsm.json"
    sample_fsm.serialize(fsm_path)

    assert default_output_path(fsm_path, "png") == OUTPUT_DOCS_DIR / "com_android_settings_fsm.png"
    expected_html = OUTPUT_DOCS_DIR / "com_android_settings" / "fsm.html"
    assert default_output_path(fsm_path, "html") == expected_html


def test_default_output_path_uses_model_scoped_visualization_dir() -> None:
    fsm_path = Path("output_docs/gpt-5.4-mini/vigilmarket/transition_guard/fsm.json")

    assert default_output_path(fsm_path, "html") == (
        OUTPUT_DOCS_DIR / "visualization" / "gpt-5.4-mini" / "vigilmarket.html"
    )


def test_explicit_docs_output_path_redirects_to_output_docs() -> None:
    assert resolve_generated_output_path("docs/settings_fsm.png", "ignored.png") == (
        OUTPUT_DOCS_DIR / "settings_fsm.png"
    )


def test_render_fsm_html_redacts_sensitive_fields_by_default(tmp_path: Path) -> None:
    html, payload = _render_html(_sensitive_fsm(), tmp_path)

    state = payload["states"][0]
    transition = payload["transitions"][0]

    assert set(state) == _SAFE_STATE_FIELDS
    # Abstraction is emitted but redacted: container_type / template_id /
    # template_role only — no selectors, parameter schema, or bindings.
    assert set(state["abstraction"]) == {"container_type", "template_id", "template_role"}
    assert state["abstraction"]["container_type"] == "dynamic"
    assert state["abstraction"]["template_id"] == "tmpl_secret"
    # android_context is fully present (activity/package/window are public Android metadata).
    assert state["android_context"]["activity_name"] == "com.example.MainActivity"

    assert transition == {
        "source": "s1",
        "target": "s2",
        "action": {"type": "click"},
        "confidence": 0.91,
        "observed_count": 3,
    }
    # The redacted safe view must not leak raw screen ids, annotations,
    # widget aliases, invariant specs, legacy invariants, or selector
    # parameters that can reveal capture-state / LLM-derived / fingerprint
    # information.
    for key in (
        "raw_screen_ids",
        "evidence",
        "annotations",
        "widget_aliases",
        "invariant_specs",
        "legacy_invariants",
        "container_selector",
        "parameter_schema",
        "parameter_bindings",
        "container_secret",
    ):
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

    # Nested canonical keys carry the data.
    assert state["evidence"]["raw_screen_ids"] == ["raw_screen_secret"]
    aliases = state["annotations"]["widget_aliases"]
    assert {a["label"] for a in aliases} == {"secret icon label"}
    assert {a["element_id"] for a in aliases} == {"secret_icon"}
    assert state["annotations"]["alt_text"] == "secret alt text"
    # Legacy invariants stay non-runtime; invariant_specs is the canonical
    # runtime-enforced list.
    assert state["legacy_invariants"] == ["secret invariant"]
    spec_exprs = [spec["expr"] for spec in state["invariant_specs"]]
    assert spec_exprs == ["secret state invariant"]
    assert transition["action"]["target"] == "wifi_entry"
    assert transition["action"]["target_text"] == "private network"
    assert "guard" not in transition
    assert "raw_screen_ids" in html
    assert "annotations" in html
    assert "widget_aliases" in html
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


def test_render_fsm_compare_html_includes_gold_explored_details_and_screenshots(
    tmp_path: Path,
) -> None:
    explored_fsm_path = tmp_path / "explored" / "fsm.json"
    gold_dir = tmp_path / "gold"
    screens_dir = tmp_path / "screens"
    out = tmp_path / "visualization" / "compare.html"
    explored_fsm_path.parent.mkdir()
    gold_dir.mkdir()
    screens_dir.mkdir()

    explored = _sensitive_fsm()
    explored.transitions[0].guard = _GUARD
    explored.states["s1"].invariant_specs[0].expr = 'read(screen_marker, text) == "screen:secret"'
    explored.serialize(explored_fsm_path)
    (screens_dir / "raw_screen_secret.png").write_bytes(b"not-a-real-png")

    gold_payload = {
        "app_id": "com.example.sensitive",
        "model": "gold",
        "initial_state": "home",
        "states": [
            {"id": "home", "screen_marker": "home_marker"},
            {"id": "detail", "screen_marker": "detail_marker"},
        ],
        "actions": [
            {"name": "open.detail", "type": "click", "query": {"text": "Detail"}},
            {"name": "system.back", "type": "navigate_back"},
        ],
        "transitions": [{"from": "home", "action": "open.detail", "to": "detail", "kind": "nav"}],
        "global_navigation": {
            "visible_on": ["detail"],
            "actions": [{"action": "system.back", "to": "home", "kind": "nav"}],
        },
    }
    gold_fsm_path = gold_dir / "fsm.json"
    gold_fsm_path.write_text(json.dumps(gold_payload), encoding="utf-8")

    render_fsm_compare_html(
        gold_fsm_path=gold_fsm_path,
        explored_fsm_path=explored_fsm_path,
        output_path=out,
        screens_dir=screens_dir,
    )

    html = out.read_text(encoding="utf-8")
    payload = _extract_compare_payload(html)

    assert "const COMPARE_DATA =" in html
    assert "Golden FSM" in html
    assert "Explored FSM" in html
    assert "#workbench.inspector-open" in html
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 0;" in html
    assert "#explored-panel { grid-column: 2; grid-row: 1;" in html
    assert "fitToGraph()" in html
    assert "edge-hit" in html
    assert "startNodeDrag" in html
    assert "layoutLinksFor" in html
    assert "foldLinks(rawLinks)" in html
    assert "folded identical actions" in html
    assert "transition guard Gamma DSL/Lark parsed clauses" in html
    assert "invariant logic clauses" in html
    assert payload["golden"]["summary"]["num_states"] == 2
    assert payload["golden"]["summary"]["global_nav_edges"] == 1
    assert payload["explored"]["summary"] == {"num_states": 2, "num_transitions": 1}
    assert payload["explored"]["states"][0]["invariant_specs"][0]["expr"] == (
        'read(screen_marker, text) == "screen:secret"'
    )
    assert payload["explored"]["states"][0]["invariant_specs"][0]["logic"]["status"] == "parsed"
    assert (
        payload["explored"]["states"][0]["invariant_specs"][0]["logic"]["clauses"][0][
            "predicate_type"
        ]
        == "read"
    )
    assert payload["explored"]["transitions"][0]["guard"] == _GUARD
    assert payload["explored"]["transitions"][0]["guard_logic"]["status"] == "parsed"
    assert (
        payload["explored"]["transitions"][0]["guard_logic"]["clauses"][0]["predicate_type"]
        == "read"
    )
    assert payload["explored"]["states"][0]["raw_screen_images"] == [
        {"screen_id": "raw_screen_secret", "src": "../screens/raw_screen_secret.png"}
    ]


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
