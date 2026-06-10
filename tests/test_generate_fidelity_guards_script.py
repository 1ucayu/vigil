"""Tests for the fidelity guard-generation CLI helpers."""

from __future__ import annotations

import json
import sys

import vigil.scripts.generate_fidelity_guards as script


class FakeResponse:
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "data": [
                    {
                        "id": "text-only",
                        "supported_endpoints": ["/chat/completions"],
                        "capabilities": {"supports": {"vision": False}},
                    },
                    {
                        "id": "claude-sonnet-4.6[1m]",
                        "supported_endpoints": ["/chat/completions"],
                        "capabilities": {"supports": {"vision": True}},
                    },
                ]
            }
        ).encode("utf-8")


def test_discover_default_model_prefers_vision_chat_model(monkeypatch) -> None:
    monkeypatch.setattr(script.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    assert script.discover_default_model("http://localhost:4141/models") == "claude-sonnet-4.6[1m]"


def _run_main(monkeypatch, tmp_path, argv: list[str]) -> dict:
    """Run main() with run_one_app stubbed; return the captured run_one_app kwargs."""
    captured: dict = {}

    def fake_run_one_app(**kwargs):
        captured.update(kwargs)
        return {"app": kwargs["spec"].name}

    monkeypatch.setattr(script, "run_one_app", fake_run_one_app)
    full_argv = [
        "vigil-fidelity-guards",
        "--apps",
        "market",
        "--report-root",
        str(tmp_path / "reports"),
        *argv,
    ]
    monkeypatch.setattr(sys, "argv", full_argv)
    script.main()
    return captured


def test_deterministic_skip_visual_builds_no_llm(monkeypatch, tmp_path) -> None:
    # Deterministic guard generation with --skip-visual must not query or construct a model.
    def _boom(*_a, **_k):
        raise AssertionError("LLM client must not be constructed in deterministic mode")

    monkeypatch.setattr(script, "LlmClient", _boom)
    monkeypatch.setattr(
        script,
        "discover_default_model",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model discovery called")),
    )

    captured = _run_main(
        monkeypatch, tmp_path, ["--skip-visual", "--guard-source", "deterministic"]
    )
    assert captured["guard_source"] == "deterministic"
    assert captured["llm"] is None


def test_hybrid_skip_visual_builds_llm(monkeypatch, tmp_path) -> None:
    # LLM/hybrid guard generation needs a client even when visual grounding is skipped.
    sentinel = object()
    monkeypatch.setattr(script, "discover_default_model", lambda *_a, **_k: "fake-model")
    monkeypatch.setattr(script, "LlmClient", lambda *_a, **_k: sentinel)

    captured = _run_main(monkeypatch, tmp_path, ["--skip-visual", "--guard-source", "hybrid"])
    assert captured["guard_source"] == "hybrid"
    assert captured["llm"] is sentinel
    assert captured["guard_prompt"] == script.DEFAULT_GUARD_PROMPT


def test_audit_skip_visual_builds_no_llm(monkeypatch, tmp_path) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("LLM client must not be constructed in audit mode")

    monkeypatch.setattr(script, "LlmClient", _boom)
    monkeypatch.setattr(
        script,
        "discover_default_model",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model discovery called")),
    )

    audit_root = tmp_path / "prior_reports"
    captured = _run_main(
        monkeypatch,
        tmp_path,
        [
            "--skip-visual",
            "--guard-source",
            "audit",
            "--guard-audit-root",
            str(audit_root),
        ],
    )
    assert captured["guard_source"] == "audit"
    assert captured["guard_audit_root"] == audit_root
    assert captured["llm"] is None


def test_invariant_deterministic_skip_all_builds_no_llm(monkeypatch, tmp_path) -> None:
    # Deterministic invariants with visual+guards skipped must not construct a model.
    def _boom(*_a, **_k):
        raise AssertionError("LLM client must not be constructed for deterministic invariants")

    monkeypatch.setattr(script, "LlmClient", _boom)
    monkeypatch.setattr(
        script,
        "discover_default_model",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model discovery called")),
    )

    captured = _run_main(
        monkeypatch,
        tmp_path,
        ["--skip-visual", "--skip-guards", "--invariant-source", "deterministic"],
    )
    assert captured["invariant_source"] == "deterministic"
    assert captured["skip_invariants"] is False
    assert captured["min_invariant_observations"] == 2
    assert captured["llm"] is None


def test_invariant_source_llm_builds_llm(monkeypatch, tmp_path) -> None:
    # The LLM invariant source needs a client even when visual + guards are skipped.
    sentinel = object()
    monkeypatch.setattr(script, "discover_default_model", lambda *_a, **_k: "fake-model")
    monkeypatch.setattr(script, "LlmClient", lambda *_a, **_k: sentinel)

    captured = _run_main(
        monkeypatch,
        tmp_path,
        ["--skip-visual", "--skip-guards", "--invariant-source", "llm"],
    )
    assert captured["invariant_source"] == "llm"
    assert captured["invariant_prompt"] == script.DEFAULT_INVARIANT_PROMPT
    assert captured["min_invariant_observations"] == 2
    assert captured["llm"] is sentinel


def test_min_invariant_observations_passthrough(monkeypatch, tmp_path) -> None:
    captured = _run_main(
        monkeypatch,
        tmp_path,
        [
            "--skip-visual",
            "--skip-guards",
            "--invariant-source",
            "deterministic",
            "--min-invariant-observations",
            "3",
        ],
    )
    assert captured["min_invariant_observations"] == 3


def test_skip_invariants_passthrough(monkeypatch, tmp_path) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("LLM client must not be constructed")

    monkeypatch.setattr(script, "LlmClient", _boom)
    monkeypatch.setattr(
        script,
        "discover_default_model",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model discovery called")),
    )

    captured = _run_main(
        monkeypatch,
        tmp_path,
        ["--skip-visual", "--skip-guards", "--skip-invariants"],
    )
    assert captured["skip_invariants"] is True
    assert captured["llm"] is None
