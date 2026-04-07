"""Tests for vigil.neuro.state_abstractor — container classification and sub-FSM templates."""

from pathlib import Path

from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.fsm import AbstractState, AppFSM, ContainerType, HierarchyLevel, Transition
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


# ============================================================
# Sub-FSM template tests
# ============================================================


def _build_sub_fsm_fixture():
    """Build a synthetic FSM + traces simulating a content item drill-down.

    s1 (list, CONTENT) → click item "HomeWiFi" → s2 (detail)
    s2 → click "Advanced" → s3 (advanced settings)
    s3 → back → s2
    s2 → back → s1
    s1 → click different item → s4 (same fingerprint as s2)
    """
    fsm = AppFSM("com.test.app")

    s1 = AbstractState(
        state_id="s1",
        name="WiFi List",
        fingerprint="fp_list",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=["scr_01"],
        container_type=ContainerType.CONTENT,
        item_skeleton_hash="skel_wifi",
    )
    s2 = AbstractState(
        state_id="s2",
        name="HomeWiFi Detail",
        fingerprint="fp_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        raw_screens=["scr_02"],
    )
    s3 = AbstractState(
        state_id="s3",
        name="Advanced Settings",
        fingerprint="fp_advanced",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        raw_screens=["scr_03"],
    )
    for s in (s1, s2, s3):
        fsm.add_state(s)

    transitions = [
        Transition(
            source="s1",
            target="s2",
            action={"type": "click", "target": "e_item1"},
            observed_count=1,
        ),
        Transition(
            source="s2",
            target="s3",
            action={"type": "click", "target": "e_advanced"},
            observed_count=1,
        ),
        Transition(
            source="s3",
            target="s2",
            action={"type": "navigate_back"},
            observed_count=1,
        ),
        Transition(
            source="s2",
            target="s1",
            action={"type": "navigate_back"},
            observed_count=1,
        ),
    ]
    for t in transitions:
        fsm.add_transition(t)

    # Raw traces (as they appear in the exploration JSON)
    traces = [
        {
            "step_number": 1,
            "source_screen_id": "scr_01",
            "action": {"action_type": "click", "target_element_id": "e_item1"},
            "target_screen_id": "scr_02",
        },
        {
            "step_number": 2,
            "source_screen_id": "scr_02",
            "action": {"action_type": "click", "target_element_id": "e_advanced"},
            "target_screen_id": "scr_03",
        },
        {
            "step_number": 3,
            "source_screen_id": "scr_03",
            "action": {"action_type": "navigate_back"},
            "target_screen_id": "scr_02",
        },
        {
            "step_number": 4,
            "source_screen_id": "scr_02",
            "action": {"action_type": "navigate_back"},
            "target_screen_id": "scr_01",
        },
    ]

    # Screen ID → state ID mapping
    sid_to_state_id = {
        "scr_01": "s1",
        "scr_02": "s2",
        "scr_03": "s3",
    }

    # Raw screens with element data
    screens = {
        "scr_01": {
            "screen_id": "scr_01",
            "interactable_elements": [
                {"element_id": "e_item1", "text": "HomeWiFi", "is_clickable": True},
                {"element_id": "e_item2", "text": "OfficeNet", "is_clickable": True},
            ],
        },
        "scr_02": {
            "screen_id": "scr_02",
            "interactable_elements": [
                {"element_id": "e_advanced", "text": "Advanced", "is_clickable": True},
            ],
        },
        "scr_03": {
            "screen_id": "scr_03",
            "interactable_elements": [],
        },
    }

    return fsm, traces, sid_to_state_id, screens


class TestExtractSubTree:
    def test_extract_sub_tree(self):
        fsm, traces, sid_to_state_id, _ = _build_sub_fsm_fixture()
        abstractor = StateAbstractor()

        sub_ids, sub_traces, entry_actions = abstractor._extract_sub_tree_from_traces(
            "s1", traces, sid_to_state_id
        )

        assert "s2" in sub_ids
        assert "s3" in sub_ids
        assert "s1" not in sub_ids
        assert len(sub_traces) > 0
        assert len(entry_actions) == 1
        assert entry_actions[0]["target_element_id"] == "e_item1"

    def test_extract_no_clicks(self):
        """No click traces from container → empty sub-tree."""
        abstractor = StateAbstractor()
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_01",
                "action": {"action_type": "scroll_up"},
                "target_screen_id": "scr_01",
            },
        ]
        sub_ids, _, _ = abstractor._extract_sub_tree_from_traces("s1", traces, {"scr_01": "s1"})
        assert len(sub_ids) == 0


