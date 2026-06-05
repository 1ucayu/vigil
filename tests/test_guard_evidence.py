"""Tests for the per-transition guard evidence view (guard generation, step 2).

Covers building evidence for a simple two-state transition, resolving the action
target to a stable registry alias, sibling-action collection, preservation of replay
confidence / low-trust / provenance, the deterministic diff summary, and graceful
degradation when raw screens are missing. No LLM, DSL compilation, or admission logic.
"""

from __future__ import annotations

from pathlib import Path

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    HierarchyLevel,
    ProvenanceEntry,
    Transition,
)
from vigil.neuro.guard_evidence import (
    build_all_guard_evidence,
    build_guard_evidence_for_transition,
)

PKG = "com.test.app"


def _el(element_id: str, **overrides) -> dict:
    base = {
        "element_id": element_id,
        "class_name": "android.view.View",
        "resource_id": "",
        "text": "",
        "content_description": "",
        "is_clickable": False,
        "is_enabled": True,
    }
    base.update(overrides)
    return base


def _screen(screen_id: str, *elements: dict) -> dict:
    return {
        "screen_id": screen_id,
        "activity_name": f"{PKG}.MainActivity",
        "package_name": PKG,
        "interactable_elements": list(elements),
    }


def _screen_with_artifacts(
    screen_id: str,
    *,
    screenshot_path: str = "",
    xml_tree_path: str = "",
    compact_tree_text: str = "",
    elements: list[dict] | None = None,
) -> dict:
    screen = _screen(screen_id, *(elements or []))
    if screenshot_path:
        screen["screenshot_path"] = screenshot_path
    if xml_tree_path:
        screen["xml_tree_path"] = xml_tree_path
    if compact_tree_text:
        screen["compact_tree_text"] = compact_tree_text
    return screen


def _state(state_id: str, name: str, screen_id: str, page_function: str = "") -> AbstractState:
    state = AbstractState(
        state_id=state_id,
        name=name,
        fingerprint=f"fp_{state_id}",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=[screen_id],
        activity_name=f"{PKG}.MainActivity",
    )
    if page_function:
        state.annotations.page_function = page_function
    return state


def _two_state_fsm() -> tuple[AppFSM, dict[str, dict]]:
    fsm = AppFSM(app_package=PKG)
    fsm.add_state(_state("s1", "Compose", "scr_s1", page_function="compose_message"))
    fsm.add_state(_state("s2", "Sent", "scr_s2", page_function="message_sent"))
    fsm.initial_state = "s1"

    src_screen = _screen(
        "scr_s1",
        _el(
            "e_send",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/send",
            text="Send",
            is_clickable=True,
        ),
        _el(
            "e_cancel",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/cancel",
            text="Cancel",
            is_clickable=True,
        ),
    )
    tgt_screen = _screen(
        "scr_s2",
        _el(
            "e_status",
            class_name="android.widget.TextView",
            resource_id=f"{PKG}:id/status",
            text="Delivered",
        ),
    )
    raw_screens = {"scr_s1": src_screen, "scr_s2": tgt_screen}

    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={
                "type": "click",
                "target": "e_send",
                "target_resource_id": f"{PKG}:id/send",
                "target_text": "Send",
            },
            confidence=0.83,
            low_trust=True,
            observed_count=4,
            provenance=[
                ProvenanceEntry(
                    trace_step_index=7,
                    source_screen_id="scr_s1",
                    target_screen_id="scr_s2",
                    confidence_source="observed",
                )
            ],
        )
    )
    # Sibling outgoing transition from the same source state.
    fsm.add_transition(
        Transition(
            source="s1",
            target="s1",
            action={"type": "click", "target": "e_cancel"},
            confidence=0.5,
        )
    )
    return fsm, raw_screens


def test_builds_evidence_for_two_state_transition():
    fsm, raw_screens = _two_state_fsm()
    transition = fsm.transitions[0]

    ev = build_guard_evidence_for_transition(fsm, transition, 0, raw_screens)

    assert ev.transition_index == 0
    assert ev.source_state_id == "s1"
    assert ev.target_state_id == "s2"
    assert ev.source_state_name == "Compose"
    assert ev.target_state_name == "Sent"
    assert ev.source_page_function == "compose_message"
    assert ev.target_page_function == "message_sent"
    assert "send" in ev.source_registry.entries
    assert ev.target_registry is not None
    assert "status" in ev.target_registry.entries


def test_resolves_action_target_alias_from_element_id():
    fsm, raw_screens = _two_state_fsm()
    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, raw_screens)
    # e_send -> "send" alias via element_id_to_alias.
    assert ev.action_target_alias == "send"


