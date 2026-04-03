"""Tests for vigil.neuro.state_abstractor — container classification."""

from pathlib import Path

from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.fsm import AbstractState, AppFSM, ContainerType, HierarchyLevel
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.state_abstractor import StateAbstractor

FIXTURES = Path(__file__).parent / "fixtures"


def _make_element(element_id: str = "e_0001", **overrides) -> UIElement:
    defaults = {
        "element_id": element_id,
        "class_name": "android.widget.TextView",
    }
    defaults.update(overrides)
    return UIElement(**defaults)


def _load_fixture(name: str) -> tuple[list[UIElement], dict[str, UIElement]]:
    """Load an XML fixture, return (elements, elements_by_id)."""
    xml = (FIXTURES / name).read_text()
    elements = parse_hierarchy_xml(xml)
    by_id = {e.element_id: e for e in elements}
    return elements, by_id


def _find_scrollable(elements: list[UIElement]) -> UIElement:
    """Find the first scrollable element."""
    for e in elements:
        if e.is_scrollable:
            return e
    raise ValueError("No scrollable element found")


def _get_children(
    container: UIElement,
    elements: list[UIElement],
    by_id: dict[str, UIElement],
) -> list[UIElement]:
    """Get direct children of a container."""
    screen = RawScreen(screen_id="test", elements=elements)
    return screen.get_container_children(container)


# --- Core classification tests (XML fixtures) ---


class TestClassifyFromFixtures:
    def setup_method(self):
        self.abstractor = StateAbstractor()

    def test_classify_structural_settings(self):
        elements, by_id = _load_fixture("settings_main.xml")
        container = _find_scrollable(elements)
        children = _get_children(container, elements, by_id)
        result = self.abstractor.classify_container(container, children, by_id)

        assert result.container_type == ContainerType.STRUCTURAL
        assert result.num_children == 8
        assert result.num_unique_skeletons >= 3
        assert result.representative_skeleton is None

    def test_classify_content_homogeneous(self):
        elements, by_id = _load_fixture("wifi_saved_networks.xml")
        container = _find_scrollable(elements)
        children = _get_children(container, elements, by_id)
        result = self.abstractor.classify_container(container, children, by_id)

        assert result.container_type == ContainerType.CONTENT
        assert result.num_children == 10
        assert result.dominant_skeleton_ratio == 1.0
        assert result.representative_skeleton is not None

    def test_classify_content_heterogeneous(self):
        elements, by_id = _load_fixture("food_delivery_list.xml")
        container = _find_scrollable(elements)
        children = _get_children(container, elements, by_id)
        result = self.abstractor.classify_container(container, children, by_id)

        assert result.container_type == ContainerType.CONTENT
        assert result.num_children == 9
        assert result.dominant_skeleton_ratio >= 0.6
        assert result.representative_skeleton is not None

    def test_classify_content_with_headers(self):
        elements, by_id = _load_fixture("chat_list_with_headers.xml")
        container = _find_scrollable(elements)
        children = _get_children(container, elements, by_id)
        result = self.abstractor.classify_container(container, children, by_id)

        assert result.container_type == ContainerType.CONTENT
        assert result.representative_skeleton is not None

    def test_classify_ecommerce_mixed(self):
        elements, by_id = _load_fixture("ecommerce_home.xml")
        # Find the main RecyclerView (not the nested ViewPager)
        container = next(e for e in elements if e.is_scrollable and "RecyclerView" in e.class_name)
        children = _get_children(container, elements, by_id)
        result = self.abstractor.classify_container(container, children, by_id)

        # E-commerce home has banner + category nav + 7 product cards
        # After header stripping, product cards dominate -> CONTENT
        assert result.container_type == ContainerType.CONTENT
        assert result.num_children == 9


# --- Edge case tests ---


