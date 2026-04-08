"""Tests for data models: UIElement skeleton, AbstractState, AppFSM."""

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    HierarchyLevel,
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
