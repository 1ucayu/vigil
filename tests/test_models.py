"""Tests for data models: UIElement skeleton, AbstractState, AppFSM."""

import json

import pytest

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
    StateKind,
    StateSemanticProfile,
    SubFsmTemplate,
    Transition,
    canonical_action_key,
)
from vigil.models.state import UIElement


def _make_element(element_id: str = "e_0001", **overrides) -> UIElement:
    """Helper to create UIElement with sensible defaults."""
    defaults = {
        "element_id": element_id,
        "class_name": "android.widget.TextView",
    }
    defaults.update(overrides)
    return UIElement(**defaults)


def _make_state(state_id: str = "s_001", **overrides) -> AbstractState:
    """Helper to create AbstractState with sensible defaults."""
    defaults = {
        "state_id": state_id,
        "name": f"State {state_id}",
        "fingerprint": f"fp_{state_id}",
        "hierarchy_level": HierarchyLevel.ACTIVITY,
    }
    defaults.update(overrides)
    return AbstractState(**defaults)


_NESTED_STATE_KEYS = (
    "identity",
    "android_context",
    "evidence",
    "abstraction",
    "invariant_specs",
    "annotations",
    "legacy_invariants",
)

_LEGACY_FLAT_STATE_KEYS = (
    "fingerprint",
    "structural_fingerprint",
    "activity_name",
    "raw_screens",
    "container_type",
    "container_resource_id",
    "sub_fsm_template_id",
    "semantic_profile",
    "state_invariants",
    "invariant_confidence",
    "invariants",
)


def _assert_state_payload_is_nested_only(state_payload: dict) -> None:
    """Schema v4 contract: nested submodels present, legacy flat keys absent.

    Use on any serialized AbstractState dict, including nested template
    states inside ``sub_fsm_templates[*].states``.
    """
    for nested in _NESTED_STATE_KEYS:
        assert nested in state_payload, f"missing nested key: {nested}"
    for flat in _LEGACY_FLAT_STATE_KEYS:
        assert flat not in state_payload, f"unexpected legacy flat key: {flat}"


# --- UIElement.get_skeleton ---


class TestUIElementSkeleton:
    def test_same_hash_different_text(self):
        e1 = _make_element("e1", text="WiFi Network A", is_clickable=True)
        e2 = _make_element("e2", text="WiFi Network B", is_clickable=True)
        assert e1.get_skeleton() == e2.get_skeleton()

    def test_different_hash_different_structure(self):
        e1 = _make_element("e1", class_name="android.widget.Button", is_clickable=True)
        e2 = _make_element("e2", class_name="android.widget.CheckBox", is_checkable=True)
        assert e1.get_skeleton() != e2.get_skeleton()

    def test_skeleton_without_elements_by_id(self):
        e = _make_element("e1", children=["c1", "c2"])
        skeleton = e.get_skeleton()
        # Without elements_by_id, child_skeletons should be empty tuple
        assert skeleton[-1] == ()

    def test_skeleton_with_elements_by_id(self):
        parent = _make_element(
            "parent",
            class_name="android.widget.LinearLayout",
            children=["c1", "c2"],
        )
        c1 = _make_element("c1", class_name="android.widget.TextView", text="Item 1")
        c2 = _make_element("c2", class_name="android.widget.Button", is_clickable=True)

        elements_by_id = {"c1": c1, "c2": c2}
        skeleton = parent.get_skeleton(elements_by_id)

        # Last element should be a tuple of 2 sorted child skeletons
        child_skeletons = skeleton[-1]
        assert len(child_skeletons) == 2

        # Children with different structure should produce different skeletons
        assert child_skeletons[0] != child_skeletons[1]

    def test_skeleton_excludes_content_properties(self):
        e1 = _make_element(
            "e1",
            text="Hello",
            content_description="Greeting",
            bounds=[0, 0, 100, 50],
            is_clickable=True,
        )
        e2 = _make_element(
            "e2",
            text="Goodbye",
            content_description="Farewell",
            bounds=[200, 300, 400, 500],
            is_clickable=True,
        )
        assert e1.get_skeleton() == e2.get_skeleton()


class TestGroupingSkeleton:
    def test_same_class_depth_interactability(self):
        e1 = _make_element("e1", class_name="Button", is_clickable=True, depth=3)
        e2 = _make_element("e2", class_name="Button", is_clickable=True, depth=3)
        assert e1.get_grouping_skeleton() == e2.get_grouping_skeleton()

    def test_different_resource_id_same_skeleton(self):
        e1 = _make_element("e1", class_name="Button", resource_id="id/btn_0", depth=3)
        e2 = _make_element("e2", class_name="Button", resource_id="id/btn_9", depth=3)
        assert e1.get_grouping_skeleton() == e2.get_grouping_skeleton()

    def test_different_class_different_skeleton(self):
        e1 = _make_element("e1", class_name="Button", depth=3)
        e2 = _make_element("e2", class_name="Switch", depth=3)
        assert e1.get_grouping_skeleton() != e2.get_grouping_skeleton()

    def test_different_depth_different_skeleton(self):
        e1 = _make_element("e1", class_name="Button", depth=3)
        e2 = _make_element("e2", class_name="Button", depth=5)
        assert e1.get_grouping_skeleton() != e2.get_grouping_skeleton()

    def test_parent_id_defaults_none(self):
        e = _make_element("e1")
        assert e.parent_id is None


