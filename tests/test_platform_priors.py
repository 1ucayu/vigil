"""Tests for vigil.core.platform_priors — Android platform prior loading."""

from vigil.core.platform_priors import (
    get_dialog_indicators,
    get_error_patterns,
    get_guard_template,
    get_tab_indicators,
    get_widget_templates,
)


class TestWidgetTemplates:
    def test_loads_all_templates(self) -> None:
        templates = get_widget_templates()
        assert len(templates) >= 35

    def test_switch_template(self) -> None:
        t = get_guard_template("Switch")
        assert t is not None
        assert "is_checked" in (t["correctness"] or "")

    def test_fully_qualified_name(self) -> None:
        t = get_guard_template("android.widget.Switch")
        assert t is not None
        assert t == get_guard_template("Switch")

    def test_switchcompat(self) -> None:
        t = get_guard_template("androidx.appcompat.widget.SwitchCompat")
        assert t is not None
        assert "is_checked" in (t["correctness"] or "")

    def test_unknown_widget(self) -> None:
        assert get_guard_template("com.custom.MyFancyWidget") is None

    def test_fab_no_correctness(self) -> None:
        t = get_guard_template("FloatingActionButton")
        assert t is not None
        assert t["correctness"] is None


class TestDialogIndicators:
    def test_has_classes(self) -> None:
        indicators = get_dialog_indicators()
        assert "AlertDialog" in indicators["classes"]
        assert "TimePicker" in indicators["classes"]

    def test_has_resource_ids(self) -> None:
        indicators = get_dialog_indicators()
        assert "android:id/button1" in indicators["resource_ids"]


class TestTabIndicators:
    def test_has_bottom_nav(self) -> None:
        tabs = get_tab_indicators()
        assert "BottomNavigationView" in tabs
        assert "TabLayout" in tabs


class TestErrorPatterns:
    def test_has_patterns(self) -> None:
        patterns = get_error_patterns()
        assert any("isn't responding" in p for p in patterns)
