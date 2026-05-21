"""CLI entry point: vigil-build.

Builds an FSM from exploration traces, optionally running the full pipeline:
  Stage 0  → App prior extraction (--manifest)
  Stage 1-3 → FSM construction from trace
  Stage 2.5 → Semantic grounding (--ground)
  Stage 4  → DSL guard generation (--generate-guards)

Usage:
    vigil-build --trace <trace.json>
    vigil-build --trace <trace.json> --manifest AndroidManifest.xml --ground
    vigil-build --trace <trace.json> --generate-guards
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loguru import logger

from vigil.core.paths import redirect_docs_output_path


def main() -> None:
    """Run the FSM construction pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-build",
        description="Build an FSM from exploration traces.",
    )
    parser.add_argument(
        "--trace",
        required=True,
        help="Path to exploration trace JSON file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for FSM JSON (default: models/bundles/<app_name>/fsm.json)",
    )
    parser.add_argument(
        "--app",
        default=None,
        help="Override app package name (auto-detected from trace)",
    )
    parser.add_argument(
        "--include-self-loops",
        action="store_true",
        help="Include self-loop transitions (action doesn't change state)",
    )
    parser.add_argument(
        "--generate-guards",
        action="store_true",
        help="Generate DSL guard expressions for transitions using LLM (Stage 4)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip screenshot input for guard generation (text-only prompts)",
    )
    parser.add_argument(
        "--fsm",
        default=None,
        help="Load existing FSM JSON instead of building from trace (use with --generate-guards)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to AndroidManifest.xml for Stage 0 app prior extraction",
    )
    parser.add_argument(
        "--ground",
        action="store_true",
        help="Run Stage 2.5 semantic grounding after FSM construction",
    )
    parser.add_argument(
        "--ground-icons",
        action="store_true",
        help="Include icon annotation in grounding (requires screenshots)",
    )
    parser.add_argument(
        "--mine-invariants",
        action="store_true",
        help="Run invariant mining in grounding (requires multi-visit data)",
    )
    parser.add_argument(
        "--prior-from-device",
        default=None,
        metavar="SERIAL",
        help="Extract Activity prior from connected device (provide ADB serial). "
        "For system apps without standalone manifest.",
    )
    parser.add_argument(
        "--apk-dir",
        default=None,
        help="Path to apktool-decompiled APK directory for resource extraction",
    )

    args = parser.parse_args()

    trace_path = Path(args.trace)
    if not trace_path.exists():
        logger.error(f"Trace file not found: {trace_path}")
        raise SystemExit(1)

    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    app_package = args.app or trace_data.get("app_package", "unknown")
    app_name = app_package.replace(".", "_")

    output_path = (
        redirect_docs_output_path(args.output)
        if args.output
        else Path(f"models/bundles/{app_name}/fsm.json")
    )

    # Stage 0: App prior extraction (optional)
    prior = None
    if args.manifest:
        from vigil.neuro.app_prior import AppPriorExtractor

        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            logger.error(f"Manifest not found: {manifest_path}")
            raise SystemExit(1)
        prior = AppPriorExtractor().extract_from_manifest(manifest_path)
        logger.info(
            f"Stage 0: extracted prior from manifest for {prior.package_name} "
            f"({len(prior.activities)} activities, entry={prior.entry_activity})"
        )
    elif args.prior_from_device:
        from vigil.neuro.app_prior import AppPriorExtractor

        prior = AppPriorExtractor().extract_from_device_serial(args.prior_from_device, app_package)
        logger.info(
            f"Stage 0: extracted prior from device for {prior.package_name} "
            f"({len(prior.activities)} activities)"
        )
    else:
        # Auto-discover the cached prior written by ``vigil-explore``. The
        # new canonical layout is ``<app_dir>/static/app_prior.json`` (read
        # via ``AppPrior.load(static_dir)``); older runs wrote the JSON in
        # legacy locations, so fall back to those via ``load_file``.
        from vigil.neuro.app_prior import AppPrior

        app_dir = trace_path.parent.parent
        static_dir = app_dir / "static"

        prior_candidates: list[tuple[Path, str]] = [
            (static_dir, "dir"),
            (static_dir / "prior.json", "file"),
            (app_dir / "prior.json", "file"),
        ]
        for candidate, mode in prior_candidates:
            try:
                if mode == "dir":
                    if not (candidate / "app_prior.json").exists():
                        continue
                    prior = AppPrior.load(candidate)
                else:
                    if not candidate.exists():
                        continue
                    prior = AppPrior.load_file(candidate)
            except Exception as exc:
                logger.warning(f"Failed to load cached prior at {candidate}: {exc}")
                continue
            logger.info(
                f"Stage 0: loaded cached app prior from {candidate} "
                f"({len(prior.activities)} activities)"
            )
            break

    # Extract APK resources if apk-dir provided
    if args.apk_dir:
        from vigil.neuro.app_prior import AppPriorExtractor

        apk_dir_path = Path(args.apk_dir)
        if apk_dir_path.is_dir():
            if prior is None:
                manifest = apk_dir_path / "AndroidManifest.xml"
                if manifest.exists():
                    prior = AppPriorExtractor().extract_from_manifest(manifest)
                else:
                    from vigil.neuro.app_prior import AppPrior

                    prior = AppPrior(package_name=app_package)
            AppPriorExtractor().extract_resources(apk_dir_path, prior)
        else:
            logger.warning(f"APK directory not found: {apk_dir_path}")

    # Stages 1-3: Build or load FSM
    if args.fsm:
        from vigil.models.fsm import AppFSM

        fsm_path = Path(args.fsm)
        if not fsm_path.exists():
            logger.error(f"FSM file not found: {fsm_path}")
            raise SystemExit(1)
        fsm = AppFSM.deserialize(fsm_path)
        logger.info(f"Loaded existing FSM from {fsm_path}: {fsm}")
    else:
        from vigil.neuro.fsm_builder import FsmBuilder

        builder = FsmBuilder(app_package=app_package)
        fsm = builder.build_from_trace(
            trace_path=trace_path,
            include_self_loops=args.include_self_loops,
            app_prior=prior,
        )

    # Stage 2.5: Semantic grounding (optional)
    if args.ground:
        from vigil.core.config import VigilConfig
        from vigil.core.llm_client import LlmClient
        from vigil.neuro.semantic_grounder import SemanticGrounder

        config = VigilConfig.from_yaml("configs/default.yaml")
        llm = LlmClient(config.llm)
        grounder = SemanticGrounder(llm)
        raw_screens = trace_data.get("screens", {})
        fsm = grounder.ground_all_states(fsm, raw_screens, prior, trace_data)
        logger.info("Stage 2.5: semantic grounding complete")

    # Stage 4: DSL guard generation (optional)
    if args.generate_guards:
        from vigil.core.config import VigilConfig
        from vigil.neuro.dsl_generator import DslGenerator

        config = VigilConfig.from_yaml("configs/default.yaml")
        generator = DslGenerator(fsm=fsm, config=config, app_prior=prior)
        fsm = generator.generate_all_guards(
            trace_path=trace_path,
            use_images=not args.no_images,
        )

    # Serialize
    fsm.serialize(output_path)
    logger.info(f"FSM saved to {output_path}")
    logger.info(f"Summary: {fsm}")


if __name__ == "__main__":
    main()
