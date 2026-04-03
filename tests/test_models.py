"""Tests for data models: ContainerType, SubFsmTemplate, UIElement skeleton."""

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    SubFsmTemplate,
    Transition,
)
from vigil.models.state import RawScreen, UIElement


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


# --- ContainerType ---


class TestContainerType:
    def test_enum_values(self):
        assert ContainerType.STRUCTURAL is not None
        assert ContainerType.CONTENT is not None
        assert ContainerType.NONE is not None

    def test_enum_string_values(self):
        assert ContainerType.STRUCTURAL == "structural"
        assert ContainerType.CONTENT == "content"
        assert ContainerType.NONE == "none"


# --- SubFsmTemplate ---


class TestSubFsmTemplate:
    def test_serialization_roundtrip(self):
        template = SubFsmTemplate(
            template_id="tmpl_001",
            entry_action={"type": "click", "target": "e_0010"},
            states={
                "sub_s1": _make_state("sub_s1", name="Detail"),
                "sub_s2": _make_state("sub_s2", name="Advanced"),
            },
            transitions=[
                Transition(
                    source="sub_s1",
                    target="sub_s2",
                    action={"type": "click"},
                    observed_count=3,
                ),
            ],
            parameters=["$item.name", "$item.description"],
            source_container_state_id="s_005",
            item_skeleton_hash="abc123",
        )

        dumped = template.model_dump()
        restored = SubFsmTemplate.model_validate(dumped)

        assert restored.template_id == "tmpl_001"
        assert restored.parameters == ["$item.name", "$item.description"]
        assert restored.source_container_state_id == "s_005"
        assert restored.item_skeleton_hash == "abc123"
        assert restored.entry_action == {"type": "click", "target": "e_0010"}

    def test_nested_state_serialization(self):
        template = SubFsmTemplate(
            template_id="tmpl_002",
            entry_action={"type": "click"},
            states={"sub_s1": _make_state("sub_s1", name="WiFi Detail")},
            transitions=[],
            source_container_state_id="s_010",
            item_skeleton_hash="def456",
        )

        dumped = template.model_dump()
        assert "sub_s1" in dumped["states"]
        assert dumped["states"]["sub_s1"]["name"] == "WiFi Detail"

        restored = SubFsmTemplate.model_validate(dumped)
        assert restored.states["sub_s1"].name == "WiFi Detail"
        assert restored.states["sub_s1"].hierarchy_level == HierarchyLevel.ACTIVITY


# --- AppFSM with SubFsmTemplates ---


class TestAppFsmSubTemplates:
    def _make_template(self) -> SubFsmTemplate:
        return SubFsmTemplate(
            template_id="tmpl_001",
            entry_action={"type": "click"},
            states={"sub_s1": _make_state("sub_s1")},
            transitions=[
                Transition(source="sub_s1", target="sub_s1", action={"type": "click"}),
            ],
            source_container_state_id="s_001",
            item_skeleton_hash="hash123",
        )

    def test_add_sub_fsm_template(self):
        fsm = AppFSM("com.test.app")
        template = self._make_template()
        fsm.add_sub_fsm_template(template)

        assert "tmpl_001" in fsm.sub_fsm_templates
        assert fsm.sub_fsm_templates["tmpl_001"].item_skeleton_hash == "hash123"

    def test_serialize_deserialize_with_templates(self, tmp_path):
        fsm = AppFSM("com.test.app")
        fsm.add_state(_make_state("s_001"))
        fsm.add_sub_fsm_template(self._make_template())

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        restored = AppFSM.deserialize(path)
        assert len(restored.sub_fsm_templates) == 1
        tmpl = restored.sub_fsm_templates["tmpl_001"]
        assert tmpl.template_id == "tmpl_001"
        assert len(tmpl.states) == 1
        assert len(tmpl.transitions) == 1
        assert tmpl.source_container_state_id == "s_001"

    def test_deserialize_without_templates(self, tmp_path):
        """Old FSM JSON without sub_fsm_templates key → empty dict."""
        fsm = AppFSM("com.test.app")
        fsm.add_state(_make_state("s_001"))

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        # Simulate old format by removing the key
        import json

        data = json.loads(path.read_text())
        del data["sub_fsm_templates"]
        path.write_text(json.dumps(data))

        restored = AppFSM.deserialize(path)
        assert restored.sub_fsm_templates == {}


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


# --- RawScreen container methods ---


class TestRawScreenContainers:
    def test_find_scrollable_containers(self):
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                _make_element("e1", class_name="android.widget.RecyclerView", is_scrollable=True),
                _make_element("e2", class_name="android.widget.TextView"),
                _make_element("e3", class_name="android.widget.ListView", is_scrollable=True),
            ],
        )
        containers = screen.find_scrollable_containers()
        assert len(containers) == 2
        assert {c.element_id for c in containers} == {"e1", "e3"}

    def test_find_scrollable_containers_empty(self):
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                _make_element("e1", class_name="android.widget.Button", is_clickable=True),
            ],
        )
        assert screen.find_scrollable_containers() == []

    def test_get_container_children_via_ids(self):
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                _make_element(
                    "container",
                    class_name="android.widget.RecyclerView",
                    is_scrollable=True,
                    depth=1,
                    children=["c1", "c2"],
                ),
                _make_element("c1", class_name="android.widget.TextView", depth=2),
                _make_element("c2", class_name="android.widget.Button", depth=2),
                _make_element("unrelated", class_name="android.widget.ImageView", depth=2),
            ],
        )
        container = screen.elements[0]
        children = screen.get_container_children(container)
        assert len(children) == 2
        assert {c.element_id for c in children} == {"c1", "c2"}

    def test_get_container_children_fallback_depth(self):
        """When children list is empty, fall back to depth-based heuristic."""
        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                _make_element(
                    "container",
                    class_name="android.widget.RecyclerView",
                    is_scrollable=True,
                    depth=1,
                    children=[],
                ),
                _make_element("c1", depth=2),
                _make_element("c2", depth=2),
                _make_element("nested", depth=3),  # not direct child
            ],
        )
        container = screen.elements[0]
        children = screen.get_container_children(container)
        assert len(children) == 2
        assert {c.element_id for c in children} == {"c1", "c2"}