class TestBuildSubFsmTemplate:
    def test_build_sub_fsm_template(self):
        fsm, traces, sid_to_state_id, screens = _build_sub_fsm_fixture()
        abstractor = StateAbstractor()

        templates = abstractor.build_sub_fsm_templates(fsm, traces, sid_to_state_id, screens)

        assert len(templates) == 1
        tmpl = templates[0]
        assert tmpl.source_container_state_id == "s1"
        assert "s2" in tmpl.states
        assert "s3" in tmpl.states
        assert len(tmpl.transitions) > 0
        assert tmpl.item_skeleton_hash == "skel_wifi"

    def test_parameterize_replaces_text(self):
        fsm, traces, sid_to_state_id, screens = _build_sub_fsm_fixture()
        abstractor = StateAbstractor()

        templates = abstractor.build_sub_fsm_templates(fsm, traces, sid_to_state_id, screens)

        assert len(templates) == 1
        tmpl = templates[0]
        # The state name "HomeWiFi Detail" should be parameterized
        assert "$item.name" in tmpl.parameters
        detail_state = tmpl.states.get("s2")
        assert detail_state is not None
        assert "$item.name" in detail_state.name

    def test_template_added_to_fsm(self):
        fsm, traces, sid_to_state_id, screens = _build_sub_fsm_fixture()
        abstractor = StateAbstractor()

        templates = abstractor.build_sub_fsm_templates(fsm, traces, sid_to_state_id, screens)

        assert len(fsm.sub_fsm_templates) == 1
        tmpl_id = templates[0].template_id
        assert tmpl_id in fsm.sub_fsm_templates
        assert fsm.states["s1"].sub_fsm_template_id == tmpl_id

    def test_no_content_states(self):
        """FSM with no CONTENT states → no templates."""
        fsm = AppFSM("com.test.app")
        state = AbstractState(
            state_id="s1",
            name="Settings",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            container_type=ContainerType.STRUCTURAL,
        )
        fsm.add_state(state)
        abstractor = StateAbstractor()

        templates = abstractor.build_sub_fsm_templates(fsm, [], {}, {})
        assert templates == []

    def test_multiple_items_same_structure(self):
        """Two items clicked from same container → one template (not two)."""
        fsm, traces, sid_to_state_id, screens = _build_sub_fsm_fixture()

        # Add a second item click leading to s2 (same structure)
        # s4 has the same fingerprint as s2
        s4 = AbstractState(
            state_id="s4",
            name="OfficeNet Detail",
            fingerprint="fp_detail",
            hierarchy_level=HierarchyLevel.FRAGMENT,
            raw_screens=["scr_04"],
        )
        fsm.add_state(s4)
        fsm.add_transition(
            Transition(
                source="s1",
                target="s4",
                action={"type": "click", "target": "e_item2"},
                observed_count=1,
            )
        )
        fsm.add_transition(
            Transition(
                source="s4",
                target="s1",
                action={"type": "navigate_back"},
                observed_count=1,
            )
        )

        # Add traces for second item
        traces.extend(
            [
                {
                    "step_number": 5,
                    "source_screen_id": "scr_01",
                    "action": {"action_type": "click", "target_element_id": "e_item2"},
                    "target_screen_id": "scr_04",
                },
                {
                    "step_number": 6,
                    "source_screen_id": "scr_04",
                    "action": {"action_type": "navigate_back"},
                    "target_screen_id": "scr_01",
                },
            ]
        )
        sid_to_state_id["scr_04"] = "s4"

        abstractor = StateAbstractor()
        templates = abstractor.build_sub_fsm_templates(fsm, traces, sid_to_state_id, screens)

        # Only one template for s1 (even though two items were explored)
        assert len(templates) == 1


class TestMajorityVotePreference:
    """Tie-breaking: prefer CONTENT over STRUCTURAL."""

    def test_tie_breaks_to_content(self):
        """When votes are tied, CONTENT wins over STRUCTURAL."""
        abstractor = StateAbstractor()
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="MixedPage",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            raw_screens=["scr_a", "scr_b"],
        )
        fsm.add_state(s1)

        # scr_a: 4 children all same skeleton → CONTENT (num_unique==1)
        children_a = [
            _make_element(f"a_{i}", class_name="android.widget.LinearLayout", is_clickable=True)
            for i in range(4)
        ]
        container_a = _make_element(
            "cont_a",
            class_name="RecyclerView",
            is_scrollable=True,
            children=[c.element_id for c in children_a],
        )
        screen_a = RawScreen(screen_id="scr_a", elements=[container_a, *children_a])

        # scr_b: 4 diverse children → STRUCTURAL (num_unique >=3, len <8)
        children_b = [
            _make_element("b_0", class_name="android.widget.Switch", is_clickable=True),
            _make_element("b_1", class_name="android.widget.Button", is_clickable=True),
            _make_element("b_2", class_name="android.widget.TextView", is_clickable=True),
            _make_element("b_3", class_name="android.widget.CheckBox", is_clickable=True),
        ]
        container_b = _make_element(
            "cont_b",
            class_name="RecyclerView",
            is_scrollable=True,
            children=[c.element_id for c in children_b],
        )
        screen_b = RawScreen(screen_id="scr_b", elements=[container_b, *children_b])

        abstractor.annotate_fsm_states(fsm, {"scr_a": screen_a, "scr_b": screen_b})
        assert s1.container_type == ContainerType.CONTENT


class TestClassifyMixedContainer:
    """Mixed container with switch + homogeneous clickable items + button."""

    def test_classify_switch_plus_list_items_plus_button(self):
        """1 Switch + 5 identical clickable TextViews + 1 Button → CONTENT."""
        abstractor = StateAbstractor()

        switch = _make_element("sw", class_name="android.widget.Switch", is_clickable=True)
        items = [
            _make_element(f"item_{i}", class_name="android.widget.LinearLayout", is_clickable=True)
            for i in range(5)
        ]
        button = _make_element("btn", class_name="android.widget.Button", is_clickable=True)

        container = _make_element(
            "rv",
            class_name="RecyclerView",
            is_scrollable=True,
            children=[switch.element_id, *[it.element_id for it in items], button.element_id],
        )
        all_elements = [container, switch, *items, button]
        elements_by_id = {e.element_id: e for e in all_elements}

        result = abstractor.classify_container(container, [switch, *items, button], elements_by_id)
        assert result.container_type == ContainerType.CONTENT
