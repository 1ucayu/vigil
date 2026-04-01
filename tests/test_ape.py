"""Tests for APE integration: XML parser, action-history parser, output parser."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vigil.core.ape_ui_parser import parse_ape_bounds, parse_ape_xml
from vigil.models.action import ActionType
from vigil.neuro.ape_parser import ApeOutputParser

FIXTURES = Path(__file__).parent / "fixtures"

# Load sample APE XML fixtures
APE_XML = (FIXTURES / "ape_sample_step.xml").read_text()
APE_XML_WITH_SYSUI = (FIXTURES / "ape_sysui_step.xml").read_text()


class TestParseApeXml:
    def test_basic_parsing(self) -> None:
        elements = parse_ape_xml(APE_XML)
        assert len(elements) > 0

    def test_direct_node_root(self) -> None:
        """APE XML has <node> as root, not <hierarchy>."""
        elements = parse_ape_xml(APE_XML)
        # Should include the root FrameLayout + children
        class_names = [e.class_name for e in elements]
        assert "android.widget.FrameLayout" in class_names
        assert "android.widget.TextView" in class_names

    def test_no_bounds(self) -> None:
        """APE XML has no bounds attribute — all should default to [0,0,0,0]."""
        elements = parse_ape_xml(APE_XML)
        for e in elements:
            assert e.bounds == [0, 0, 0, 0]

    def test_scroll_type_mapping(self) -> None:
        """scroll-type != 'none' should set is_scrollable."""
        # The RecyclerView in the sample has scrollable="true"
        elements = parse_ape_xml(APE_XML)
        recycler = [e for e in elements if "RecyclerView" in e.class_name]
        assert len(recycler) == 1
        assert recycler[0].is_scrollable is True

    def test_clickable_elements(self) -> None:
        elements = parse_ape_xml(APE_XML)
        clickable = [e for e in elements if e.is_clickable]
        assert len(clickable) >= 3  # Wi-Fi, Bluetooth, search bar, title

    def test_editable_detection(self) -> None:
        elements = parse_ape_xml(APE_XML)
        editable = [e for e in elements if e.is_editable]
        assert len(editable) == 1
        assert editable[0].class_name == "android.widget.EditText"

    def test_element_ids_sequential(self) -> None:
        elements = parse_ape_xml(APE_XML)
        ids = [e.element_id for e in elements]
        # IDs should contain e_0000, e_0001, etc.
        for i in range(len(ids)):
            assert f"e_{i:04d}" in ids

    def test_system_ui_filtering(self) -> None:
        """System UI elements should be filtered when app_package is set."""
        all_elements = parse_ape_xml(APE_XML_WITH_SYSUI)
        filtered = parse_ape_xml(APE_XML_WITH_SYSUI, app_package="com.android.settings")

        assert len(all_elements) == 4  # root + title + status_bar + clock
        assert len(filtered) == 2  # root + title (system UI filtered)

    def test_empty_xml(self) -> None:
        assert parse_ape_xml("") == []
        assert parse_ape_xml("  ") == []

    def test_invalid_xml(self) -> None:
        assert parse_ape_xml("<not valid") == []

    def test_text_preserved_as_is(self) -> None:
        """APE may truncate text, but we store whatever is in the XML."""
        elements = parse_ape_xml(APE_XML)
        bt = [e for e in elements if e.text == "Bluetoo"]
        assert len(bt) == 1  # truncated text preserved


class TestParseApeBounds:
    def test_comma_format(self) -> None:
        assert parse_ape_bounds("100,200,300,400") == [100, 200, 300, 400]

    def test_bracket_format(self) -> None:
        assert parse_ape_bounds("[100,200][300,400]") == [100, 200, 300, 400]

    def test_empty(self) -> None:
        assert parse_ape_bounds("") == [0, 0, 0, 0]

    def test_invalid(self) -> None:
        assert parse_ape_bounds("invalid") == [0, 0, 0, 0]


class TestApeOutputParser:
    @pytest.fixture
    def ape_output_dir(self, tmp_path: Path) -> Path:
        """Create a mock APE output directory with sample files."""
        out = tmp_path / "ape_output"
        out.mkdir()

        # Copy sample XML as multiple steps
        sample_xml = (FIXTURES / "ape_sample_step.xml").read_text()
        for i in range(6):
            (out / f"step-{i}.xml").write_text(sample_xml)
            (out / f"step-{i}.png").write_text("")  # dummy PNG

        # Copy action history
        shutil.copy(FIXTURES / "ape_action_history.log", out / "action-history.log")

        return out

    def test_parse_screens(self, ape_output_dir: Path) -> None:
        parser = ApeOutputParser(ape_output_dir, "com.android.settings")
        result = parser.parse()
        # All 6 steps have same XML so only 1 unique screen
        assert result.unique_screens == 1
        assert result.total_steps == 6

    def test_parse_traces(self, ape_output_dir: Path) -> None:
        parser = ApeOutputParser(ape_output_dir, "com.android.settings")
        result = parser.parse()
        # action-history has: EVENT_START(skip), CLICK(1), BACK(2), CLICK(3), SCROLL(4), CRASH(skip)
        # Traces need both source and target step → steps 1→2, 2→3, 3→4, 4→5
        assert len(result.traces) == 4

    def test_action_type_mapping(self, ape_output_dir: Path) -> None:
        parser = ApeOutputParser(ape_output_dir, "com.android.settings")
        result = parser.parse()
        action_types = [t.action.action_type for t in result.traces]
        assert ActionType.CLICK in action_types
        assert ActionType.NAVIGATE_BACK in action_types
        assert ActionType.SCROLL_DOWN in action_types

    def test_bounds_enrichment(self, ape_output_dir: Path) -> None:
        parser = ApeOutputParser(ape_output_dir, "com.android.settings")
        result = parser.parse()
        # The CLICK action on android:id/title should enrich bounds
        for screen in result.screens.values():
            title_els = [e for e in screen.elements if e.resource_id == "android:id/title"]
            # At least one should have non-zero bounds from action log enrichment
            enriched = [e for e in title_els if e.bounds != [0, 0, 0, 0]]
            assert len(enriched) >= 1

    def test_crash_actions_skipped(self, ape_output_dir: Path) -> None:
        parser = ApeOutputParser(ape_output_dir, "com.android.settings")
        result = parser.parse()
        # PHANTOM_CRASH should not appear in traces
        for trace in result.traces:
            assert trace.action.action_type != "PHANTOM_CRASH"

    def test_empty_directory(self, tmp_path: Path) -> None:
        parser = ApeOutputParser(tmp_path, "com.android.settings")
        result = parser.parse()
        assert result.unique_screens == 0
        assert result.total_steps == 0