# --- AppFSM serialization ---


class TestAppFsmSerialization:
    def test_serialize_deserialize_roundtrip(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(_make_state("s_001", name="Home"))
        fsm.add_state(_make_state("s_002", name="Settings"))
        fsm.add_transition(Transition(source="s_001", target="s_002", action={"type": "click"}))
        fsm.initial_state = "s_001"

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        restored = AppFSM.deserialize(path)
        assert len(restored.states) == 2
        assert len(restored.transitions) == 1
        assert restored.initial_state == "s_001"
        assert restored.states["s_001"].name == "Home"


class TestActionIdentityMatching:
    def _identity_fsm(self) -> AppFSM:
        fsm = AppFSM("com.test.app")
        fsm.add_state(_make_state("s1"))
        fsm.add_state(_make_state("s2"))
        fsm.add_state(_make_state("s3"))
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={
                    "type": "click",
                    "resource_id": "com.app:id/a",
                    "target_resource_id": "com.app:id/a",
                    "target_text": "Alpha",
                    "target_content_desc": "Open Alpha",
                    "target_class": "android.widget.Button",
                    "target_class_name": "android.widget.Button",
                    "target_selector": {
                        "resource_id": "com.app:id/a",
                        "text": "Alpha",
                        "content_description": "Open Alpha",
                        "class_name": "android.widget.Button",
                        "bounds": [0, 0, 10, 10],
                    },
                    "target": "e_a",
                },
            )
        )
        fsm.add_transition(
            Transition(
                source="s1",
                target="s3",
                action={
                    "type": "click",
                    "resource_id": "com.app:id/b",
                    "target_resource_id": "com.app:id/b",
                    "target_text": "Beta",
                    "target_content_desc": "Open Beta",
                    "target_class": "android.widget.Button",
                    "target_class_name": "android.widget.Button",
                    "target_selector": {
                        "resource_id": "com.app:id/b",
                        "text": "Beta",
                        "content_description": "Open Beta",
                        "class_name": "android.widget.Button",
                        "bounds": [20, 0, 30, 10],
                    },
                    "target": "e_b",
                },
            )
        )
        return fsm

    def test_canonical_action_key_normalizes_aliases_and_selector(self) -> None:
        a = {
            "type": "click",
            "resource_id": "com.app:id/a",
            "target_class_name": "android.widget.Button",
            "target_selector": {
                "resource_id": "com.app:id/a",
                "class_name": "android.widget.Button",
                "bounds": [0, 0, 10, 10],
                "depth": 2,
            },
        }
        b = {
            "type": "click",
            "target_resource_id": "com.app:id/a",
            "target_class": "android.widget.Button",
            "target_selector": {
                "resource_id": "com.app:id/a",
                "class_name": "android.widget.Button",
                "bounds": [100, 100, 200, 200],
                "depth": 5,
            },
        }
        assert canonical_action_key(a) == canonical_action_key(b)

    def test_keyed_match_uses_resource_id_not_type_only(self) -> None:
        fsm = self._identity_fsm()
        assert (
            fsm.get_transition_target("s1", {"type": "click", "resource_id": "com.app:id/a"})
            == "s2"
        )
        assert (
            fsm.get_transition_target("s1", {"type": "click", "resource_id": "com.app:id/b"})
            == "s3"
        )
        assert (
            fsm.get_transition_target("s1", {"type": "click", "resource_id": "com.app:id/c"})
            is None
        )

    def test_type_only_non_global_action_is_uncertain_when_identity_is_required(self) -> None:
        fsm = self._identity_fsm()
        assert fsm.is_valid_transition("s1", {"type": "click"}) is None
        assert fsm.get_transition("s1", {"type": "click"}) is None


# --- ContainerType ---