def test_includes_sibling_actions_excluding_current():
    fsm, raw_screens = _two_state_fsm()
    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, raw_screens)

    assert len(ev.sibling_actions) == 1
    assert ev.sibling_actions[0]["target"] == "e_cancel"


def test_preserves_confidence_low_trust_and_provenance():
    fsm, raw_screens = _two_state_fsm()
    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, raw_screens)

    assert ev.replay_confidence == 0.83
    assert ev.low_trust is True
    assert len(ev.provenance) == 1
    assert ev.provenance[0]["trace_step_index"] == 7
    assert ev.provenance[0]["source_screen_id"] == "scr_s1"


def test_builds_hoare_screen_evidence_from_trace_and_annotations(tmp_path: Path):
    fsm, raw_screens = _two_state_fsm()
    source_xml = tmp_path / "source.xml"
    target_xml = tmp_path / "target.xml"
    source_xml.write_text('<node resource-id="com.test.app:id/send" text="Send" />')
    target_xml.write_text('<node resource-id="com.test.app:id/status" text="Delivered" />')
    raw_screens["scr_s1"] = _screen_with_artifacts(
        "scr_s1",
        screenshot_path="data/screens/source.png",
        xml_tree_path=str(source_xml),
        compact_tree_text='[c_0001] Button send ;click; text="Send"',
        elements=[
            _el(
                "e_send",
                class_name="android.widget.Button",
                resource_id=f"{PKG}:id/send",
                text="Send",
                is_clickable=True,
            )
        ],
    )
    raw_screens["scr_s2"] = _screen_with_artifacts(
        "scr_s2",
        screenshot_path="data/screens/target.png",
        xml_tree_path=str(target_xml),
        compact_tree_text='[c_0001] TextView status ;; text="Delivered"',
        elements=[
            _el(
                "e_status",
                class_name="android.widget.TextView",
                resource_id=f"{PKG}:id/status",
                text="Delivered",
            )
        ],
    )
    fsm.states["s1"].annotations.alt_text = "Source screen has composer controls."
    fsm.states["s2"].annotations.alt_text = "Target screen shows delivered status."

    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, raw_screens)

    assert ev.source_screen_ids == ["scr_s1"]
    assert ev.target_screen_ids == ["scr_s2"]
    assert ev.source_screen.screenshot_path == "data/screens/source.png"
    assert ev.source_screen.xml_tree_path == str(source_xml)
    assert "Button send" in ev.source_screen.compact_tree_text
    assert 'resource-id="com.test.app:id/send"' in ev.source_screen.xml_excerpt
    assert ev.source_screen.alt_text == "Source screen has composer controls."
    assert ev.target_screen.screenshot_path == "data/screens/target.png"
    assert "Delivered" in ev.target_screen.xml_excerpt
    assert ev.target_screen.alt_text == "Target screen shows delivered status."


def test_diff_summary_reports_changed_text_and_checked():
    fsm = AppFSM(app_package=PKG)
    fsm.add_state(_state("s1", "Before", "scr_s1"))
    fsm.add_state(_state("s2", "After", "scr_s2"))

    src_screen = _screen(
        "scr_s1",
        _el(
            "e_t",
            class_name="android.widget.TextView",
            resource_id=f"{PKG}:id/title",
            text="Wi-Fi off",
        ),
        _el(
            "e_sw",
            class_name="android.widget.Switch",
            resource_id=f"{PKG}:id/sw",
            is_clickable=True,
        ),
    )
    tgt_screen = _screen(
        "scr_s2",
        _el(
            "e_t",
            class_name="android.widget.TextView",
            resource_id=f"{PKG}:id/title",
            text="Wi-Fi on",
        ),
        _el(
            "e_sw",
            class_name="android.widget.Switch",
            resource_id=f"{PKG}:id/sw",
            is_clickable=True,
            is_checkable=True,
            is_checked=True,
        ),
    )
    raw_screens = {"scr_s1": src_screen, "scr_s2": tgt_screen}
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click", "target": "e_sw"},
            confidence=0.9,
        )
    )

    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, raw_screens)

    assert f"{PKG}:id/title.text:" in ev.diff_summary
    assert f"{PKG}:id/sw.is_checked:changed" in ev.diff_summary


def test_diff_summary_reports_added_and_removed_resource_ids():
    fsm = AppFSM(app_package=PKG)
    fsm.add_state(_state("s1", "Before", "scr_s1"))
    fsm.add_state(_state("s2", "After", "scr_s2"))
    src_screen = _screen(
        "scr_s1",
        _el("e_a", class_name="android.widget.Button", resource_id=f"{PKG}:id/a"),
    )
    tgt_screen = _screen(
        "scr_s2",
        _el("e_b", class_name="android.widget.Button", resource_id=f"{PKG}:id/b"),
    )
    raw_screens = {"scr_s1": src_screen, "scr_s2": tgt_screen}
    fsm.add_transition(
        Transition(source="s1", target="s2", action={"type": "click"}, confidence=1.0)
    )

    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, raw_screens)
    assert f"+{PKG}:id/b" in ev.diff_summary
    assert f"-{PKG}:id/a" in ev.diff_summary


