"""Run visual grounding + contract guard generation for fidelity app bundles."""

from __future__ import annotations

import argparse
import json
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
    "claude-sonnet-4.6",
    "gpt-5-mini",
    "gpt-5.4",
    "gemini-3.5-flash[1m]",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
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
        default=Path("output_docs/fidelity_guard_generation"),
        help="Directory for visual/guard generation reports.",
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
            "<report-root>/<app>/explored_fsm/fsm.json, traces from formal "
            "data/apps/com_vigil_* directories, and output to "
            "<report-root>/<app>/transition_guard/."
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
        default="deterministic",
        help=(
            "Guard contract source: deterministic rules, LLM contract, hybrid "
            "(LLM first, deterministic fallback), or audit replay (reuse prior LLM "
            "audit artifacts with no model calls)."
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
        "--guard-no-images",
        action="store_true",
        help="Disable source/target screenshot attachments for LLM guard generation.",
    )
    parser.add_argument(
        "--skip-invariants",
        action="store_true",
        help="Skip contract-first state-invariant generation.",
    )
    parser.add_argument(
        "--invariant-source",
        choices=["deterministic", "llm", "hybrid", "audit"],
        default="deterministic",
        help=(
            "State-invariant candidate source: deterministic rules, LLM packet, hybrid "
            "(LLM first, deterministic fallback), or audit replay (reuse prior packet "
            "audits with no model calls)."
        ),
    )
    parser.add_argument(
        "--invariant-prompt",
        default=DEFAULT_INVARIANT_PROMPT,
        help="System-prompt file name (under src/vigil/system_prompt/) for the LLM invariant path.",
    )
    parser.add_argument(
        "--invariant-no-images",
        action="store_true",
        help="Disable observation screenshot attachments for LLM invariant generation.",
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
        model = args.model or discover_default_model(args.models_url)
        logger.info(f"Using model {model!r} via {args.base_url}")
        llm = LlmClient(
            LLMConfig(
                provider="proxy",
                proxy_base_url=args.base_url,
                proxy_api_key="dummy_key",
                proxy_model=model,
                temperature=0.0,
            )
        )

    summary: list[dict[str, Any]] = []
    for spec in selected:
        summary.append(
            run_one_app(
                spec=spec,
                data_root=args.data_root,
                bundle_root=args.bundle_root,
                report_root=args.report_root,
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
                guard_use_images=not args.guard_no_images,
                guard_audit_root=args.guard_audit_root,
                skip_invariants=args.skip_invariants,
                invariant_source=args.invariant_source,
                invariant_prompt=args.invariant_prompt,
                invariant_use_images=not args.invariant_no_images,
                invariant_audit_root=args.invariant_audit_root,
                min_invariant_observations=args.min_invariant_observations,
            )
        )

    summary_path = args.report_root / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Summary written to {summary_path}")


def discover_default_model(models_url: str) -> str:
    """Select a vision-capable chat-completions model from the local model list."""
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
        endpoints = set(item.get("supported_endpoints") or [])
        supports = (item.get("capabilities") or {}).get("supports") or {}
        if "/chat/completions" in endpoints and supports.get("vision"):
            eligible.add(model_id)

    for model in _PREFERRED_MODELS:
        if model in eligible:
            return model
    if eligible:
        return sorted(eligible)[0]
    for model in _PREFERRED_MODELS:
        if model in ids:
            return model
    raise SystemExit("No suitable vision chat model found in local /models response")


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
    guard_source: str = "deterministic",
    guard_prompt: str = DEFAULT_GUARD_PROMPT,
    guard_use_images: bool = True,
    guard_audit_root: Path | None = None,
    skip_invariants: bool = False,
    invariant_source: str = "deterministic",
    invariant_prompt: str = DEFAULT_INVARIANT_PROMPT,
    invariant_use_images: bool = True,
    invariant_audit_root: Path | None = None,
    min_invariant_observations: int = 2,
) -> dict[str, Any]:
    if output_docs_layout:
        app_data_dir = data_root / spec.trace_package
        fsm_path = report_root / spec.output_slug / "explored_fsm" / "fsm.json"
        app_report_dir = report_root / spec.output_slug / "transition_guard"
    else:
        app_data_dir = data_root / spec.package
        bundle_dir = bundle_root / spec.package
        fsm_path = bundle_dir / "fsm.json"
        app_report_dir = report_root / spec.name
    if not fsm_path.exists():
        raise SystemExit(f"FSM bundle missing for {spec.name}: {fsm_path}")

    trace_path = _latest_trace_path(app_data_dir)
    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    raw_screens = trace_data.get("screens", {})
    if not isinstance(raw_screens, dict):
        raise SystemExit(f"Trace has no screens dict: {trace_path}")

    prior = _load_prior(app_data_dir)
    fsm = AppFSM.deserialize(fsm_path)
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

    guard_report: list[dict[str, Any]] = []
    if not skip_guards:
        action_schema_count = len({guard_action_schema_key(t.action) for t in fsm.transitions})
        logger.info(
            f"[{spec.name}] contract guard generation ({guard_source}) "
            f"{action_schema_count} guard action schemas over "
            f"{len(fsm.transitions)} edge transitions"
        )
        llm_audit_report = None
        if guard_source == "audit":
            audit_report_root = guard_audit_root or report_root
            audit_report_path = audit_report_root / spec.name / "guard_generation.json"
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
        )
        write_guard_generation_report(guard_report, app_report_dir / "guard_generation.json")

    invariant_report: list[dict[str, Any]] = []
    if not skip_invariants:
        logger.info(
            f"[{spec.name}] contract invariant generation ({invariant_source}) "
            f"over {len(fsm.states)} states"
        )
        invariant_audit_replay = None
        if invariant_source == "audit":
            audit_report_root = invariant_audit_root or report_root
            audit_report_path = audit_report_root / spec.name / "invariant_generation.json"
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
        )
        write_invariant_generation_report(
            invariant_report, app_report_dir / "invariant_generation.json"
        )

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
        "guards_required": sum(1 for t in fsm.transitions if t.requires_guard),
        "guards_semantic_incomplete": sum(
            1 for row in guard_report if row.get("semantic_binding_incomplete")
        ),
        "guard_origin_counts": _count_field(guard_report, "guard_origin"),
        "guard_status_counts": _guard_status_counts(fsm),
        "postcondition_status_counts": _postcondition_status_counts(fsm),
        "postconditions_attached": sum(1 for t in fsm.transitions if t.postcondition),
        "postconditions_unsupported_effects": sum(
            len(t.postcondition_unsupported_effects) for t in fsm.transitions
        ),
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


def _postcondition_status_counts(fsm: AppFSM) -> dict[str, int]:
    counts: dict[str, int] = {}
    for transition in fsm.transitions:
        status = transition.postcondition_admission_status
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