class TestContainerType:
    def test_enum_values(self):
        assert ContainerType.STATIC == "static"
        assert ContainerType.DYNAMIC == "dynamic"
        assert ContainerType.NONE == "none"
        assert set(ContainerType) == {
            ContainerType.STATIC,
            ContainerType.DYNAMIC,
            ContainerType.NONE,
        }

    def test_default_container_type(self):
        state = _make_state("s_001")
        assert state.abstraction.container_type == ContainerType.NONE
        assert state.abstraction.container_selector.get("resource_id") is None

    def test_explicit_container_type(self):
        state = _make_state(
            "s_001",
            container_type=ContainerType.DYNAMIC,
            container_resource_id="com.app:id/wifi_list",
        )
        assert state.abstraction.container_type == ContainerType.DYNAMIC
        assert state.abstraction.container_selector.get("resource_id") == "com.app:id/wifi_list"

    def test_serialization_roundtrip(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(
            _make_state(
                "s_001",
                container_type=ContainerType.DYNAMIC,
                container_resource_id="com.app:id/list",
            )
        )
        fsm.add_state(_make_state("s_002", container_type=ContainerType.STATIC))
        fsm.initial_state = "s_001"

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        restored = AppFSM.deserialize(path)
        assert restored.states["s_001"].abstraction.container_type == ContainerType.DYNAMIC
        assert (
            restored.states["s_001"].abstraction.container_selector.get("resource_id")
            == "com.app:id/list"
        )
        assert restored.states["s_002"].abstraction.container_type == ContainerType.STATIC
        assert restored.states["s_002"].abstraction.container_selector.get("resource_id") is None

    def test_backward_compat_missing_fields(self, tmp_path):
        """FSM JSON without container fields still deserializes (fields get defaults)."""
        import json

        data = {
            "app_package": "com.test.app",
            "version": "0.1.0",
            "initial_state": "s1",
            "states": {
                "s1": {
                    "state_id": "s1",
                    "name": "Home",
                    "fingerprint": "abc123",
                    "hierarchy_level": "activity",
                }
            },
            "transitions": [],
        }
        path = tmp_path / "old_fsm.json"
        path.write_text(json.dumps(data))

        restored = AppFSM.deserialize(path)
        assert restored.states["s1"].abstraction.container_type == ContainerType.NONE
        assert restored.states["s1"].abstraction.container_selector.get("resource_id") is None


# --- StateSemanticProfile ---


class TestStateSemanticProfile:
    def test_defaults(self):
        profile = StateSemanticProfile()
        assert profile.alt_text == ""
        assert profile.page_function == ""
        assert profile.expected_actions == []
        assert profile.icon_labels == {}
        assert profile.generation_confidence == 0.0

    def test_populated(self):
        profile = StateSemanticProfile(
            alt_text="WiFi network list showing available networks",
            page_function="settings/wifi/list",
            expected_actions=["connect_to_wifi", "forget_network"],
            icon_labels={"e_0042": "settings_gear", "e_0043": "info_icon"},
            generation_confidence=0.95,
        )
        assert profile.page_function == "settings/wifi/list"
        assert len(profile.icon_labels) == 2
        assert profile.icon_labels["e_0042"] == "settings_gear"

    def test_on_abstract_state(self):
        profile = StateSemanticProfile(
            alt_text="WiFi list",
            page_function="settings/wifi",
        )
        state = _make_state("s_001", semantic_profile=profile)
        assert state.annotations.page_function == "settings/wifi"

    def test_abstract_state_defaults_none(self):
        state = _make_state("s_001")
        assert state.annotations.alt_text == "" and state.annotations.page_function == ""
        assert state.invariant_specs == []
        assert max((s.confidence for s in state.invariant_specs), default=0.0) == 0.0
        assert state.abstraction.template_id is None


# --- State invariants on AbstractState ---


class TestStateInvariants:
    def test_state_invariants_populated(self):
        state = _make_state(
            "s_001",
            state_invariants=[
                "count(recycler_view) > 0",
                'read(action_bar_title, text) != ""',
            ],
            invariant_confidence=0.85,
        )
        assert len(state.invariant_specs) == 2
        assert max((s.confidence for s in state.invariant_specs), default=0.0) == 0.85

    def test_serialization_roundtrip_with_invariants(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(
            _make_state(
                "s_001",
                semantic_profile=StateSemanticProfile(
                    alt_text="Home screen",
                    page_function="home",
                    generation_confidence=0.9,
                ),
                state_invariants=['read(title, text) != ""'],
                invariant_confidence=0.9,
            )
        )
        fsm.initial_state = "s_001"

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        restored = AppFSM.deserialize(path)
        s = restored.states["s_001"]
        assert s.annotations.alt_text == "Home screen"
        assert s.annotations.generation_confidence == 0.9
        assert [spec.expr for spec in s.invariant_specs] == ['read(title, text) != ""']
        assert max((spec.confidence for spec in s.invariant_specs), default=0.0) == 0.9


# --- SubFsmTemplate ---


class TestSubFsmTemplate:
    def test_basic_template(self):
        tmpl = SubFsmTemplate(
            template_id="tmpl_wifi_detail",
            source_state_id="s_wifi_list",
            entry_fingerprint="fp_detail",
            parameter_schema={"ssid": "string", "security": "string"},
            item_skeleton="sk_wifi_item",
        )
        assert tmpl.template_id == "tmpl_wifi_detail"
        assert tmpl.parameter_schema["ssid"] == "string"
        assert tmpl.states == {}
        assert tmpl.transitions == []

    def test_template_with_states_and_transitions(self):
        detail_state = _make_state("tmpl_s1", name="DetailView")
        confirm_state = _make_state("tmpl_s2", name="ConfirmDialog")
        tmpl = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
            states={"tmpl_s1": detail_state, "tmpl_s2": confirm_state},
            transitions=[
                Transition(
                    source="tmpl_s1",
                    target="tmpl_s2",
                    action={"type": "click"},
                )
            ],
        )
        assert len(tmpl.states) == 2
        assert len(tmpl.transitions) == 1

    def test_appfsm_sub_fsm_templates(self):
        fsm = AppFSM("com.test.app")
        tmpl = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
        )
        fsm.sub_fsm_templates[tmpl.template_id] = tmpl
        assert "tmpl_1" in fsm.sub_fsm_templates

    def test_serialization_roundtrip_with_templates(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(
            _make_state(
                "s_list",
                container_type=ContainerType.DYNAMIC,
                sub_fsm_template_id="tmpl_1",
            )
        )
        fsm.initial_state = "s_list"

        detail = _make_state("tmpl_s1", name="Detail")
        tmpl = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
            states={"tmpl_s1": detail},
            transitions=[Transition(source="tmpl_s1", target="tmpl_s1", action={"type": "click"})],
            parameter_schema={"item_name": "string"},
            item_skeleton="sk_item",
        )
        fsm.sub_fsm_templates["tmpl_1"] = tmpl

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        payload = json.loads(path.read_text())
        template_state = payload["sub_fsm_templates"]["tmpl_1"]["states"]["tmpl_s1"]
        # Template states honour the same nested-only schema-v4 contract.
        _assert_state_payload_is_nested_only(template_state)
        assert template_state["identity"]["functional_hash"] == "fp_tmpl_s1"

        restored = AppFSM.deserialize(path)
        assert restored.states["s_list"].abstraction.template_id == "tmpl_1"
        assert "tmpl_1" in restored.sub_fsm_templates
        rt = restored.sub_fsm_templates["tmpl_1"]
        assert rt.source_state_id == "s_list"
        assert rt.entry_fingerprint == "fp_detail"
        assert "tmpl_s1" in rt.states
        assert rt.states["tmpl_s1"].name == "Detail"
        assert len(rt.transitions) == 1
        assert rt.parameter_schema == {"item_name": "string"}
        assert rt.item_skeleton == "sk_item"

    def test_backward_compat_no_templates(self, tmp_path):
        """Old FSM JSON without sub_fsm_templates still deserializes."""
        import json

        data = {
            "app_package": "com.test.app",
            "version": "0.1.0",
            "initial_state": "s1",
            "states": {
                "s1": {
                    "state_id": "s1",
                    "name": "Home",
                    "fingerprint": "abc123",
                    "hierarchy_level": "activity",
                }
            },
            "transitions": [],
        }
        path = tmp_path / "old_fsm.json"
        path.write_text(json.dumps(data))

        restored = AppFSM.deserialize(path)
        assert restored.sub_fsm_templates == {}
        assert restored.states["s1"].annotations.alt_text == ""
        assert restored.states["s1"].invariant_specs == []


