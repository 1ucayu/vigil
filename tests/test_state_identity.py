"""Tests for text-anchored functional state identity on RawScreen.

Fixtures live in tests/fixtures/settings/ and are committed to the repo so the
tests run in CI and on fresh clones. The fixtures correspond to three Android
Settings subpages captured from an emulator.
"""

from __future__ import annotations

from pathlib import Path

from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.state import EMPTY_SCREEN_ID, RawScreen, UIElement

APP_PKG = "com.android.settings"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "settings"


def _load(name: str) -> RawScreen:
    xml = (FIXTURE_DIR / name).read_text()
    elements = parse_hierarchy_xml(xml, app_package=APP_PKG)
    return RawScreen(screen_id=name.replace(".xml", ""), elements=elements)


def _replace_title_text(screen: RawScreen, old: str, new: str) -> RawScreen:
    new_elements = []
    for e in screen.elements:
        if e.resource_id == "android:id/title" and e.text == old:
            new_elements.append(e.model_copy(update={"text": new}))
        else:
            new_elements.append(e)
    return screen.model_copy(update={"elements": new_elements})


def _replace_summary_text(screen: RawScreen, old: str, new: str) -> RawScreen:
    new_elements = []
    for e in screen.elements:
        if e.resource_id == "android:id/summary" and e.text == old:
            new_elements.append(e.model_copy(update={"text": new}))
        else:
            new_elements.append(e)
    return screen.model_copy(update={"elements": new_elements})


def _replace_any_text(screen: RawScreen, old: str, new: str) -> RawScreen:
    new_elements = []
    for e in screen.elements:
        if e.text == old:
            new_elements.append(e.model_copy(update={"text": new}))
        else:
            new_elements.append(e)
    return screen.model_copy(update={"elements": new_elements})


def test_three_settings_pages_distinct() -> None:
    s49 = _load("scr_0049.xml")
    s66 = _load("scr_0066.xml")
    s259 = _load("scr_0259.xml")
    ids = {s49.get_state_id(APP_PKG), s66.get_state_id(APP_PKG), s259.get_state_id(APP_PKG)}
    assert len(ids) == 3, f"Expected 3 distinct state_ids, got {ids}"


def test_summary_text_change_preserves_state_id() -> None:
    s = _load("scr_0066.xml")
    before = s.get_state_id(APP_PKG)
    mutated = _replace_summary_text(s, "Flutey Phone", "Cesium")
    after = mutated.get_state_id(APP_PKG)
    assert before == after, f"summary text change should not affect state_id: {before} -> {after}"


def test_dynamic_numeric_change_preserves_state_id() -> None:
    s = _load("scr_0259.xml")
    before = s.get_state_id(APP_PKG)
    mutated = _replace_any_text(s, "100%", "47%")
    after = mutated.get_state_id(APP_PKG)
    assert before == after, f"numeric text change should not affect state_id: {before} -> {after}"


def test_title_change_changes_state_id() -> None:
    s = _load("scr_0049.xml")
    before = s.get_state_id(APP_PKG)
    mutated = _replace_title_text(s, "Messages", "Notifications")
    after = mutated.get_state_id(APP_PKG)
    assert before != after, "title text change MUST change state_id (titles are anchors)"


def test_external_package_element_ignored() -> None:
    s = _load("scr_0049.xml")
    before = s.get_state_id(APP_PKG)
    intruder = UIElement(
        element_id="e_systemui_fake",
        class_name="android.widget.TextView",
        package="com.android.systemui",
        resource_id="com.android.systemui:id/clock",
        text="DISTINCTIVE_SYSTEMUI_TEXT_XYZ",
        is_clickable=True,
        depth=1,
    )
    intruded = s.model_copy(update={"elements": [*s.elements, intruder]})
    after = intruded.get_state_id(APP_PKG)
    assert before == after, "external-package elements must be ignored when computing state_id"


def test_empty_screen_returns_sentinel() -> None:
    empty = RawScreen(screen_id="empty", elements=[])
    assert empty.get_state_id(APP_PKG) == EMPTY_SCREEN_ID


# ============================================================
# Feature D: homogeneous list collapse
# ============================================================


