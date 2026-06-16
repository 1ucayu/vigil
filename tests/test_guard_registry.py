"""Tests for the deterministic widget registry (guard generation, step 2).

Covers stable alias assignment, role inference, selector stability grading, and
the trace-only invariant for ``AppPrior`` (priors never create entries). No LLM,
DSL compilation, or admission logic is exercised here.
"""

from __future__ import annotations

from vigil.models.fsm import AbstractState, HierarchyLevel
from vigil.neuro.app_prior import ActivityInfo, AppPrior, WidgetDecl
from vigil.neuro.guard_registry import (
    SelectorStability,
    WidgetRole,
    build_widget_registry,
    build_widget_registry_from_screen,
)

PKG = "com.test.app"


def _el(element_id: str, **overrides) -> dict:
    base = {
        "element_id": element_id,
        "class_name": "android.view.View",
        "resource_id": "",
        "text": "",
        "content_description": "",
        "is_clickable": False,
        "is_enabled": True,
    }
    base.update(overrides)
    return base


def _screen(*elements: dict, screen_id: str = "scr_001") -> dict:
    return {
        "screen_id": screen_id,
        "activity_name": f"{PKG}.MainActivity",
        "package_name": PKG,
        "interactable_elements": list(elements),
    }


def _screen_with_all(
    interactable_elements: list[dict],
    elements: list[dict],
    screen_id: str = "scr_001",
) -> dict:
    return {
        "screen_id": screen_id,
        "activity_name": f"{PKG}.MainActivity",
        "package_name": PKG,
        "interactable_elements": interactable_elements,
        "elements": elements,
    }


def _state(raw_screen_ids: list[str]) -> AbstractState:
    return AbstractState(
        state_id="s1",
        name="Main",
        fingerprint="fp_s1",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=raw_screen_ids,
    )


def test_resource_id_creates_stable_short_alias():
    screen = _screen(
        _el(
            "e_0001",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/amount_input",
            text="Amount",
            is_clickable=True,
        )
    )
    registry = build_widget_registry_from_screen("s1", screen)

    assert "amount_input" in registry.entries
    entry = registry.entries["amount_input"]
    assert entry.resource_id == f"{PKG}:id/amount_input"
    assert registry.resource_id_to_alias[f"{PKG}:id/amount_input"] == "amount_input"


def test_element_id_retained_but_not_primary_when_resource_id_exists():
    screen = _screen(
        _el(
            "e_0042",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/submit",
            is_clickable=True,
        )
    )
    registry = build_widget_registry_from_screen("s1", screen)

    # Primary alias comes from the resource id, not the raw e_XXXX handle.
    assert "submit" in registry.entries
    assert "e_0042" not in registry.entries
    # But the capture-local handle is retained for later action -> alias mapping.
    assert registry.element_id_to_alias["e_0042"] == "submit"
    assert registry.entries["submit"].element_id == "e_0042"


def test_raw_element_id_only_used_when_no_better_signal():
    screen = _screen(
        _el("e_0099", class_name="", is_clickable=True),
    )
    registry = build_widget_registry_from_screen("s1", screen)
    assert "e_0099" in registry.entries
    assert registry.element_id_to_alias["e_0099"] == "e_0099"


def test_role_inference_edittext_switch_button():
    screen = _screen(
        _el("e_1", class_name="android.widget.EditText", resource_id=f"{PKG}:id/q"),
        _el(
            "e_2",
            class_name="android.widget.Switch",
            resource_id=f"{PKG}:id/wifi",
            is_clickable=True,
            is_checkable=True,
        ),
        _el(
            "e_3",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/go",
            text="Continue",
            is_clickable=True,
        ),
    )
    registry = build_widget_registry_from_screen("s1", screen)

    assert registry.entries["q"].role is WidgetRole.TEXT_FIELD
    assert registry.entries["wifi"].role is WidgetRole.TOGGLE
    assert registry.entries["go"].role is WidgetRole.BUTTON


def test_role_inference_list_container_and_image_button():
    screen = _screen(
        _el(
            "e_1",
            class_name="androidx.recyclerview.widget.RecyclerView",
            resource_id=f"{PKG}:id/list",
            is_scrollable=True,
        ),
        _el(
            "e_2",
            class_name="android.widget.ImageButton",
            resource_id=f"{PKG}:id/overflow",
            is_clickable=True,
        ),
    )
    registry = build_widget_registry_from_screen("s1", screen)
    assert registry.entries["list"].role is WidgetRole.LIST_CONTAINER
    assert registry.entries["overflow"].role is WidgetRole.IMAGE_BUTTON


def test_selector_stability_high_medium_low():
    screen = _screen(
        _el("e_1", class_name="android.widget.Button", resource_id=f"{PKG}:id/a"),
        _el("e_2", class_name="android.widget.Button", content_description="Back"),
        _el("e_3", class_name="android.widget.Button", text="Save"),
        _el("e_4", class_name="android.widget.Button"),
    )
    registry = build_widget_registry_from_screen("s1", screen)
    aliases = {e.element_id: e for e in registry.entries.values()}

    assert aliases["e_1"].selector_stability is SelectorStability.HIGH
    assert aliases["e_2"].selector_stability is SelectorStability.MEDIUM
    assert aliases["e_3"].selector_stability is SelectorStability.MEDIUM
    assert aliases["e_4"].selector_stability is SelectorStability.LOW


