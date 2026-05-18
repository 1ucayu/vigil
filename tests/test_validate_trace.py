"""Tests for ``vigil-validate-trace`` (trace quality validator).

Uses synthetic in-memory traces; does not depend on a real device or any
existing generated trace under ``data/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigil.scripts.validate_trace import _render_text, validate_trace


def _trace(
    step: int,
    *,
    src: str = "s_a",
    tgt: str = "s_b",
    intended: str | None = None,
    action_type: str = "click",
    resource_id: str = "com.example:id/btn",
    metadata: dict | None = None,
) -> dict:
    return {
        "step_number": step,
        "source_state_id": src,
        "intended_source_state_id": intended if intended is not None else src,
        "source_screen_id": f"scr_{step}_src",
        "target_state_id": tgt,
        "target_screen_id": f"scr_{step}_tgt",
        "action": {
            "action_type": action_type,
            "target_resource_id": resource_id,
        },
        "timestamp": "",
        "metadata": metadata or {},
    }


def test_validate_trace_empty() -> None:
    summary = validate_trace({"traces": [], "screens": {}, "nav_stats": {}})
    assert summary["total_steps"] == 0
    assert summary["sentinel_rate"] == 0.0
    assert summary["warnings"] == []


def test_validate_trace_counts_sentinels_and_left_app_scope() -> None:
    traces = [
        _trace(1),
        _trace(2, tgt="ACTION_FAILED"),
        _trace(
            3,
            tgt="LEFT_APP",
            metadata={
                "scope_post": "out_of_scope_external",
                "left_app_reason": "out_of_scope_external",
            },
        ),
        _trace(
            4,
            tgt="LEFT_APP",
            metadata={
                "scope_post": "launcher_or_home",
                "left_app_reason": "launcher_or_home",
            },
        ),
    ]
    summary = validate_trace({"traces": traces, "screens": {}, "nav_stats": {}})
    assert summary["sentinel_breakdown"]["ACTION_FAILED"] == 1
    assert summary["sentinel_breakdown"]["LEFT_APP"] == 2
    assert summary["action_failed_rate"] == 0.25
    assert summary["left_app_by_scope"] == {
        "out_of_scope_external": 1,
        "launcher_or_home": 1,
    }
    assert summary["sentinel_rate"] == 0.75


def test_validate_trace_scroll_and_self_loops() -> None:
    traces = [
        _trace(1, action_type="scroll_down", src="s1", tgt="s1"),  # meaningful self-loop
        _trace(2, action_type="click", src="s1", tgt="s2"),
        _trace(3, action_type="input_text", src="s2", tgt="s2"),
    ]
    summary = validate_trace({"traces": traces, "screens": {}, "nav_stats": {}})
    assert summary["scroll_transition_count"] == 1
    assert summary["meaningful_self_loop_count"] == 2


def test_validate_trace_drift_and_untrusted() -> None:
    traces = [_trace(1)]
    summary = validate_trace(
        {
            "traces": traces,
            "screens": {},
            "nav_stats": {"drift_count": 1, "untrusted_targets": 2},
        }
    )
    assert summary["drift_rate"] == 1.0
    assert summary["untrusted_target_count"] == 2
    assert any("drift_rate" in w for w in summary["warnings"])


def test_validate_trace_ambiguous_selector_warning() -> None:
    traces = [
        _trace(
            1,
            tgt="ACTION_FAILED",
            metadata={"selector_resolution": "ambiguous"},
        )
    ]
    summary = validate_trace({"traces": traces, "screens": {}, "nav_stats": {}})
    assert summary["ambiguous_selector_count"] == 1
    assert any("ambiguous selector" in w for w in summary["warnings"])


def test_validate_trace_input_side_effect_warning() -> None:
    traces = [_trace(1, action_type="input_text", metadata={"cleared": False})]
    summary = validate_trace({"traces": traces, "screens": {}, "nav_stats": {}})
    assert summary["input_side_effect_warnings"] == 1
    assert any("did not clear" in w for w in summary["warnings"])


def test_validate_trace_skipped_risky() -> None:
    traces = [_trace(1, metadata={"risk_tags": ["destructive"]})]
    summary = validate_trace({"traces": traces, "screens": {}, "nav_stats": {}})
    assert summary["skipped_risky_count"] == 1


def test_validate_trace_repeated_action_ratio() -> None:
    traces = [_trace(i, src="s1", action_type="click") for i in range(1, 6)]
    summary = validate_trace({"traces": traces, "screens": {}, "nav_stats": {}})
    # All 5 are (s1, click rid) — one unique (s, a), repeated 5x.
    assert summary["repeated_action_ratio"] == 1.0


def test_validate_trace_state_merge_risk_groups() -> None:
    screens = {
        "scr1": {"page_title": "Inbox", "structural_fingerprint": "fpA"},
        "scr2": {"page_title": "Inbox", "structural_fingerprint": "fpB"},
        "scr3": {"page_title": "Sent", "structural_fingerprint": "fpC"},
    }
    summary = validate_trace({"traces": [], "screens": screens, "nav_stats": {}})
    assert "Inbox" in summary["state_merge_risk_groups"]
    assert "Sent" not in summary["state_merge_risk_groups"]
    assert any("structural fingerprints" in w for w in summary["warnings"])


def test_render_text_does_not_raise(tmp_path: Path) -> None:
    summary = validate_trace({"traces": [_trace(1)], "screens": {}, "nav_stats": {}})
    text = _render_text(summary)
    assert "total_steps:" in text
    assert "scroll_transition_count:" in text


def test_validate_trace_cli_compatible_via_file(tmp_path: Path) -> None:
    payload = {"traces": [_trace(1)], "screens": {}, "nav_stats": {}}
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload))
    # The validator's pure function operates on the dict, mirroring what
    # the CLI does after json.loads. Asserting via the function avoids
    # subprocessing pytest under uv.
    summary = validate_trace(json.loads(path.read_text()))
    assert summary["total_steps"] == 1


@pytest.mark.parametrize(
    "metadata_value",
    [None, {}, {"selector_resolution": "match"}],
)
def test_validate_trace_tolerates_missing_metadata(metadata_value: dict | None) -> None:
    """Backward compatibility: traces from before the metadata field existed
    must still validate without error."""
    t = _trace(1)
    if metadata_value is None:
        t.pop("metadata", None)
    else:
        t["metadata"] = metadata_value
    summary = validate_trace({"traces": [t], "screens": {}, "nav_stats": {}})
    assert summary["total_steps"] == 1


def test_validate_trace_counts_action_attempts() -> None:
    """Skipped attempts surface in attempt_status_breakdown and never
    influence sentinel/transition counts directly (they appear only as
    refusal accounting)."""
    payload = {
        "traces": [],
        "screens": {},
        "nav_stats": {},
        "action_attempts": [
            {
                "step_number": 0,
                "source_state_id": "s",
                "action": {"action_type": "click"},
                "status": "skipped_risky",
                "metadata": {"severity": "hard_block", "risk_tags": ["destructive"]},
            },
            {
                "step_number": 0,
                "source_state_id": "s",
                "action": {"action_type": "click"},
                "status": "ambiguous_selector",
                "metadata": {},
            },
            {
                "step_number": 0,
                "source_state_id": "s",
                "action": {"action_type": "input_text"},
                "status": "skipped_unsafe_input",
                "metadata": {"cleared": False},
            },
        ],
    }
    summary = validate_trace(payload)
    assert summary["skipped_risky_count"] == 1
    assert summary["ambiguous_selector_count"] == 1
    assert summary["skipped_unsafe_input_count"] == 1
    assert summary["action_attempt_severity_breakdown"].get("hard_block") == 1
    # Warning surfaces.
    assert any("hard-block" in w for w in summary["warnings"])
    assert any("INPUT_TEXT" in w for w in summary["warnings"])