class TestClassifyEdgeCases:
    def setup_method(self):
        self.abstractor = StateAbstractor()

    def _make_container_with_children(
        self, n: int, skeleton_pattern: list[str] | None = None
    ) -> tuple[UIElement, list[UIElement], dict[str, UIElement]]:
        """Create a container with n children.

        skeleton_pattern: list of class_names to cycle through for children.
            If None, all children are identical TextViews.
        """
        container = _make_element(
            "container",
            class_name="androidx.recyclerview.widget.RecyclerView",
            is_scrollable=True,
            depth=0,
            children=[f"c_{i}" for i in range(n)],
        )
        children = []
        by_id: dict[str, UIElement] = {container.element_id: container}

        for i in range(n):
            cls = "android.widget.TextView"
            clickable = False
            if skeleton_pattern:
                cls = skeleton_pattern[i % len(skeleton_pattern)]
                clickable = cls == "android.widget.Button"
            child = _make_element(
                f"c_{i}",
                class_name=cls,
                is_clickable=clickable,
                depth=1,
            )
            children.append(child)
            by_id[child.element_id] = child

        return container, children, by_id

    def test_classify_empty_container(self):
        container, _, _ = self._make_container_with_children(0)
        result = self.abstractor.classify_container(container, [], None)
        assert result.container_type == ContainerType.NONE
        assert result.num_children == 0

    def test_classify_single_child(self):
        container, children, by_id = self._make_container_with_children(1)
        result = self.abstractor.classify_container(container, children, by_id)
        assert result.container_type == ContainerType.NONE

    def test_classify_two_children(self):
        container, children, by_id = self._make_container_with_children(2)
        result = self.abstractor.classify_container(container, children, by_id)
        assert result.container_type == ContainerType.NONE

    def test_classify_three_identical(self):
        container, children, by_id = self._make_container_with_children(3)
        result = self.abstractor.classify_container(container, children, by_id)
        assert result.container_type == ContainerType.CONTENT
        assert result.dominant_skeleton_ratio == 1.0


# --- Header/footer stripping tests ---


class TestStripHeadersFooters:
    def setup_method(self):
        self.abstractor = StateAbstractor()

    def test_strip_header_only(self):
        skeletons = ["H", "A", "A", "A", "A"]
        core, header, footer = self.abstractor._strip_headers_footers(skeletons)
        assert core == ["A", "A", "A", "A"]
        assert header is True
        assert footer is False

    def test_strip_footer_only(self):
        skeletons = ["A", "A", "A", "A", "F"]
        core, header, footer = self.abstractor._strip_headers_footers(skeletons)
        assert core == ["A", "A", "A", "A"]
        assert header is False
        assert footer is True

    def test_strip_both(self):
        skeletons = ["H", "A", "A", "A", "F"]
        core, header, footer = self.abstractor._strip_headers_footers(skeletons)
        assert core == ["A", "A", "A"]
        assert header is True
        assert footer is True

    def test_no_strip_when_all_different(self):
        skeletons = ["A", "B", "C", "D"]
        core, header, footer = self.abstractor._strip_headers_footers(skeletons)
        assert core == ["A", "B", "C", "D"]
        assert header is False
        assert footer is False

    def test_no_strip_when_header_appears_later(self):
        """If first item's skeleton also appears later, it's not a header."""
        skeletons = ["A", "B", "A", "B"]
        core, header, footer = self.abstractor._strip_headers_footers(skeletons)
        assert core == ["A", "B", "A", "B"]
        assert header is False
        assert footer is False

    def test_no_strip_short_list(self):
        skeletons = ["A", "B"]
        core, header, footer = self.abstractor._strip_headers_footers(skeletons)
        assert core == ["A", "B"]
        assert header is False
        assert footer is False


# --- Skeleton computation tests ---


class TestSkeletonComputation:
    def setup_method(self):
        self.abstractor = StateAbstractor()

    def test_skeleton_ignores_text(self):
        c1 = _make_element("c1", text="WiFi Network A", is_clickable=True)
        c2 = _make_element("c2", text="WiFi Network B", is_clickable=True)
        hashes = self.abstractor._compute_child_skeletons([c1, c2])
        assert hashes[0] == hashes[1]

    def test_skeleton_ignores_bounds(self):
        c1 = _make_element("c1", bounds=[0, 0, 100, 50], is_clickable=True)
        c2 = _make_element("c2", bounds=[200, 300, 400, 500], is_clickable=True)
        hashes = self.abstractor._compute_child_skeletons([c1, c2])
        assert hashes[0] == hashes[1]

    def test_skeleton_differs_on_class(self):
        c1 = _make_element("c1", class_name="android.widget.Button")
        c2 = _make_element("c2", class_name="android.widget.Switch")
        hashes = self.abstractor._compute_child_skeletons([c1, c2])
        assert hashes[0] != hashes[1]

    def test_skeleton_differs_on_children(self):
        parent1 = _make_element("p1", class_name="android.widget.LinearLayout", children=["a"])
        parent2 = _make_element("p2", class_name="android.widget.LinearLayout", children=["b"])
        child_a = _make_element("a", class_name="android.widget.TextView")
        child_b = _make_element("b", class_name="android.widget.ImageView")
        by_id = {"a": child_a, "b": child_b, "p1": parent1, "p2": parent2}
        hashes = self.abstractor._compute_child_skeletons([parent1, parent2], by_id)
        assert hashes[0] != hashes[1]

    def test_skeleton_differs_on_interactability(self):
        c1 = _make_element("c1", is_clickable=True)
        c2 = _make_element("c2", is_clickable=False)
        hashes = self.abstractor._compute_child_skeletons([c1, c2])
        assert hashes[0] != hashes[1]


