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


def main() -> None:
    """Run the FSM construction pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-build",
        description="Build an FSM from exploration traces.",
    )
    parser.add_argument(
        "--trace",
        default=None,
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
        "--from-droidbot",
        default=None,
        metavar="DIR",
        help="Parse DroidBot output directory and build FSM from it",
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

    args = parser.parse_args()

    # Handle --from-droidbot: parse DroidBot output into a Vigil trace first
    if args.from_droidbot:
        from vigil.core.config import VigilConfig
        from vigil.neuro.droidbot_explorer import DroidBotExplorer
        from vigil.neuro.droidbot_parser import DroidBotParser

        db_dir = Path(args.from_droidbot)
        if not db_dir.exists():
            logger.error(f"DroidBot output not found: {db_dir}")
            raise SystemExit(1)

        app_package = args.app or "unknown"
        parser_obj = DroidBotParser(db_dir, app_package)
        exploration_result = parser_obj.parse()
        app_package = exploration_result.app_package or app_package

        app_name = app_package.rsplit(".", maxsplit=1)[-1]
        out_dir = Path(f"data/apps/{app_name}")
        out_dir.mkdir(parents=True, exist_ok=True)

        saver = DroidBotExplorer.__new__(DroidBotExplorer)
        saver._serial = ""
        saver._app_package = app_package
        saver._config = VigilConfig()
        saver._output_dir = out_dir
        saver._save_trace(exploration_result)

        trace_files = sorted((out_dir / "traces").glob("exploration_*.json"))
        if not trace_files:
            logger.error("Failed to save DroidBot trace")
            raise SystemExit(1)
        trace_path = trace_files[-1]
        logger.info(f"DroidBot output converted to trace: {trace_path}")
    elif args.trace:
        trace_path = Path(args.trace)
    else:
        logger.error("Either --trace or --from-droidbot is required")
        raise SystemExit(1)

    if not trace_path.exists():
        logger.error(f"Trace file not found: {trace_path}")
        raise SystemExit(1)

    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    app_package = args.app or trace_data.get("app_package", "unknown")
    app_name = app_package.rsplit(".", maxsplit=1)[-1]

    output_path = Path(args.output) if args.output else Path(f"models/bundles/{app_name}/fsm.json")

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
        prior_path = trace_path.parent.parent / "prior.json"
        if prior_path.exists():
            from vigil.neuro.app_prior import AppPrior

            prior = AppPrior(**json.loads(prior_path.read_text(encoding="utf-8")))
            logger.info(
                f"Stage 0: loaded prior from {prior_path} ({len(prior.activities)} activities)"
            )

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
        generator = DslGenerator(fsm=fsm, config=config)
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