# --- Nested schema migration ---


class TestNestedStateSchema:
    def test_old_flat_kwargs_populate_nested_views(self):
        profile = StateSemanticProfile(
            alt_text="alt",
            page_function="home",
            icon_labels={"e1": "gear"},
            generation_confidence=0.9,
        )
        state = AbstractState(
            state_id="s1",
            name="Home",
            fingerprint="fp_home",
            structural_fingerprint="struct_home",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name="com.example.HomeActivity",
            raw_screens=["raw_001", "raw_002"],
            container_type=ContainerType.DYNAMIC,
            container_resource_id="com.app:id/list",
            sub_fsm_template_id="tmpl_detail",
            state_invariants=['read(title, text) != ""'],
            invariant_confidence=0.9,
            semantic_profile=profile,
        )

        # Flat aliases route to the same nested storage.
        assert state.fingerprint == "fp_home"
        assert state.structural_fingerprint == "struct_home"
        assert state.raw_screens == ["raw_001", "raw_002"]
        assert state.container_type == ContainerType.DYNAMIC
        assert state.container_resource_id == "com.app:id/list"
        assert state.sub_fsm_template_id == "tmpl_detail"
        assert state.state_invariants == ['read(title, text) != ""']
        assert state.invariant_confidence == 0.9

        # Nested canonical fields hold the data exactly once.
        assert isinstance(state.identity, StateIdentity)
        assert state.identity.functional_hash == "fp_home"
        assert state.identity.structural_hash == "struct_home"
        assert state.kind == StateKind.NORMAL
        assert isinstance(state.android_context, AndroidStateContext)
        assert state.android_context.activity_name == "com.example.HomeActivity"
        assert isinstance(state.evidence, StateEvidence)
        assert state.evidence.raw_screen_ids == ["raw_001", "raw_002"]
        assert state.evidence.observation_count == 2
        assert isinstance(state.abstraction, StateAbstraction)
        assert state.abstraction.container_type == ContainerType.DYNAMIC
        assert state.abstraction.container_selector == {"resource_id": "com.app:id/list"}
        assert state.abstraction.template_id == "tmpl_detail"
        assert isinstance(state.annotations, StateAnnotations)
        # ``display_name`` is annotation-only and must not mirror state.name.
        assert state.annotations.display_name == ""
        assert state.annotations.alt_text == "alt"
        assert state.annotations.page_function == "home"
        # icon_labels is rebuilt as widget_aliases with element_id/label keys.
        assert state.annotations.widget_aliases == [{"element_id": "e1", "label": "gear"}]
        assert len(state.invariant_specs) == 1
        assert state.invariant_specs[0].expr == 'read(title, text) != ""'
        assert state.invariant_specs[0].confidence == 0.9
        assert state.invariant_specs[0].source == "mined_multivisit"

    def test_dialog_kind_from_hierarchy_level(self):
        state = _make_state("s_dlg", hierarchy_level=HierarchyLevel.COMPONENT)
        assert state.kind == StateKind.DIALOG
        state2 = _make_state("s_act", hierarchy_level=HierarchyLevel.ACTIVITY)
        assert state2.kind == StateKind.NORMAL

    def test_explicit_kind_is_preserved(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.COMPONENT,
            kind=StateKind.ERROR,
        )
        assert state.kind == StateKind.ERROR
        # Round-trip via dump
        restored = AbstractState(**state.model_dump())
        assert restored.kind == StateKind.ERROR

    def test_kind_syncs_with_hierarchy_level(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            kind=StateKind.NORMAL,
        )
        state.hierarchy_level = HierarchyLevel.COMPONENT
        assert state.model_dump(mode="json")["kind"] == StateKind.DIALOG.value

        explicit = AbstractState(
            state_id="s2",
            name="dialog override",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            kind=StateKind.DIALOG,
        )
        explicit.hierarchy_level = HierarchyLevel.FRAGMENT
        assert explicit.model_dump(mode="json")["kind"] == StateKind.DIALOG.value

    def test_annotations_display_name_does_not_override_state_name(self):
        state = AbstractState(
            state_id="s1",
            name="CanonicalName",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            annotations={"display_name": "PrettyName"},
        )
        assert state.name == "CanonicalName"
        assert state.annotations.display_name == "PrettyName"

    def test_legacy_invariants_kwarg_stays_out_of_canonical_store(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            invariants=["legacy_expr"],
        )
        # Legacy invariants are preserved for readers, but must not become
        # runtime-enforced state_invariants — otherwise verifier verdicts
        # would silently change for old FSMs.
        assert state.legacy_invariants == ["legacy_expr"]
        assert state.invariants == ["legacy_expr"]
        assert state.state_invariants == []
        assert state.invariant_specs == []

    def test_dual_canonical_and_legacy_lists_stay_separate(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            state_invariants=["new_expr"],
            invariants=["legacy_expr"],
            invariant_confidence=0.7,
        )
        assert state.state_invariants == ["new_expr"]
        assert state.invariants == ["legacy_expr"]
        assert state.legacy_invariants == ["legacy_expr"]

    def test_setter_state_invariants_updates_specs(self):
        state = _make_state("s1")
        state.state_invariants = ["a", "b"]
        state.invariant_confidence = 0.8
        assert {s.expr for s in state.invariant_specs} == {"a", "b"}
        assert all(s.confidence == 0.8 for s in state.invariant_specs)
        # The legacy alias setter writes only to the legacy store.
        state.invariants = ["c"]
        assert state.invariants == ["c"]
        assert state.legacy_invariants == ["c"]
        assert {s.expr for s in state.invariant_specs} == {"a", "b"}

    def test_state_invariants_source_with_and_without_confidence(self):
        without = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            state_invariants=["a"],
        )
        assert without.invariant_specs[0].source == "unknown"
        assert without.invariant_specs[0].confidence == 0.0

        withc = AbstractState(
            state_id="s2",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            state_invariants=["a"],
            invariant_confidence=0.42,
        )
        assert withc.invariant_specs[0].source == "mined_multivisit"
        assert withc.invariant_specs[0].confidence == 0.42

    def test_no_legacy_flat_source_string(self):
        # No code path should ever stamp 'legacy_flat' as the spec source.
        states = [
            AbstractState(
                state_id="s1",
                name="x",
                fingerprint="fp",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                state_invariants=["a"],
            ),
            AbstractState(
                state_id="s2",
                name="x",
                fingerprint="fp",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                state_invariants=["a"],
                invariant_confidence=0.3,
            ),
        ]
        for state in states:
            for spec in state.invariant_specs:
                assert spec.source != "legacy_flat"

    def test_conflicting_invariant_specs_and_flat_mirror_raise(self):
        with pytest.raises(ValueError, match="invariant_specs"):
            AbstractState(
                state_id="s1",
                name="x",
                fingerprint="fp",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                invariant_specs=[{"expr": "a"}],
                state_invariants=["b"],
            )

    def test_matching_invariant_specs_and_flat_mirror_load_cleanly(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            invariant_specs=[{"expr": "a", "confidence": 0.6}],
            state_invariants=["a"],
            invariant_confidence=0.6,
        )
        assert state.state_invariants == ["a"]
        assert len(state.invariant_specs) == 1
        assert state.invariant_specs[0].confidence == 0.6

    def test_invariant_confidence_agreement_compares_against_max(self):
        # Brief constraint #2: invariant_confidence agreement is checked
        # against max(spec.confidence), not every spec's confidence.
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            invariant_specs=[
                {"expr": "a", "confidence": 0.6},
                {"expr": "b", "confidence": 0.9},
            ],
            invariant_confidence=0.9,
        )
        assert state.invariant_confidence == 0.9
        assert {s.confidence for s in state.invariant_specs} == {0.6, 0.9}

    def test_construct_from_nested_kwargs(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            identity={"functional_hash": "fp_nested", "structural_hash": "struct_nested"},
            android_context={"activity_name": "Act"},
            evidence={"raw_screen_ids": ["raw_a"]},
            abstraction={
                "container_type": ContainerType.STATIC,
                "container_selector": {"resource_id": "id/list"},
                "template_id": "tmpl",
            },
        )
        assert state.fingerprint == "fp_nested"
        assert state.structural_fingerprint == "struct_nested"
        assert state.activity_name == "Act"
        assert state.raw_screens == ["raw_a"]
        assert state.container_type == ContainerType.STATIC
        assert state.container_resource_id == "id/list"
        assert state.sub_fsm_template_id == "tmpl"

    def test_nested_metadata_survives_model_dump_reload(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            identity={
                "functional_hash": "fp_nested",
                "structural_hash": "struct_nested",
                "algorithm": "custom_identity_v2",
            },
            evidence={
                "raw_screen_ids": ["raw_a"],
                "trust_level": "inferred",
            },
            abstraction={
                "container_type": ContainerType.DYNAMIC,
                "container_selector": {"resource_id": "id/list"},
                "template_id": "tmpl",
                "template_role": "template_member",
            },
        )

        dumped = state.model_dump()
        restored = AbstractState(**dumped)

        assert restored.identity.algorithm == "custom_identity_v2"
        assert restored.evidence.observation_count == 1
        assert restored.evidence.trust_level == "inferred"
        assert restored.abstraction.template_role == "template_member"

    def test_alias_mutation_propagates_to_nested(self):
        state = _make_state("s1", raw_screens=["a"])
        state.fingerprint = "fp_new"
        state.structural_fingerprint = "struct_new"
        state.activity_name = "Activity2"
        state.container_type = ContainerType.STATIC
        state.container_resource_id = "id/cont"
        state.sub_fsm_template_id = "tmpl2"
        # Aliases should hit the nested canonical fields.
        assert state.identity.functional_hash == "fp_new"
        assert state.identity.structural_hash == "struct_new"
        assert state.android_context.activity_name == "Activity2"
        assert state.abstraction.container_type == ContainerType.STATIC
        assert state.abstraction.container_selector["resource_id"] == "id/cont"
        assert state.abstraction.template_id == "tmpl2"

    def test_nested_mutation_visible_via_alias(self):
        state = _make_state("s1")
        state.identity.functional_hash = "fp_via_nested"
        state.android_context.activity_name = "AnotherActivity"
        state.abstraction.container_type = ContainerType.DYNAMIC
        state.abstraction.container_selector["resource_id"] = "id/cont"
        state.abstraction.template_id = "tmpl_via_nested"
        state.evidence.raw_screen_ids.append("r1")
        assert state.fingerprint == "fp_via_nested"
        assert state.activity_name == "AnotherActivity"
        assert state.container_type == ContainerType.DYNAMIC
        assert state.container_resource_id == "id/cont"
        assert state.sub_fsm_template_id == "tmpl_via_nested"
        assert state.raw_screens == ["r1"]

    def test_raw_screens_extend_updates_observation_count(self):
        state = _make_state("s1", raw_screens=["a"])
        assert state.evidence.observation_count == 1
        state.raw_screens.extend(["b", "c"])
        assert state.raw_screens == ["a", "b", "c"]
        # observation_count is derived: never stale after raw_screens mutation.
        assert state.evidence.observation_count == 3

    def test_container_resource_id_setter_to_none_removes_key(self):
        state = _make_state("s1", container_resource_id="id/old")
        assert state.abstraction.container_selector == {"resource_id": "id/old"}
        state.container_resource_id = None
        assert "resource_id" not in state.abstraction.container_selector

    def test_old_flat_fsm_json_deserializes(self, tmp_path):
        """A pre-refactor FSM JSON file (schema v2 flat keys) still loads."""
        import json

        data = {
            "app_package": "com.test.app",
            "version": "0.1.0",
            "schema_version": "2",
            "initial_state": "s1",
            "states": {
                "s1": {
                    "state_id": "s1",
                    "name": "Home",
                    "fingerprint": "fp_home",
                    "structural_fingerprint": "struct_home",
                    "hierarchy_level": "activity",
                    "activity_name": "com.example.HomeActivity",
                    "raw_screens": ["raw_001"],
                    "container_type": "dynamic",
                    "container_resource_id": "com.app:id/list",
                    "sub_fsm_template_id": "tmpl_detail",
                    "invariants": ["legacy expr"],
                    "state_invariants": ['read(title, text) != ""'],
                    "invariant_confidence": 0.85,
                    "semantic_profile": {
                        "alt_text": "alt",
                        "page_function": "home",
                        "expected_actions": ["go"],
                        "icon_labels": {"e1": "gear"},
                        "generation_confidence": 0.9,
                    },
                }
            },
            "transitions": [],
        }
        path = tmp_path / "old_fsm.json"
        path.write_text(json.dumps(data))

        restored = AppFSM.deserialize(path)
        s = restored.states["s1"]
        assert s.fingerprint == "fp_home"
        assert s.identity.functional_hash == "fp_home"
        assert s.identity.structural_hash == "struct_home"
        assert s.android_context.activity_name == "com.example.HomeActivity"
        assert s.evidence.raw_screen_ids == ["raw_001"]
        assert s.abstraction.container_type == ContainerType.DYNAMIC
        assert s.abstraction.template_id == "tmpl_detail"
        # icon_labels round-trips via widget_aliases (element_id/label).
        assert {a["label"] for a in s.annotations.widget_aliases} == {"gear"}
        assert s.semantic_profile is not None
        assert s.semantic_profile.icon_labels == {"e1": "gear"}
        # Canonical and legacy invariant lists are preserved separately.
        assert s.state_invariants == ['read(title, text) != ""']
        assert s.invariants == ["legacy expr"]
        assert s.legacy_invariants == ["legacy expr"]

    def test_mixed_flat_and_nested_agreeing_values_load(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp_x",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            identity={"functional_hash": "fp_x", "structural_hash": "struct_x"},
            structural_fingerprint="struct_x",
            raw_screens=["r1"],
            evidence={"raw_screen_ids": ["r1"]},
            container_type=ContainerType.DYNAMIC,
            abstraction={"container_type": "dynamic", "template_id": "tmpl"},
            sub_fsm_template_id="tmpl",
        )
        assert state.fingerprint == "fp_x"
        assert state.structural_fingerprint == "struct_x"
        assert state.raw_screens == ["r1"]
        assert state.container_type == ContainerType.DYNAMIC
        assert state.sub_fsm_template_id == "tmpl"

    def test_mixed_flat_and_nested_conflict_raises(self):
        with pytest.raises(ValueError, match="fingerprint"):
            AbstractState(
                state_id="s1",
                name="x",
                fingerprint="fp_A",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                identity={"functional_hash": "fp_B"},
            )

    def test_explicit_matching_observation_count_loads(self):
        state = AbstractState(
            state_id="s1",
            name="x",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            evidence={"raw_screen_ids": ["a", "b"], "observation_count": 2},
        )
        assert state.evidence.observation_count == 2

    def test_explicit_mismatching_observation_count_raises(self):
        with pytest.raises(ValueError, match="observation_count"):
            AbstractState(
                state_id="s1",
                name="x",
                fingerprint="fp",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                evidence={"raw_screen_ids": ["a"], "observation_count": 7},
            )

    def test_serialized_json_is_nested_only(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(
            _make_state(
                "s1",
                structural_fingerprint="struct_s1",
                activity_name="com.test.MainActivity",
                raw_screens=["raw_a", "raw_b"],
                container_type=ContainerType.DYNAMIC,
                container_resource_id="id/list",
                sub_fsm_template_id="tmpl",
                semantic_profile=StateSemanticProfile(
                    alt_text="alt",
                    page_function="home",
                    expected_actions=["tap"],
                    icon_labels={"e1": "gear"},
                    generation_confidence=0.8,
                ),
                state_invariants=["e1", "e2"],
                invariants=["legacy e"],
                invariant_confidence=0.6,
            )
        )
        fsm.initial_state = "s1"
        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        payload = json.loads(path.read_text())
        assert payload["schema_version"] == "4"
        state_payload = payload["states"]["s1"]

        _assert_state_payload_is_nested_only(state_payload)

        # Nested submodel contents are correct.
        assert state_payload["identity"]["functional_hash"] == "fp_s1"
        assert state_payload["identity"]["structural_hash"] == "struct_s1"
        assert state_payload["android_context"]["activity_name"] == "com.test.MainActivity"
        assert state_payload["evidence"]["raw_screen_ids"] == ["raw_a", "raw_b"]
        assert state_payload["abstraction"]["container_type"] == "dynamic"
        assert state_payload["abstraction"]["container_selector"] == {"resource_id": "id/list"}
        assert state_payload["abstraction"]["template_id"] == "tmpl"
        assert [spec["expr"] for spec in state_payload["invariant_specs"]] == ["e1", "e2"]
        assert max(spec["confidence"] for spec in state_payload["invariant_specs"]) == 0.6
        # icon_labels round-trips into annotations.widget_aliases.
        widget_labels = {a["label"] for a in state_payload["annotations"]["widget_aliases"]}
        assert "gear" in widget_labels
        assert state_payload["annotations"]["alt_text"] == "alt"
        assert state_payload["annotations"]["page_function"] == "home"
        assert state_payload["annotations"]["expected_actions"] == ["tap"]
        # legacy_invariants stays as the one intentional non-runtime survivor.
        assert state_payload["legacy_invariants"] == ["legacy e"]

    def test_serialize_round_trip_preserves_aliases(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(
            _make_state(
                "s1",
                structural_fingerprint="struct_s1",
                raw_screens=["raw_a", "raw_b"],
                container_type=ContainerType.DYNAMIC,
                container_resource_id="id/list",
                sub_fsm_template_id="tmpl",
                state_invariants=["e1", "e2"],
                invariants=["legacy e"],
                invariant_confidence=0.6,
            )
        )
        fsm.initial_state = "s1"
        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        restored = AppFSM.deserialize(path)
        s = restored.states["s1"]
        assert s.fingerprint == "fp_s1"
        assert s.structural_fingerprint == "struct_s1"
        assert s.raw_screens == ["raw_a", "raw_b"]
        assert s.container_type == ContainerType.DYNAMIC
        assert s.container_resource_id == "id/list"
        assert s.sub_fsm_template_id == "tmpl"
        assert s.state_invariants == ["e1", "e2"]
        assert s.invariant_confidence == 0.6
        assert s.invariants == ["legacy e"]

    def test_state_invariant_model_basic(self):
        inv = StateInvariant(expr="x > 0", confidence=0.8, source="manual")
        assert inv.expr == "x > 0"
        assert inv.confidence == 0.8
        assert inv.source == "manual"
        assert inv.evidence_count == 0


class TestSchemaVersionCompatibility:
    def test_loads_schema_v2_flat_bundle(self, tmp_path):
        data = {
            "app_package": "com.test.app",
            "version": "0.1.0",
            "schema_version": "2",
            "initial_state": "s1",
            "states": {
                "s1": {
                    "state_id": "s1",
                    "name": "Home",
                    "fingerprint": "fp_home",
                    "structural_fingerprint": "struct_home",
                    "hierarchy_level": "activity",
                    "activity_name": "com.example.HomeActivity",
                    "raw_screens": ["raw_001"],
                    "container_type": "dynamic",
                    "container_resource_id": "com.app:id/list",
                    "sub_fsm_template_id": "tmpl_detail",
                    "state_invariants": ['read(title, text) != ""'],
                    "invariant_confidence": 0.85,
                    "invariants": ["legacy expr"],
                    "semantic_profile": {
                        "alt_text": "alt",
                        "page_function": "home",
                        "expected_actions": ["go"],
                        "icon_labels": {"e1": "gear"},
                        "generation_confidence": 0.9,
                    },
                }
            },
            "transitions": [],
        }
        path = tmp_path / "schema_v2_fsm.json"
        path.write_text(json.dumps(data))

        restored = AppFSM.deserialize(path)

        s = restored.states["s1"]
        assert s.identity.functional_hash == "fp_home"
        assert s.identity.structural_hash == "struct_home"
        assert s.android_context.activity_name == "com.example.HomeActivity"
        assert s.evidence.raw_screen_ids == ["raw_001"]
        assert s.abstraction.container_type == ContainerType.DYNAMIC
        assert s.abstraction.container_selector == {"resource_id": "com.app:id/list"}
        assert s.abstraction.template_id == "tmpl_detail"
        assert s.state_invariants == ['read(title, text) != ""']
        assert s.invariant_confidence == 0.85
        assert s.legacy_invariants == ["legacy expr"]
        assert s.annotations.page_function == "home"

    def test_loads_schema_v3_mixed_bundle(self, tmp_path):
        data = {
            "app_package": "com.test.app",
            "version": "0.1.0",
            "schema_version": "3",
            "initial_state": "s1",
            "states": {
                "s1": {
                    "state_id": "s1",
                    "name": "Home",
                    "hierarchy_level": "activity",
                    "identity": {
                        "functional_hash": "fp_home",
                        "structural_hash": "struct_home",
                    },
                    "android_context": {"activity_name": "com.example.HomeActivity"},
                    "evidence": {"raw_screen_ids": ["raw_001"], "observation_count": 1},
                    "abstraction": {
                        "container_type": "dynamic",
                        "container_selector": {"resource_id": "com.app:id/list"},
                        "template_id": "tmpl_detail",
                    },
                    "invariant_specs": [
                        {
                            "expr": 'read(title, text) != ""',
                            "confidence": 0.85,
                            "source": "mined_multivisit",
                        }
                    ],
                    "annotations": {
                        "alt_text": "alt",
                        "page_function": "home",
                        "expected_actions": ["go"],
                        "widget_aliases": [{"element_id": "e1", "label": "gear"}],
                        "generation_confidence": 0.9,
                    },
                    "legacy_invariants": ["legacy expr"],
                    "fingerprint": "fp_home",
                    "structural_fingerprint": "struct_home",
                    "activity_name": "com.example.HomeActivity",
                    "raw_screens": ["raw_001"],
                    "container_type": "dynamic",
                    "container_resource_id": "com.app:id/list",
                    "sub_fsm_template_id": "tmpl_detail",
                    "state_invariants": ['read(title, text) != ""'],
                    "invariant_confidence": 0.85,
                    "invariants": ["legacy expr"],
                    "semantic_profile": {
                        "alt_text": "alt",
                        "page_function": "home",
                        "expected_actions": ["go"],
                        "icon_labels": {"e1": "gear"},
                        "generation_confidence": 0.9,
                    },
                }
            },
            "transitions": [],
        }
        path = tmp_path / "schema_v3_fsm.json"
        path.write_text(json.dumps(data))

        restored = AppFSM.deserialize(path)

        s = restored.states["s1"]
        assert s.identity.functional_hash == "fp_home"
        assert s.identity.structural_hash == "struct_home"
        assert s.android_context.activity_name == "com.example.HomeActivity"
        assert s.evidence.raw_screen_ids == ["raw_001"]
        assert s.abstraction.container_type == ContainerType.DYNAMIC
        assert s.abstraction.template_id == "tmpl_detail"
        assert s.state_invariants == ['read(title, text) != ""']
        assert s.invariants == ["legacy expr"]

    def test_loads_schema_v4_nested_only_bundle(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(
            _make_state(
                "s1",
                structural_fingerprint="struct_s1",
                activity_name="com.test.MainActivity",
                raw_screens=["raw_a"],
                state_invariants=["read(title, text) != ''"],
                invariant_confidence=0.7,
            )
        )
        fsm.initial_state = "s1"
        path = tmp_path / "schema_v4_fsm.json"

        fsm.serialize(path)

        payload = json.loads(path.read_text())
        assert payload["schema_version"] == "4"
        _assert_state_payload_is_nested_only(payload["states"]["s1"])

        restored = AppFSM.deserialize(path)
        assert restored.initial_state == "s1"
        assert restored.states["s1"].identity.functional_hash == "fp_s1"
        assert restored.states["s1"].android_context.activity_name == "com.test.MainActivity"

    def test_unknown_schema_version_raises(self, tmp_path):
        data = {
            "app_package": "com.test.app",
            "version": "0.1.0",
            "schema_version": "99",
            "states": {},
            "transitions": [],
        }
        path = tmp_path / "unknown_schema_fsm.json"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="schema_version '99'"):
            AppFSM.deserialize(path)

    def test_raw_json_consumers_see_nested_state_keys(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(_make_state("s1"))
        fsm.add_state(_make_state("s2", raw_screens=["raw_002"]))
        path = tmp_path / "fsm.json"

        fsm.serialize(path)

        payload = json.loads(path.read_text())
        assert payload["schema_version"] == "4"
        for state_payload in payload["states"].values():
            for key in _NESTED_STATE_KEYS:
                assert key in state_payload
