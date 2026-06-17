"""Run visual grounding + contract guard generation for fidelity app bundles."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.config import LLMConfig
from vigil.core.llm_client import LlmClient
from vigil.models.fsm import AppFSM
from vigil.models.llm_structured import LlmInvariantGuardResponse, LlmTransitionGuardResponse
from vigil.neuro.app_prior import AppPrior
from vigil.neuro.guard_contract_llm import DEFAULT_GUARD_PROMPT
from vigil.neuro.guard_generation_pipeline import (
    generate_contract_guards,
    guard_action_schema_key,
    write_guard_generation_report,
)
from vigil.neuro.invariant_generation_pipeline import (
    generate_contract_invariants,
    write_invariant_generation_report,
)
from vigil.neuro.invariant_guard_llm import DEFAULT_INVARIANT_PROMPT
from vigil.neuro.visual_grounder import (
    ground_fsm_visual_annotations,
    write_visual_grounding_report,
)


@dataclass(frozen=True)
class FidelityAppSpec:
    name: str
    package: str
    trace_package: str
    output_slug: str


FIDELITY_APPS: tuple[FidelityAppSpec, ...] = (
    FidelityAppSpec("market", "com_vigil_market_fidelity", "com_vigil_market", "vigilmarket"),
    FidelityAppSpec("bank", "com_vigil_bank_fidelity", "com_vigil_bank", "vigilbank"),
    FidelityAppSpec("chat", "com_vigil_chat_fidelity", "com_vigil_chat", "vigilchat"),
    FidelityAppSpec("clock", "com_vigil_clock_fidelity", "com_vigil_clock", "vigilclock"),
)

_PREFERRED_MODELS = (
    # Prefer the explicitly tested local proxy model before falling back to other
    # vision-capable chat-completions models.
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4.6",
    "gpt-5-mini",
    "gpt-5.4",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "claude-haiku-4.5",
    "claude-sonnet-4.6[1m]",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vigil-fidelity-guards",
        description="Generate screenshot visual annotations and contract guards for fidelity apps.",
    )
    parser.add_argument(
        "--apps",
        nargs="+",
        default=["market", "bank", "chat", "clock"],
        choices=[spec.name for spec in FIDELITY_APPS],
        help="Fidelity apps to process.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI-compatible model id. Defaults to the first preferred vision chat model.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:4141/v1",
        help="OpenAI-compatible chat completions base URL.",
    )
    parser.add_argument(
        "--models-url",
        default="http://localhost:4141/models",
        help="Model-list URL used when --model is omitted.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/apps"),
        help="Root containing fidelity app traces/screens/static priors.",
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=Path("models/bundles"),
        help="Root containing generated FSM bundles.",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=Path("output_docs"),
        help=(
            "Base directory for visual/guard generation reports. When a model is selected, "
            "outputs are written under <report-root>/<model>/..."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional output root. Defaults to overwriting the input bundle fsm.json.",
    )
    parser.add_argument(
        "--output-docs-layout",
        action="store_true",
        help=(
            "Use output_docs fidelity layout: input from "
            "<report-root>/<model>/<app>/explored_fsm/fsm.json, traces from formal "
            "data/apps/com_vigil_* directories, and output to "
            "<report-root>/<model>/<app>/transition_guard/."
        ),
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete each app's guard output/report directory before regenerating it.",
    )
    parser.add_argument(
        "--skip-visual",
        action="store_true",
        help="Skip LLM screenshot/layout grounding and only generate guards.",
    )
    parser.add_argument(
        "--skip-guards",
        action="store_true",
        help="Only generate visual annotations; do not attach contract guards.",
    )
    parser.add_argument(
        "--guard-source",
        choices=["deterministic", "llm", "hybrid", "audit"],
        default="llm",
        help=(
            "Guard contract source: LLM contract, deterministic rules for ablation, "
            "hybrid (LLM first, deterministic fallback), or audit replay (reuse "
            "prior LLM audit artifacts with no model calls)."
        ),
    )
    parser.add_argument(
        "--guard-audit-root",
        type=Path,
        default=None,
        help=(
            "Report root containing prior per-app guard_generation.json files for "
            "--guard-source audit. Defaults to --report-root."
        ),
    )
    parser.add_argument(
        "--guard-prompt",
        default=DEFAULT_GUARD_PROMPT,
        help="System-prompt file name (under src/vigil/system_prompt/) for the LLM path.",
    )
    parser.add_argument(
        "--guard-with-images",
        action="store_true",
        help=(
            "Opt in to source/target screenshot attachments for LLM guard generation. "
            "Off by default: guard synthesis consumes the visual caption cache from "
            "the visual grounding stage."
        ),
    )
    parser.add_argument(
        "--skip-invariants",
        action="store_true",
        help="Skip contract-first state-invariant generation.",
    )
    parser.add_argument(
        "--invariant-source",
        choices=["deterministic", "llm", "hybrid", "audit"],
        default="llm",
        help=(
            "State-invariant candidate source: LLM packet, deterministic rules for "
            "ablation, hybrid (LLM first, deterministic fallback), or audit replay "
            "(reuse prior packet audits with no model calls)."
        ),
    )
    parser.add_argument(
        "--invariant-prompt",
        default=DEFAULT_INVARIANT_PROMPT,
        help="System-prompt file name (under src/vigil/system_prompt/) for the LLM invariant path.",
    )
    parser.add_argument(
        "--invariant-with-images",
        action="store_true",
        help=(
            "Opt in to observation screenshot attachments for LLM invariant generation. "
            "Off by default: invariant synthesis consumes the visual caption cache from "
            "the visual grounding stage."
        ),
    )
    parser.add_argument(
        "--invariant-audit-root",
        type=Path,
        default=None,
        help=(
            "Report root containing prior per-app invariant_generation.json files for "
            "--invariant-source audit. Defaults to --report-root."
        ),
    )
    parser.add_argument(
        "--min-invariant-observations",
        type=int,
        default=2,
        help="Minimum replay observations required before attaching a runtime state invariant.",
    )
    parser.add_argument(
        "--allow-provider-fallback",
        action="store_true",
        help=(
            "Opt in to the explicitly-recorded fallback_validate mode when a provider/proxy "
            "cannot honor native structured output. Off by default: the llm path then fails "
            "clearly (prompt_only_unavailable) instead of silently degrading to prompt-only JSON."
        ),
    )
    parser.add_argument(
        "--force-visual",
        action="store_true",
        help="Regenerate visual annotations even when state annotations already exist.",
    )
    parser.add_argument(
        "--max-states",
        type=int,
        default=None,
        help="Debug limit for visual grounding states per app.",
    )
    args = parser.parse_args()

    selected = [spec for spec in FIDELITY_APPS if spec.name in set(args.apps)]
    model = args.model
    # The LLM client is needed for visual grounding and for the LLM/hybrid guard/invariant
    # sources. Deterministic and audit generation must never query or construct the model.
    need_llm_for_guards = (not args.skip_guards) and args.guard_source in ("llm", "hybrid")
    need_llm_for_invariants = (not args.skip_invariants) and args.invariant_source in (
        "llm",
        "hybrid",
    )
    need_llm = (not args.skip_visual) or need_llm_for_guards or need_llm_for_invariants
    llm = None
    if need_llm:
        model = args.model or discover_default_model(
            args.models_url,
            require_structured=need_llm_for_guards or need_llm_for_invariants,
        )
        logger.info(f"Using model {model!r} via {args.base_url}")
        llm = LlmClient(
            LLMConfig(
                provider="proxy",
                proxy_base_url=args.base_url,
                proxy_models_url=args.models_url,
                proxy_api_key="dummy_key",
                proxy_model=model,
            )
        )
        if need_llm_for_guards or need_llm_for_invariants:
            preflight_structured_output(
                llm,
                check_guard=need_llm_for_guards,
                check_invariant=need_llm_for_invariants,
                allow_provider_fallback=args.allow_provider_fallback,
            )

    report_root = model_scoped_report_root(args.report_root, model)
    summary: list[dict[str, Any]] = []
    for spec in selected:
        summary.append(
            run_one_app(
                spec=spec,
                data_root=args.data_root,
                bundle_root=args.bundle_root,
                report_root=report_root,
                output_root=args.output_root,
                output_docs_layout=args.output_docs_layout,
                clean_output=args.clean_output,
                llm=llm,
                skip_visual=args.skip_visual,
                skip_guards=args.skip_guards,
                force_visual=args.force_visual,
                max_states=args.max_states,
                guard_source=args.guard_source,
                guard_prompt=args.guard_prompt,
                guard_use_images=args.guard_with_images,
                guard_audit_root=args.guard_audit_root,
                skip_invariants=args.skip_invariants,
                invariant_source=args.invariant_source,
                invariant_prompt=args.invariant_prompt,
                invariant_use_images=args.invariant_with_images,
                invariant_audit_root=args.invariant_audit_root,
                min_invariant_observations=args.min_invariant_observations,
                allow_provider_fallback=args.allow_provider_fallback,
            )
        )

    summary_path = report_root / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Summary written to {summary_path}")


def sanitize_model_output_dir(model: str | None) -> str | None:
    """Return a filesystem-safe model directory name without changing readable ids."""
    if model is None:
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", model.strip())
    slug = slug.strip("._-")
    return slug or None


def model_scoped_report_root(report_root: Path, model: str | None) -> Path:
    """Scope report output by model id, avoiding duplicate model path segments."""
    model_slug = sanitize_model_output_dir(model)
    if model_slug is None:
        return report_root
    if report_root.name == model_slug:
        return report_root
    return report_root / model_slug


def discover_default_model(models_url: str, *, require_structured: bool = False) -> str:
    """Select a vision-capable model from the local model list.

    When guard/invariant LLM generation is active, prefer models whose metadata exposes at
    least one structured-output strategy. A live preflight still validates the selected
    strategy before any output directory is cleaned.
    """
    try:
        with urllib.request.urlopen(models_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Failed to query models from {models_url}: {exc}") from exc

    data = payload.get("data", []) if isinstance(payload, dict) else []
    ids: set[str] = set()
    eligible: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        ids.add(model_id)
        supports = (item.get("capabilities") or {}).get("supports") or {}
        if supports.get("vision") and (
            not require_structured or _model_has_structured_strategy(item)
        ):
            eligible.add(model_id)

    for model in _PREFERRED_MODELS:
        if model in eligible:
            return model
    if eligible:
        return sorted(eligible)[0]
    for model in _PREFERRED_MODELS:
        if model in ids:
            return model
    requirement = "vision + structured" if require_structured else "vision"
    raise SystemExit(f"No suitable {requirement} model found in local /models response")


def _model_has_structured_strategy(item: dict[str, Any]) -> bool:
    vendor = str(item.get("vendor") or item.get("owned_by") or "").lower()
    model_id = str(item.get("id") or "")
    endpoints = set(item.get("supported_endpoints") or [])
    supports = (item.get("capabilities") or {}).get("supports") or {}
    no_endpoint_metadata = not endpoints

    def has(endpoint: str) -> bool:
        return no_endpoint_metadata or endpoint in endpoints

    has_tools = bool(supports.get("tool_calls"))
    has_structured = bool(supports.get("structured_outputs"))
    is_anthropic = "anthropic" in vendor or model_id.startswith("claude")
    is_google = "google" in vendor or model_id.startswith("gemini")
    is_openai = "openai" in vendor or "azure" in vendor or model_id.startswith(("gpt", "mai-"))

    if is_anthropic:
        return has_tools and (has("/v1/messages") or has("/chat/completions"))
    if is_google:
        return has_tools and has("/chat/completions")
    if is_openai:
        return (has_structured and (has("/responses") or has("/chat/completions"))) or (
            has_tools and has("/chat/completions")
        )
    return (has_tools and (has("/v1/messages") or has("/chat/completions"))) or (
        has_structured and (has("/responses") or has("/chat/completions"))
    )


def preflight_structured_output(
    llm: LlmClient,
    *,
    check_guard: bool = True,
    check_invariant: bool = True,
    allow_provider_fallback: bool = False,
) -> None:
    """Fail before any per-app output cleanup when strict structured output is unavailable."""
    checks: list[tuple[str, type[Any]]] = []
    if check_guard:
        checks.append(("LlmTransitionGuardResponse", LlmTransitionGuardResponse))
    if check_invariant:
        checks.append(("LlmInvariantGuardResponse", LlmInvariantGuardResponse))
    for schema_name, response_model in checks:
        if allow_provider_fallback:
            result = llm.probe_structured_output(
                response_model,
                schema_name,
                allow_provider_fallback=True,
            )
        else:
            result = llm.probe_structured_output(response_model, schema_name)
        if result.parsed is not None:
            logger.info(
                "Structured output preflight passed: "
                f"schema={schema_name} model={result.model} strategy={result.strategy} "
                f"mode={result.schema_constraint_mode}"
            )
            continue
        detail = (
            "; ".join(result.validation_errors) or result.raw_text or result.refusal or "unknown"
        )
        raise SystemExit(
            "Structured output preflight failed before modifying outputs: "
            f"schema={schema_name} model={result.model} mode={result.schema_constraint_mode} "
            f"strategy={result.strategy or 'none'} detail={detail}"
        )
    if not checks:
        logger.info("Structured output preflight skipped: no guard/invariant LLM schema requested")


def run_one_app(
    *,
    spec: FidelityAppSpec,
    data_root: Path,
    bundle_root: Path,
    report_root: Path,
    output_root: Path | None,
    llm: LlmClient | None,
    skip_visual: bool,
    skip_guards: bool,
    force_visual: bool,
    max_states: int | None,
    output_docs_layout: bool = False,
    clean_output: bool = False,
    guard_source: str = "llm",
    guard_prompt: str = DEFAULT_GUARD_PROMPT,
    guard_use_images: bool = False,
    guard_audit_root: Path | None = None,
    skip_invariants: bool = False,
    invariant_source: str = "llm",
    invariant_prompt: str = DEFAULT_INVARIANT_PROMPT,
    invariant_use_images: bool = False,
    invariant_audit_root: Path | None = None,
    min_invariant_observations: int = 2,
    allow_provider_fallback: bool = False,
) -> dict[str, Any]:
    if output_docs_layout:
        app_data_dir = data_root / spec.trace_package
        fsm_path = report_root / spec.output_slug / "explored_fsm" / "fsm.json"
        app_report_dir = _app_report_dir(report_root, spec, output_docs_layout=True)
    else:
        app_data_dir = data_root / spec.package
        bundle_dir = bundle_root / spec.package
        fsm_path = bundle_dir / "fsm.json"
        app_report_dir = _app_report_dir(report_root, spec, output_docs_layout=False)
    if not fsm_path.exists():
        raise SystemExit(f"FSM bundle missing for {spec.name}: {fsm_path}")

    trace_path = _latest_trace_path(app_data_dir)
    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    raw_screens = trace_data.get("screens", {})
    if not isinstance(raw_screens, dict):
        raise SystemExit(f"Trace has no screens dict: {trace_path}")

    prior = _load_prior(app_data_dir)
    fsm = AppFSM.deserialize(fsm_path)
    # Identifiers the prompt redactor must mask (config/evidence-driven, not a hardcoded
    # blacklist): the bundle package, the trace package, and the output slug for this app.
    redact_identifiers = [spec.package, spec.trace_package, spec.output_slug, spec.name]
    if clean_output and app_report_dir.exists():
        shutil.rmtree(app_report_dir)
    app_report_dir.mkdir(parents=True, exist_ok=True)

    visual_report: list[dict[str, Any]] = []
    if not skip_visual:
        assert llm is not None
        logger.info(f"[{spec.name}] visual grounding {len(fsm.states)} states")
        visual_report = ground_fsm_visual_annotations(
            fsm,
            raw_screens,
            llm,
            prior,
            force=force_visual,
            max_states=max_states,
        )
        write_visual_grounding_report(visual_report, app_report_dir / "visual_grounding.json")

    invariant_report: list[dict[str, Any]] = []
    if not skip_invariants:
        logger.info(
            f"[{spec.name}] contract invariant generation ({invariant_source}) "
            f"over {len(fsm.states)} states before transition guard generation"
        )
        invariant_audit_replay = None
        if invariant_source == "audit":
            audit_report_root = invariant_audit_root or report_root
            audit_report_path = _app_audit_report_path(
                audit_report_root,
                spec,
                output_docs_layout=output_docs_layout,
                filename="invariant_generation.json",
            )
            if not audit_report_path.exists():
                raise SystemExit(
                    f"Invariant audit report missing for {spec.name}: {audit_report_path}"
                )
            loaded = json.loads(audit_report_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise SystemExit(f"Invariant audit report is not a list: {audit_report_path}")
            invariant_audit_replay = loaded
        invariant_report = generate_contract_invariants(
            fsm,
            raw_screens,
            prior,
            invariant_source=invariant_source,  # type: ignore[arg-type]
            llm=llm if invariant_source in ("llm", "hybrid") else None,
            invariant_prompt=invariant_prompt,
            use_images=invariant_use_images,
            llm_audit_dir=(
                app_report_dir / "llm_invariant_attempts"
                if invariant_source in ("llm", "hybrid")
                else None
            ),
            llm_audit_report=invariant_audit_replay,
            min_runtime_observations=min_invariant_observations,
            redact_identifiers=redact_identifiers,
            allow_provider_fallback=allow_provider_fallback,
        )
        write_invariant_generation_report(
            invariant_report, app_report_dir / "invariant_generation.json"
        )

    guard_report: list[dict[str, Any]] = []
    if not skip_guards:
        action_schema_count = len({guard_action_schema_key(t.action) for t in fsm.transitions})
        logger.info(
            f"[{spec.name}] transition guard generation ({guard_source}) "
            f"{action_schema_count} guard action schemas over "
            f"{len(fsm.transitions)} edge transitions"
        )
        llm_audit_report = None
        if guard_source == "audit":
            audit_report_root = guard_audit_root or report_root
            audit_report_path = _app_audit_report_path(
                audit_report_root,
                spec,
                output_docs_layout=output_docs_layout,
                filename="guard_generation.json",
            )
            if not audit_report_path.exists():
                raise SystemExit(f"Guard audit report missing for {spec.name}: {audit_report_path}")
            loaded = json.loads(audit_report_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise SystemExit(f"Guard audit report is not a list: {audit_report_path}")
            llm_audit_report = loaded
        guard_report = generate_contract_guards(
            fsm,
            raw_screens,
            prior,
            guard_source=guard_source,  # type: ignore[arg-type]
            llm=llm if guard_source in ("llm", "hybrid") else None,
            guard_prompt=guard_prompt,
            guard_use_images=guard_use_images,
            llm_audit_dir=(
                app_report_dir / "llm_guard_attempts" if guard_source in ("llm", "hybrid") else None
            ),
            llm_audit_report=llm_audit_report,
            redact_identifiers=redact_identifiers,
            allow_provider_fallback=allow_provider_fallback,
        )
        write_guard_generation_report(guard_report, app_report_dir / "guard_generation.json")

    output_path = (
        app_report_dir / "fsm.json"
        if output_docs_layout
        else _output_fsm_path(spec.package, fsm_path, output_root)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fsm.serialize(output_path)
    logger.info(f"[{spec.name}] enriched FSM written to {output_path}")

    summary = {
        "app": spec.name,
        "package": spec.package,
        "trace_package": spec.trace_package,
        "input_fsm": str(fsm_path),
        "output_fsm": str(output_path),
        "trace": str(trace_path),
        "states": len(fsm.states),
        "transitions": len(fsm.transitions),
        "visual_annotated": sum(1 for row in visual_report if row.get("status") == "annotated"),
        "visual_failed": sum(1 for row in visual_report if row.get("status") == "failed"),
        "guard_source": guard_source,
        "guard_use_images": guard_use_images,
        "guards_attached": sum(1 for t in fsm.transitions if t.guard),
        "guard_origin_counts": _count_field(guard_report, "guard_origin"),
        "guard_status_counts": _guard_status_counts(fsm),
        "invariant_source": invariant_source,
        "invariants_attached": sum(len(s.invariant_specs) for s in fsm.states.values()),
        "invariant_states": sum(1 for s in fsm.states.values() if s.invariant_specs),
        "invariants_admitted_run": sum(
            len(row.get("invariants_admitted", [])) for row in invariant_report
        ),
        "invariant_hints": sum(len(row.get("effect_hints", [])) for row in invariant_report),
        "invariants_rejected": sum(len(row.get("rejected", [])) for row in invariant_report),
        "min_invariant_observations": min_invariant_observations,
    }
    (app_report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def _app_report_dir(
    report_root: Path,
    spec: FidelityAppSpec,
    *,
    output_docs_layout: bool,
) -> Path:
    if output_docs_layout:
        return report_root / spec.output_slug / "transition_guard"
    return report_root / spec.name


def _app_audit_report_path(
    report_root: Path,
    spec: FidelityAppSpec,
    *,
    output_docs_layout: bool,
    filename: str,
) -> Path:
    if output_docs_layout:
        return (
            _app_report_dir(
                report_root,
                spec,
                output_docs_layout=True,
            )
            / filename
        )
    return report_root / spec.name / filename


def _latest_trace_path(app_data_dir: Path) -> Path:
    traces = sorted((app_data_dir / "traces").glob("*.json"))
    if not traces:
        raise SystemExit(f"No traces found under {app_data_dir / 'traces'}")
    return traces[-1]


def _load_prior(app_data_dir: Path) -> AppPrior | None:
    prior_path = app_data_dir / "static" / "app_prior.json"
    if not prior_path.exists():
        return None
    return AppPrior.load_file(prior_path)


def _output_fsm_path(package: str, input_fsm: Path, output_root: Path | None) -> Path:
    if output_root is None:
        return input_fsm
    return output_root / package / "fsm.json"


def _guard_status_counts(fsm: AppFSM) -> dict[str, int]:
    counts: dict[str, int] = {}
    for transition in fsm.transitions:
        status = transition.guard_admission_status
        key = str(getattr(status, "value", status or "none"))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_field(report: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in report:
        key = str(row.get(field) or "none")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    main()