def test_missing_raw_screens_yield_partial_evidence_without_crashing():
    fsm, _ = _two_state_fsm()
    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, {})

    assert ev.source_state_id == "s1"
    assert ev.source_registry.entries == {}
    assert ev.target_registry is not None
    assert ev.target_registry.entries == {}
    assert ev.action_target_alias is None
    assert ev.diff_summary == ""
    # Confidence / provenance still preserved from the transition itself.
    assert ev.replay_confidence == 0.83
    assert len(ev.provenance) == 1


def test_missing_states_yield_partial_evidence():
    fsm = AppFSM(app_package=PKG)
    # Transition references states that are not in fsm.states.
    fsm.add_transition(Transition(source="ghost1", target="ghost2", action={"type": "click"}))
    ev = build_guard_evidence_for_transition(fsm, fsm.transitions[0], 0, {})

    assert ev.source_state_id == "ghost1"
    assert ev.source_state_name == ""
    assert ev.source_registry.state_id == "ghost1"
    assert ev.source_registry.entries == {}
    assert ev.target_registry is None


def test_build_all_guard_evidence_indexes_every_transition():
    fsm, raw_screens = _two_state_fsm()
    evidence = build_all_guard_evidence(fsm, raw_screens)

    assert len(evidence) == len(fsm.transitions)
    assert [e.transition_index for e in evidence] == [0, 1]


def _multi_screen_fsm() -> tuple[AppFSM, dict[str, dict]]:
    """Source state spanning two raw screens; only the second holds the target handle."""
    fsm = AppFSM(app_package=PKG)
    s1 = AbstractState(
        state_id="s1",
        name="List",
        fingerprint="fp_s1",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=["scrA", "scrB"],
        activity_name=f"{PKG}.MainActivity",
    )
    fsm.add_state(s1)
    fsm.add_state(_state("s2", "Detail", "scr_t"))
    fsm.initial_state = "s1"

    scr_a = _screen(
        "scrA",
        _el(
            "e_other",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/other",
            text="Other",
            is_clickable=True,
        ),
    )
    scr_b = _screen(
        "scrB",
        _el(
            "e_target",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/go",
            text="Go",
            is_clickable=True,
        ),
    )
    raw_screens = {"scrA": scr_a, "scrB": scr_b, "scr_t": _screen("scr_t")}
    return fsm, raw_screens


def test_provenance_screen_selection_resolves_target_handle():
    fsm, raw_screens = _multi_screen_fsm()
    transition = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "e_target"},
        confidence=0.9,
        provenance=[ProvenanceEntry(source_screen_id="scrB", target_screen_id="scr_t")],
    )
    fsm.add_transition(transition)

    ev = build_guard_evidence_for_transition(fsm, transition, 0, raw_screens)

    # Provenance points at scrB, which holds the capture-local handle e_target.
    assert ev.source_registry.screen_id == "scrB"
    assert ev.action_target_alias == "go"
    assert ev.action_target_alias_reason == "matched:element_id"


def test_without_provenance_first_screen_lacks_handle():
    fsm, raw_screens = _multi_screen_fsm()
    transition = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "e_target"},
        confidence=0.9,
    )
    fsm.add_transition(transition)

    ev = build_guard_evidence_for_transition(fsm, transition, 0, raw_screens)

    # No provenance -> falls back to the state's first raw screen (scrA), which lacks
    # the target handle, so the alias cannot resolve.
    assert ev.source_registry.screen_id == "scrA"
    assert ev.action_target_alias is None


def test_ambiguous_target_text_does_not_bind_arbitrarily():
    fsm = AppFSM(app_package=PKG)
    fsm.add_state(_state("s1", "Dialog", "scr1"))
    fsm.add_state(_state("s2", "Next", "scr2"))
    fsm.initial_state = "s1"

    scr1 = _screen(
        "scr1",
        _el("e_ok1", class_name="android.widget.Button", text="OK", is_clickable=True),
        _el("e_ok2", class_name="android.widget.Button", text="OK", is_clickable=True),
    )
    raw_screens = {"scr1": scr1, "scr2": _screen("scr2")}
    # Action carries only the ambiguous label, no stable handle/resource id.
    transition = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target_text": "OK"},
        confidence=0.9,
    )
    fsm.add_transition(transition)

    ev = build_guard_evidence_for_transition(fsm, transition, 0, raw_screens)

    assert ev.action_target_alias is None
    assert "ambiguous" in ev.action_target_alias_reason