def test_readable_props_include_present_keys_even_when_empty():
    # text / content_description are present keys (DecisionEngine exposes them at runtime
    # even when empty), so they are listed regardless of emptiness.
    screen = _screen(
        _el(
            "e_1",
            class_name="android.widget.Switch",
            resource_id=f"{PKG}:id/wifi",
            text="Wi-Fi",
            is_clickable=True,
            is_checkable=True,
            is_checked=True,
        )
    )
    registry = build_widget_registry_from_screen("s1", screen)
    props = registry.entries["wifi"].readable_props

    assert "text" in props
    assert "resource_id" in props
    assert "class_name" in props
    assert "is_checked" in props
    # content_description key is present (empty) -> still listed.
    assert "content_description" in props


def test_readable_props_empty_edittext_includes_text_and_value():
    screen = _screen(
        _el(
            "e_1",
            class_name="android.widget.EditText",
            resource_id=f"{PKG}:id/amount",
            text="",
            value="",
            is_editable=True,
            is_enabled=True,
        )
    )
    registry = build_widget_registry_from_screen("s1", screen)
    props = registry.entries["amount"].readable_props

    # Empty EditText must still expose text/value for form guards.
    assert "text" in props
    assert "value" in props
    assert "resource_id" in props
    assert "class_name" in props
    assert "is_editable" in props
    assert "is_enabled" in props


def test_registry_includes_runtime_readable_semantic_elements_with_interactables():
    pay = _el(
        "e_pay",
        class_name="android.view.View",
        resource_id="payment_confirm.pay",
        is_clickable=True,
    )
    product = _el(
        "e_product",
        class_name="android.widget.TextView",
        resource_id="payment.product_name",
        text="Espresso",
        is_clickable=False,
    )
    amount = _el(
        "e_amount",
        class_name="android.widget.TextView",
        resource_id="payment.total_amount",
        text="Total: $4.50",
        is_clickable=False,
    )
    decorative = _el(
        "e_decor",
        class_name="android.view.View",
        resource_id="payment.summary_card",
        text="",
        is_clickable=False,
    )
    screen = _screen_with_all([pay], [product, amount, decorative, pay])

    registry = build_widget_registry_from_screen("s1", screen)

    assert "payment_confirm_pay" in registry.entries
    assert "payment_product_name" in registry.entries
    assert "payment_total_amount" in registry.entries
    assert "payment_summary_card" not in registry.entries
    assert registry.entries["payment_product_name"].text == "Espresso"
    assert "text" in registry.entries["payment_total_amount"].readable_props
    assert registry.resource_id_to_alias["payment.product_name"] == "payment_product_name"


def test_llm_widget_aliases_enrich_existing_entries_only():
    screen = _screen(
        _el("e_1", class_name="android.widget.ImageButton", is_clickable=True),
        _el("e_2", class_name="android.widget.Button", text="Cancel", is_clickable=True),
    )
    registry = build_widget_registry_from_screen(
        "s1",
        screen,
        widget_aliases=[
            {
                "element_id": "e_1",
                "label": "delete_button",
                "confidence": 0.82,
                "basis": "trash icon",
            },
            {
                "element_id": "e_missing",
                "label": "send_button",
                "confidence": 0.9,
                "basis": "not present at runtime",
            },
        ],
    )
    by_eid = {entry.element_id: entry for entry in registry.entries.values()}

    assert len(registry.entries) == 2
    assert by_eid["e_1"].source == "trace+llm"
    assert "e_missing" not in registry.element_id_to_alias


def test_app_prior_does_not_create_absent_widgets():
    screen = _screen(
        _el(
            "e_1",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/present",
            is_clickable=True,
        )
    )
    prior = AppPrior(
        package_name=PKG,
        activities=[ActivityInfo(name=f"{PKG}.MainActivity", is_launcher=True)],
        widget_declarations=[
            WidgetDecl(
                widget_id="absent",
                widget_class="android.widget.Button",
                layout_file="main.xml",
            ),
            WidgetDecl(
                widget_id="present",
                widget_class="android.widget.Button",
                layout_file="main.xml",
            ),
        ],
        string_constants={"label_send": "Send money"},
    )
    registry = build_widget_registry_from_screen("s1", screen, app_prior=prior)

    # Only the runtime-present element is registered; the prior-only "absent" widget
    # never becomes an entry.
    assert len(registry.entries) == 1
    assert "present" in registry.entries
    assert "absent" not in registry.entries


def test_build_widget_registry_from_state_uses_first_resolvable_screen():
    screen = _screen(
        _el(
            "e_1",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/go",
            is_clickable=True,
        ),
        screen_id="scr_900",
    )
    state = _state(["missing_screen", "scr_900"])
    registry = build_widget_registry(state, {"scr_900": screen})

    assert registry.state_id == "s1"
    assert registry.screen_id == "scr_900"
    assert "go" in registry.entries


def test_build_widget_registry_missing_screen_returns_empty():
    state = _state(["nope"])
    registry = build_widget_registry(state, {})
    assert registry.state_id == "s1"
    assert registry.entries == {}
