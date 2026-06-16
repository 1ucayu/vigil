"""Tests for the fidelity guard-generation CLI helpers."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

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


class StructuredFakeResponse:
    def __enter__(self) -> StructuredFakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "data": [
                    {
                        "id": "vision-only",
                        "vendor": "Unknown",
                        "supported_endpoints": ["/chat/completions"],
                        "capabilities": {"supports": {"vision": True}},
                    },
                    {
                        "id": "gemini-3.5-flash",
                        "vendor": "Google",
                        "supported_endpoints": ["/chat/completions"],
                        "capabilities": {"supports": {"vision": True, "tool_calls": True}},
                    },
                ]
            }
        ).encode("utf-8")


def test_discover_default_model_requires_structured_strategy(monkeypatch) -> None:
    monkeypatch.setattr(
        script.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: StructuredFakeResponse(),
    )

    assert (
        script.discover_default_model(
            "http://localhost:4141/models",
            require_structured=True,
        )
        == "gemini-3.5-flash"
    )


class FakeStructuredResponse:
    parsed = object()
    model = "fake-model"
    strategy = "chat_function_tool"
    schema_constraint_mode = "tool_schema"
    validation_errors: list[str] = []
    raw_text = ""
    refusal = None


class FakeLlm:
    def probe_structured_output(self, *_args, **_kwargs) -> FakeStructuredResponse:
        return FakeStructuredResponse()


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


def test_default_guard_and_invariant_sources_build_llm(monkeypatch, tmp_path) -> None:
    sentinel = FakeLlm()
    monkeypatch.setattr(script, "discover_default_model", lambda *_a, **_k: "fake-model")
    monkeypatch.setattr(script, "LlmClient", lambda *_a, **_k: sentinel)

    captured = _run_main(monkeypatch, tmp_path, ["--skip-visual"])

    assert captured["guard_source"] == "llm"
    assert captured["invariant_source"] == "llm"
    assert captured["guard_use_images"] is False
    assert captured["invariant_use_images"] is False
    assert captured["llm"] is sentinel


def test_image_attachment_is_explicit_opt_in(monkeypatch, tmp_path) -> None:
    sentinel = FakeLlm()
    monkeypatch.setattr(script, "discover_default_model", lambda *_a, **_k: "fake-model")
    monkeypatch.setattr(script, "LlmClient", lambda *_a, **_k: sentinel)

    captured = _run_main(
        monkeypatch,
        tmp_path,
        ["--skip-visual", "--guard-with-images", "--invariant-with-images"],
    )

    assert captured["guard_use_images"] is True
    assert captured["invariant_use_images"] is True


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
        monkeypatch,
        tmp_path,
        [
            "--skip-visual",
            "--guard-source",
            "deterministic",
            "--invariant-source",
            "deterministic",
        ],
    )
    assert captured["guard_source"] == "deterministic"
    assert captured["llm"] is None


def test_hybrid_skip_visual_builds_llm(monkeypatch, tmp_path) -> None:
    # LLM/hybrid guard generation needs a client even when visual grounding is skipped.
    sentinel = FakeLlm()
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
            "--invariant-source",
            "deterministic",
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
    sentinel = FakeLlm()
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


def test_structured_preflight_runs_before_clean_output(monkeypatch, tmp_path) -> None:
    class FailingLlm:
        def probe_structured_output(self, *_args, **_kwargs):
            return SimpleNamespace(
                parsed=None,
                model="fake-model",
                strategy="",
                schema_constraint_mode="prompt_only_unavailable",
                validation_errors=["no structured strategy"],
                raw_text="",
                refusal=None,
            )

    app_report_dir = tmp_path / "reports" / "market"
    app_report_dir.mkdir(parents=True)
    marker = app_report_dir / "keep.txt"
    marker.write_text("old output", encoding="utf-8")

    monkeypatch.setattr(script, "discover_default_model", lambda *_a, **_k: "fake-model")
    monkeypatch.setattr(script, "LlmClient", lambda *_a, **_k: FailingLlm())
    monkeypatch.setattr(
        script,
        "run_one_app",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("run_one_app called")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vigil-fidelity-guards",
            "--apps",
            "market",
            "--report-root",
            str(tmp_path / "reports"),
            "--skip-visual",
            "--clean-output",
        ],
    )

    with pytest.raises(SystemExit, match="Structured output preflight failed"):
        script.main()
    assert marker.exists()


def test_structured_preflight_checks_real_guard_and_invariant_schemas() -> None:
    class CountingLlm:
        def __init__(self) -> None:
            self.schemas: list[str] = []
            self.allow_flags: list[bool] = []

        def probe_structured_output(
            self,
            _response_model,
            schema_name: str,
            *,
            allow_provider_fallback: bool = False,
        ):
            self.schemas.append(schema_name)
            self.allow_flags.append(allow_provider_fallback)
            return SimpleNamespace(
                parsed=object(),
                model="fake-model",
                strategy="tool_schema",
                schema_constraint_mode="tool_schema",
                validation_errors=[],
                raw_text="",
                refusal=None,
            )

    llm = CountingLlm()
    script.preflight_structured_output(llm)
    assert llm.schemas == ["LlmTransitionGuardResponse", "LlmInvariantGuardResponse"]
    assert llm.allow_flags == [False, False]

    llm_with_fallback = CountingLlm()
    script.preflight_structured_output(llm_with_fallback, allow_provider_fallback=True)
    assert llm_with_fallback.schemas == [
        "LlmTransitionGuardResponse",
        "LlmInvariantGuardResponse",
    ]
    assert llm_with_fallback.allow_flags == [True, True]
