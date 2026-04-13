"""Tests for data models: UIElement skeleton, AbstractState, AppFSM."""

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    StateSemanticProfile,
    SubFsmTemplate,
    Transition,
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
        assert state.container_type == ContainerType.NONE
        assert state.container_resource_id is None

    def test_explicit_container_type(self):
        state = _make_state(
            "s_001",
            container_type=ContainerType.DYNAMIC,
            container_resource_id="com.app:id/wifi_list",
        )
        assert state.container_type == ContainerType.DYNAMIC
        assert state.container_resource_id == "com.app:id/wifi_list"

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
        assert restored.states["s_001"].container_type == ContainerType.DYNAMIC
        assert restored.states["s_001"].container_resource_id == "com.app:id/list"
        assert restored.states["s_002"].container_type == ContainerType.STATIC
        assert restored.states["s_002"].container_resource_id is None

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
        assert restored.states["s1"].container_type == ContainerType.NONE
        assert restored.states["s1"].container_resource_id is None


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
        assert state.semantic_profile is not None
        assert state.semantic_profile.page_function == "settings/wifi"

    def test_abstract_state_defaults_none(self):
        state = _make_state("s_001")
        assert state.semantic_profile is None
        assert state.state_invariants == []
        assert state.invariant_confidence == 0.0
        assert state.sub_fsm_template_id is None


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
        assert len(state.state_invariants) == 2
        assert state.invariant_confidence == 0.85

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
        assert s.semantic_profile is not None
        assert s.semantic_profile.alt_text == "Home screen"
        assert s.semantic_profile.generation_confidence == 0.9
        assert s.state_invariants == ['read(title, text) != ""']
        assert s.invariant_confidence == 0.9


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

        restored = AppFSM.deserialize(path)
        assert restored.states["s_list"].sub_fsm_template_id == "tmpl_1"
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
        assert restored.states["s1"].semantic_profile is None
        assert restored.states["s1"].state_invariants == []
