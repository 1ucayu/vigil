"""Tests for vigil.neuro.semantic_grounder — Stage 2.5: Semantic Grounding."""

from __future__ import annotations

from unittest.mock import MagicMock

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
)
from vigil.neuro.app_prior import ActivityInfo, AppPrior, WidgetDecl
from vigil.neuro.semantic_grounder import (
    SemanticGrounder,
    _build_element_table,
    _build_screen_context_from_obs,
    _build_static_context,
    _functions_consistent,
    _match_layout_to_activity,
    _parse_json,
)


def _mock_llm(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.generate.side_effect = responses
    llm.generate_with_images.side_effect = responses
    return llm


def _make_obs(
    screen_id: str = "scr_001",
    elements: list[dict] | None = None,
    screenshot_path: str | None = None,
) -> dict:
    if elements is None:
        elements = [
            {
                "element_id": "e_001",
                "class_name": "android.widget.TextView",
                "resource_id": "com.app:id/title",
                "text": "WiFi",
                "is_clickable": True,
                "is_enabled": True,
            },
            {
                "element_id": "e_002",
                "class_name": "android.widget.Switch",
                "resource_id": "com.app:id/toggle",
                "text": "",
                "is_clickable": True,
                "is_checkable": True,
                "is_checked": True,
                "is_enabled": True,
            },
        ]
    return {
        "screen_id": screen_id,
        "screenshot_path": screenshot_path,
        "interactable_elements": elements,
    }


def _make_state(state_id: str = "s1", **overrides) -> AbstractState:
    defaults = {
        "state_id": state_id,
        "name": f"State_{state_id}",
        "fingerprint": f"fp_{state_id}",
        "hierarchy_level": HierarchyLevel.ACTIVITY,
        "raw_screens": ["scr_001"],
    }
    defaults.update(overrides)
    return AbstractState(**defaults)


# ============================================================
# State Description
# ============================================================


class TestGenerateStateDescription:
    def test_basic_description(self) -> None:
        llm = _mock_llm(
            [
                '{"alt_text": "WiFi settings page", '
                '"page_function": "settings/wifi", '
                '"expected_actions": ["toggle_wifi", "select_network"]}'
            ]
        )
        grounder = SemanticGrounder(llm)
        state = _make_state()
        obs = [_make_obs()]

        profile = grounder.generate_state_description(state, obs)
        assert profile.alt_text == "WiFi settings page"
        assert profile.page_function == "settings/wifi"
        assert "toggle_wifi" in profile.expected_actions
        assert profile.generation_confidence == 0.7

    def test_with_app_prior_consistent(self) -> None:
        llm = _mock_llm(
            [
                '{"alt_text": "WiFi list", '
                '"page_function": "settings/wifi/list", '
                '"expected_actions": ["connect"]}'
            ]
        )
        grounder = SemanticGrounder(llm)
        state = _make_state(activity_name="com.app.WiFiActivity")
        prior = AppPrior(
            package_name="com.app",
            activities=[
                ActivityInfo(
                    name="com.app.WiFiActivity",
                    label="WiFi",
                    predicted_function="settings/wifi",
                )
            ],
        )

        profile = grounder.generate_state_description(state, [_make_obs()], prior)
        assert profile.generation_confidence == 1.0

    def test_with_app_prior_conflicting(self) -> None:
        llm = _mock_llm(
            [
                '{"alt_text": "Payment page", '
                '"page_function": "payment/confirm", '
                '"expected_actions": ["pay"]}'
            ]
        )
        grounder = SemanticGrounder(llm)
        state = _make_state(activity_name="com.app.WiFiActivity")
        prior = AppPrior(
            package_name="com.app",
            activities=[
                ActivityInfo(
                    name="com.app.WiFiActivity",
                    predicted_function="settings/wifi",
                )
            ],
        )

        profile = grounder.generate_state_description(state, [_make_obs()], prior)
        assert profile.generation_confidence == 0.5

    def test_invalid_json_graceful(self) -> None:
        llm = _mock_llm(["not json at all"])
        grounder = SemanticGrounder(llm)
        profile = grounder.generate_state_description(_make_state(), [_make_obs()])
        assert profile.alt_text == ""
        assert profile.page_function == ""

    def test_markdown_fenced_json(self) -> None:
        llm = _mock_llm(
            [
                '```json\n{"alt_text": "Home", "page_function": "home", '
                '"expected_actions": ["navigate"]}\n```'
            ]
        )
        grounder = SemanticGrounder(llm)
        profile = grounder.generate_state_description(_make_state(), [_make_obs()])
        assert profile.alt_text == "Home"


# ============================================================
# Icon Annotation
# ============================================================


class TestAnnotateIcons:
    def test_labels_anonymous_elements(self) -> None:
        elements = [
            {
                "element_id": "e_010",
                "class_name": "android.widget.ImageButton",
                "text": None,
                "content_description": None,
                "is_clickable": True,
                "bounds": [0, 0, 100, 100],
            },
            {
                "element_id": "e_011",
                "class_name": "android.widget.ImageButton",
                "text": None,
                "content_description": None,
                "is_clickable": True,
                "bounds": [200, 0, 300, 100],
            },
        ]
        llm = _mock_llm(['{"e_010": "back_arrow", "e_011": "share_icon"}'])
        grounder = SemanticGrounder(llm)
        state = _make_state()
        obs = [_make_obs(elements=elements)]

        labels = grounder.annotate_icons(state, obs)
        assert labels == {"e_010": "back_arrow", "e_011": "share_icon"}

    def test_no_anonymous_elements(self) -> None:
        elements = [
            {
                "element_id": "e_001",
                "class_name": "android.widget.Button",
                "text": "OK",
                "content_description": "Confirm",
                "is_clickable": True,
            },
        ]
        llm = _mock_llm([])
        grounder = SemanticGrounder(llm)
        obs = [_make_obs(elements=elements)]
        labels = grounder.annotate_icons(_make_state(), obs)
        assert labels == {}
        llm.generate.assert_not_called()

    def test_filters_invalid_ids(self) -> None:
        elements = [
            {
                "element_id": "e_010",
                "class_name": "android.widget.ImageButton",
                "text": None,
                "content_description": None,
                "is_clickable": True,
                "bounds": [0, 0, 100, 100],
            },
        ]
        llm = _mock_llm(['{"e_010": "menu_icon", "e_999": "hallucinated"}'])
        grounder = SemanticGrounder(llm)
        labels = grounder.annotate_icons(_make_state(), [_make_obs(elements=elements)])
        assert "e_010" in labels
        assert "e_999" not in labels

    def test_invalid_json_returns_empty(self) -> None:
        elements = [
            {
                "element_id": "e_010",
                "class_name": "android.widget.ImageButton",
                "text": None,
                "content_description": None,
                "is_clickable": True,
                "bounds": [0, 0, 100, 100],
            },
        ]
        llm = _mock_llm(["not json"])
        grounder = SemanticGrounder(llm)
        labels = grounder.annotate_icons(_make_state(), [_make_obs(elements=elements)])
        assert labels == {}


# ============================================================
# Invariant Mining
# ============================================================


class TestMineInvariants:
    def test_single_observation_low_confidence(self) -> None:
        llm = _mock_llm(
            ['["count(com.app:id/list) >= 1", "read(com.app:id/title, text) != \\"\\""]']
        )
        grounder = SemanticGrounder(llm)
        state = _make_state()
        obs = [_make_obs()]

        invariants, confidence, ctype = grounder.mine_invariants(state, obs)
        assert len(invariants) == 2
        assert confidence == 0.5
        assert ctype == ContainerType.DYNAMIC

    def test_no_observations(self) -> None:
        llm = _mock_llm([])
        grounder = SemanticGrounder(llm)
        invariants, confidence, ctype = grounder.mine_invariants(_make_state(), [])
        assert invariants == []
        assert confidence == 0.0
        assert ctype == ContainerType.NONE

    def test_llm_returns_garbage(self) -> None:
        llm = _mock_llm(["not an array"])
        grounder = SemanticGrounder(llm)
        invariants, confidence, ctype = grounder.mine_invariants(_make_state(), [_make_obs()])
        assert invariants == []
        assert confidence == 0.0


class TestDeriveContainerType:
    def test_exact_count_is_static(self) -> None:
        ctype = SemanticGrounder._derive_container_type(
            ["count(com.app:id/list) == 8"], [_make_obs(), _make_obs()]
        )
        assert ctype == ContainerType.STATIC

    def test_range_count_is_dynamic(self) -> None:
        ctype = SemanticGrounder._derive_container_type(
            ["count(com.app:id/list) >= 1"], [_make_obs(), _make_obs()]
        )
        assert ctype == ContainerType.DYNAMIC

    def test_no_count_invariants_is_none(self) -> None:
        ctype = SemanticGrounder._derive_container_type(['read(title, text) != ""'], [_make_obs()])
        assert ctype == ContainerType.NONE

    def test_exact_takes_precedence(self) -> None:
        ctype = SemanticGrounder._derive_container_type(
            ["count(com.app:id/list) == 5", "count(other) >= 1"],
            [_make_obs()],
        )
        assert ctype == ContainerType.STATIC


class TestSoftPredictContainerType:
    def test_range_predicts_dynamic(self) -> None:
        ctype = SemanticGrounder._soft_predict_container_type(["count(list) >= 1"])
        assert ctype == ContainerType.DYNAMIC

    def test_exact_predicts_static(self) -> None:
        ctype = SemanticGrounder._soft_predict_container_type(["count(list) == 5"])
        assert ctype == ContainerType.STATIC

    def test_no_count_predicts_none(self) -> None:
        ctype = SemanticGrounder._soft_predict_container_type(['read(title, text) != ""'])
        assert ctype == ContainerType.NONE


# ============================================================
# Orchestrator
# ============================================================


class TestGroundAllStates:
    def test_enriches_fsm_states(self) -> None:
        desc_response = (
            '{"alt_text": "Main page", "page_function": "main", "expected_actions": ["tap"]}'
        )
        icon_response = '{"e_002": "toggle_switch"}'
        inv_response = '["count(com.app:id/list) >= 1"]'
        llm = _mock_llm([desc_response, icon_response, inv_response])
        grounder = SemanticGrounder(llm)

        fsm = AppFSM("com.test.app")
        state = _make_state("s1", raw_screens=["scr_001"])
        fsm.add_state(state)
        fsm.initial_state = "s1"

        raw_screens = {"scr_001": _make_obs("scr_001")}
        result = grounder.ground_all_states(fsm, raw_screens)

        s = result.states["s1"]
        assert s.annotations.alt_text == "Main page"
        assert len(s.invariant_specs) == 1
        assert max((spec.confidence for spec in s.invariant_specs), default=0.0) == 0.5

    def test_skips_state_with_no_screens(self) -> None:
        llm = _mock_llm([])
        grounder = SemanticGrounder(llm)

        fsm = AppFSM("com.test.app")
        state = _make_state("s1", raw_screens=[])
        fsm.add_state(state)

        grounder.ground_all_states(fsm, {})
        llm.generate.assert_not_called()


# ============================================================
# Helpers
# ============================================================


class TestHelpers:
    def test_parse_json_valid(self) -> None:
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_parse_json_fenced(self) -> None:
        assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_parse_json_invalid(self) -> None:
        assert _parse_json("not json") is None

    def test_functions_consistent_overlap(self) -> None:
        assert _functions_consistent("settings/wifi/list", "settings/wifi") is True

    def test_functions_consistent_no_overlap(self) -> None:
        assert _functions_consistent("payment/confirm", "settings/wifi") is False

    def test_build_element_table_empty(self) -> None:
        result = _build_element_table({"interactable_elements": []})
        assert "no interactable" in result

    def test_build_screen_context(self) -> None:
        obs = _make_obs()
        ctx = _build_screen_context_from_obs(obs)
        assert "e_001" in ctx.elements
        assert "com.app:id/title" in ctx.elements
        assert ctx.elements["e_001"]["text"] == "WiFi"


class TestStaticContextInjection:
    def test_with_widgets(self) -> None:
        prior = AppPrior(
            package_name="com.test",
            widget_declarations=[
                WidgetDecl(
                    widget_id="wifi_switch",
                    widget_class="Switch",
                    layout_file="activity_settings",
                ),
            ],
        )
        ctx = _build_static_context(prior, "com.test.SettingsActivity", [])
        assert "Switch" in ctx
        assert "wifi_switch" in ctx

    def test_with_strings(self) -> None:
        prior = AppPrior(
            package_name="com.test",
            string_constants={"cancel": "Cancel", "ok": "OK"},
        )
        elements = [{"text": "Cancel"}, {"text": "Submit"}]
        ctx = _build_static_context(prior, None, elements)
        assert "Cancel" in ctx
        assert "Submit" not in ctx

    def test_with_arrays(self) -> None:
        prior = AppPrior(
            package_name="com.test",
            string_arrays={"durations": ["1 min", "5 min", "10 min"]},
        )
        ctx = _build_static_context(prior, None, [])
        assert "durations" in ctx
        assert "1 min" in ctx

    def test_with_permissions(self) -> None:
        prior = AppPrior(
            package_name="com.test",
            permissions=["android.permission.CAMERA", "android.permission.INTERNET"],
        )
        ctx = _build_static_context(prior, None, [])
        assert "CAMERA" in ctx
        assert "INTERNET" in ctx

    def test_empty_prior(self) -> None:
        prior = AppPrior(package_name="com.test")
        assert _build_static_context(prior, None, []) == ""

    def test_no_prior(self) -> None:
        assert _build_static_context(None, None, []) == ""

    def test_layout_activity_matching(self) -> None:
        assert _match_layout_to_activity("activity_alarm", "com.app.AlarmActivity") is True
        assert _match_layout_to_activity("fragment_settings", "com.app.SettingsActivity") is True
        assert _match_layout_to_activity("activity_main", "com.app.AlarmActivity") is False
        assert _match_layout_to_activity("activity_alarm", None) is False
