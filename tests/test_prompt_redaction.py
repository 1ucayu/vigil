"""Tests for generic, config/evidence-driven prompt-time identifier redaction.

The fidelity fixtures (``com.vigil`` ...) appear here only as EXAMPLES; redaction is driven by
configured/evidence identifiers, not a hardcoded fidelity blacklist. Generic patterns
(configured packages/slugs, raw absolute paths, ``scr_####`` ids, gold labels) are also tested.
"""

from __future__ import annotations

import pytest

from vigil.neuro.prompt_redaction import PromptRedactor, build_prompt_redactor

# ---------------------------------------------------------------------------
# Current fidelity fixtures, used only as examples (not the complete rule).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier",
    ["com.vigil", "com_vigil", "vigilmarket", "vigilbank", "vigilchat", "vigilclock"],
)
def test_fixture_examples_are_masked(identifier: str) -> None:
    redactor = PromptRedactor(packages=[identifier])
    text = f"the app {identifier} does things"
    out = redactor.redact(text)
    assert identifier not in out
    assert "<app>" in out


def test_resource_id_package_prefix_is_masked_but_suffix_preserved() -> None:
    redactor = PromptRedactor(packages=["com.vigil.market"])
    out = redactor.redact("resource_id=com.vigil.market:id/amount_input role=button")
    assert "com.vigil.market" not in out
    assert "<app>:id/amount_input" in out
    # Usable role hint preserved.
    assert "role=button" in out


# ---------------------------------------------------------------------------
# Generic config-driven identifiers and patterns.
# ---------------------------------------------------------------------------


def test_configured_package_and_slug_masked() -> None:
    redactor = PromptRedactor(
        packages=["com.example.app", "com_example_app_fidelity"],
        extra_identifiers=["exampleslug"],
    )
    out = redactor.redact("pkg com.example.app slug exampleslug bundle com_example_app_fidelity")
    assert "com.example.app" not in out
    assert "exampleslug" not in out
    assert "com_example_app_fidelity" not in out


def test_longer_identifier_masked_before_shorter_substring() -> None:
    # The fidelity bundle id embeds the package; both must vanish regardless of order.
    redactor = PromptRedactor(packages=["com.example.app", "com.example.app.fidelity"])
    out = redactor.redact("com.example.app.fidelity and com.example.app")
    assert "com.example.app" not in out


def test_raw_absolute_paths_masked() -> None:
    redactor = PromptRedactor(paths=["/data/apps/com.example/traces/run.json"])
    out = redactor.redact(
        "screenshot /data/apps/com.example/screens/scr_0001.png xml "
        "/data/apps/com.example/traces/run.json"
    )
    assert "/data/apps/com.example" not in out
    assert "<path>" in out


def test_scr_ids_masked_explicit_and_generic() -> None:
    redactor = PromptRedactor(screen_ids=["scr_weird_id"])
    out = redactor.redact("frames scr_0042 and scr_weird_id seen")
    assert "scr_0042" not in out
    assert "scr_weird_id" not in out
    assert "<scr>" in out


def test_gold_label_masked_via_extra_identifiers() -> None:
    redactor = PromptRedactor(extra_identifiers=["GoldTaskAnswer_42"])
    out = redactor.redact("the evaluator label GoldTaskAnswer_42 must not leak")
    assert "GoldTaskAnswer_42" not in out


def test_usable_evidence_is_preserved() -> None:
    redactor = PromptRedactor(packages=["com.vigil.market"], screen_ids=["scr_0001"])
    text = (
        "alias=amount_field resource_id=com.vigil.market:id/amount_input "
        "perm:SEND_SMS readable=[text, is_enabled] action(input_text)"
    )
    out = redactor.redact(text)
    assert "alias=amount_field" in out
    assert "perm:SEND_SMS" in out
    assert "readable=[text, is_enabled]" in out
    assert "action(input_text)" in out
    assert "<app>:id/amount_input" in out


# ---------------------------------------------------------------------------
# Builder draws from FSM + evidence (duck-typed).
# ---------------------------------------------------------------------------


class _FakeScreen:
    def __init__(self, package_name="", screen_id="", screenshot_path="", xml_tree_path=""):
        self.package_name = package_name
        self.screen_id = screen_id
        self.screenshot_path = screenshot_path
        self.xml_tree_path = xml_tree_path


class _FakeGuardEvidence:
    def __init__(self):
        self.source_screen = _FakeScreen("com.vigil.bank", "scr_0001", "/tmp/a.png", "/tmp/a.xml")
        self.target_screen = _FakeScreen("com.vigil.bank", "scr_0002")
        self.source_screen_ids = ["scr_0001"]
        self.target_screen_ids = ["scr_0002"]


class _FakeFsm:
    app_package = "com.vigil.bank"


def test_build_prompt_redactor_from_guard_evidence() -> None:
    redactor = build_prompt_redactor(
        _FakeFsm(), [_FakeGuardEvidence()], extra_identifiers=["vigilbank"]
    )
    out = redactor.redact(
        "pkg com.vigil.bank slug vigilbank scr_0001 path /tmp/a.png id com.vigil.bank:id/x"
    )
    assert "com.vigil.bank" not in out
    assert "vigilbank" not in out
    assert "scr_0001" not in out
    assert "/tmp/a.png" not in out
    assert "<app>:id/x" in out


def test_build_prompt_redactor_from_invariant_evidence() -> None:
    class _InvEvidence:
        raw_screen_ids = ["scr_0009"]
        observations = [
            {
                "screen_id": "scr_0009",
                "screenshot_path": "/tmp/o.png",
                "xml_tree_path": "/tmp/o.xml",
            }
        ]

    redactor = build_prompt_redactor(_FakeFsm(), [_InvEvidence()])
    out = redactor.redact("frame scr_0009 at /tmp/o.png in com.vigil.bank")
    assert "scr_0009" not in out
    assert "/tmp/o.png" not in out
    assert "com.vigil.bank" not in out