# --- Segment analysis tests ---


class TestComputeSegments:
    def setup_method(self):
        self.abstractor = StateAbstractor()

    def test_segments_homogeneous(self):
        segments = self.abstractor._compute_segments(["A", "A", "A", "A"])
        assert segments == [("A", 4)]

    def test_segments_alternating(self):
        segments = self.abstractor._compute_segments(["A", "B", "A", "B"])
        assert segments == [("A", 1), ("B", 1), ("A", 1), ("B", 1)]

    def test_segments_with_large_run(self):
        segments = self.abstractor._compute_segments(["A", "B", "B", "B", "B", "B", "A"])
        assert segments == [("A", 1), ("B", 5), ("A", 1)]

    def test_segments_empty(self):
        segments = self.abstractor._compute_segments([])
        assert segments == []

    def test_segments_single(self):
        segments = self.abstractor._compute_segments(["X"])
        assert segments == [("X", 1)]


# --- Integration test: annotate_fsm_states ---


class TestAnnotateFsmStates:
    def test_annotate_fsm_states(self):
        abstractor = StateAbstractor()

        # Build a simple FSM with one state pointing to a WiFi-like screen
        fsm = AppFSM("com.test.app")
        state = AbstractState(
            state_id="s_001",
            name="WiFi List",
            fingerprint="fp_001",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            raw_screens=["scr_001"],
        )
        fsm.add_state(state)

        # Build a screen with homogeneous content
        container = _make_element(
            "rv",
            class_name="RecyclerView",
            is_scrollable=True,
            depth=0,
            children=["c0", "c1", "c2", "c3", "c4"],
        )
        children = [
            _make_element(f"c{i}", class_name="android.widget.TextView", is_clickable=True, depth=1)
            for i in range(5)
        ]
        screen = RawScreen(
            screen_id="scr_001",
            elements=[container, *children],
        )

        abstractor.annotate_fsm_states(fsm, {"scr_001": screen})

        assert fsm.states["s_001"].container_type == ContainerType.CONTENT
        assert fsm.states["s_001"].item_skeleton_hash is not None

    def test_annotate_no_container(self):
        abstractor = StateAbstractor()

        fsm = AppFSM("com.test.app")
        state = AbstractState(
            state_id="s_001",
            name="Simple Page",
            fingerprint="fp_001",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            raw_screens=["scr_001"],
        )
        fsm.add_state(state)

        screen = RawScreen(
            screen_id="scr_001",
            elements=[
                _make_element("e1", class_name="android.widget.Button", is_clickable=True),
                _make_element("e2", class_name="android.widget.TextView"),
            ],
        )

        abstractor.annotate_fsm_states(fsm, {"scr_001": screen})

        # No scrollable container -> stays NONE
        assert fsm.states["s_001"].container_type == ContainerType.NONE


# --- classify_screen_containers ---


class TestClassifyScreenContainers:
    def test_classify_screen_containers(self):
        abstractor = StateAbstractor()

        elements, by_id = _load_fixture("wifi_saved_networks.xml")
        screen = RawScreen(screen_id="test", elements=elements)
        results = abstractor.classify_screen_containers(screen)

        assert len(results) == 1
        assert results[0].container_type == ContainerType.CONTENT

    def test_classify_screen_no_containers(self):
        abstractor = StateAbstractor()

        screen = RawScreen(
            screen_id="test",
            elements=[
                _make_element("e1", class_name="android.widget.Button", is_clickable=True),
            ],
        )
        results = abstractor.classify_screen_containers(screen)
        assert results == []