def _make_list_screen(n_rows: int, screen_id: str = "s") -> RawScreen:
    """A synthetic page whose RecyclerView has ``n_rows`` structurally-identical rows."""
    root = UIElement(
        element_id="e_root",
        class_name="android.widget.FrameLayout",
        package=APP_PKG,
        resource_id="com.android.settings:id/content_parent",
        bounds=[0, 0, 1080, 2400],
    )
    scroll = UIElement(
        element_id="e_scroll",
        class_name="androidx.recyclerview.widget.RecyclerView",
        package=APP_PKG,
        resource_id="com.android.settings:id/mail_inbox",
        is_scrollable=True,
        is_enabled=True,
        bounds=[0, 0, 1080, 2400],
        parent_id=root.element_id,
        depth=1,
    )
    rows: list[UIElement] = []
    for i in range(n_rows):
        rows.append(
            UIElement(
                element_id=f"e_row_{i}",
                class_name="android.widget.LinearLayout",
                package=APP_PKG,
                is_clickable=True,
                is_enabled=True,
                bounds=[0, i * 100, 1080, (i + 1) * 100],
                parent_id=scroll.element_id,
                depth=2,
            )
        )
        rows.append(
            UIElement(
                element_id=f"e_title_{i}",
                class_name="android.widget.TextView",
                package=APP_PKG,
                resource_id="android:id/title",
                text=f"Row_{chr(ord('a') + (i % 26))}{i // 26}",
                bounds=[0, i * 100, 1080, (i + 1) * 100],
                parent_id=f"e_row_{i}",
                depth=3,
            )
        )
    return RawScreen(
        screen_id=screen_id,
        activity_name="com.mail.InboxActivity",
        package_name=APP_PKG,
        elements=[root, scroll, *rows],
    )


def test_homogeneous_list_50_rows_collapses_to_sentinel() -> None:
    scr = _make_list_screen(50)
    _, anchors = scr.get_functional_state_key(APP_PKG)
    # No per-row titles should survive.
    assert not any(
        rid == "title" for rid, _ in anchors
    ), "homogeneous list should collapse row titles"
    # A single list sentinel should be present.
    list_sentinels = {a for a in anchors if a[0].startswith("list_")}
    assert list_sentinels == {("list_mail_inbox", "non_empty")}


def test_homogeneous_list_varying_row_counts_same_state_id() -> None:
    # 30 rows vs 50 rows should share state_id once both are classified
    # homogeneous (>15 rows).
    sid_30 = _make_list_screen(30).get_state_id(APP_PKG)
    sid_50 = _make_list_screen(50).get_state_id(APP_PKG)
    assert sid_30 == sid_50


def test_empty_list_distinct_from_populated_list() -> None:
    # 0 rows -> "empty" sentinel; non-empty -> "non_empty" sentinel. Distinct.
    sid_empty = _make_list_screen(0).get_state_id(APP_PKG)
    sid_full = _make_list_screen(50).get_state_id(APP_PKG)
    assert sid_empty != sid_full


def test_heterogeneous_menu_preserved_under_threshold() -> None:
    # 10 rows still counts as heterogeneous menu → per-row titles preserved.
    scr = _make_list_screen(10)
    _, anchors = scr.get_functional_state_key(APP_PKG)
    titles = {t for rid, t in anchors if rid == "title"}
    assert len(titles) == 10


# ============================================================
# Hybrid state id (structural fingerprint + activity + page title)
# ============================================================


def test_hybrid_id_distinguishes_three_settings_pages() -> None:
    s49 = _load("scr_0049.xml")
    s66 = _load("scr_0066.xml")
    s259 = _load("scr_0259.xml")
    ids = {
        s49.get_hybrid_state_id(APP_PKG),
        s66.get_hybrid_state_id(APP_PKG),
        s259.get_hybrid_state_id(APP_PKG),
    }
    assert len(ids) == 3, f"expected 3 distinct hybrid ids, got {ids}"


def test_hybrid_id_collapses_list_content_variations() -> None:
    # 30-row and 50-row homogeneous lists have the same structural
    # skeleton (Feature D collapses descendants), no action_bar_title /
    # collapsing_toolbar in the synthetic fixture → title is "". Hybrid
    # reduces to structural fp + activity → same id.
    scr30 = _make_list_screen(30)
    scr50 = _make_list_screen(50)
    assert scr30.get_hybrid_state_id(APP_PKG) == scr50.get_hybrid_state_id(APP_PKG)


def test_hybrid_id_empty_screen_sentinel() -> None:
    assert RawScreen(screen_id="x", elements=[]).get_hybrid_state_id(APP_PKG) == EMPTY_SCREEN_ID


def test_extract_page_title_from_collapsing_toolbar() -> None:
    # scr_0049 has a collapsing_toolbar with content-desc="People".
    s49 = _load("scr_0049.xml")
    assert s49.extract_page_title() == "People"
