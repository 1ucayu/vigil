"""CLI entry point: vigil-explore.

Explores an Android app and generates exploration traces.

Usage:
    vigil-explore --app com.android.settings --serial a8e2da20 --steps 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger


def main() -> None:
    """Run the app exploration pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-explore",
        description="Explore an Android app and generate exploration traces.",
    )
    parser.add_argument(
        "--app",
        required=True,
        help="Android package name (e.g., com.android.settings)",
    )
    parser.add_argument(
        "--serial",
        default=None,
        help="ADB device serial (default: auto-detect first device)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Max exploration steps (overrides config)",
    )
    parser.add_argument(
        "--strategy",
        choices=["bfs", "dfs", "hybrid"],
        default=None,
        help="Exploration strategy (overrides config)",
    )
    parser.add_argument(
        "--backend",
        choices=["native", "ape"],
        default=None,
        help="Exploration backend: 'native' (uiautomator2) or 'ape' (APE CEGAR)",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=None,
        help="APE running time in minutes (only for --backend ape)",
    )
    parser.add_argument(
        "--ape-jar",
        default=None,
        help="Path to ape.jar (only for --backend ape)",
    )
    parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="Path to config YAML (default: configs/default.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: data/apps/<app_name>/)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to AndroidManifest.xml for Activity coverage guidance",
    )
    parser.add_argument(
        "--prior-from-device",
        action="store_true",
        help="Extract Activity prior from connected device via 'adb dumpsys' "
        "(for system apps without standalone manifest)",
    )

    args = parser.parse_args()

    # Load config
    from vigil.core.config import VigilConfig

    config_path = Path(args.config)
    if config_path.exists():
        config = VigilConfig.from_yaml(config_path)
    else:
        logger.warning(f"Config file not found: {config_path}, using defaults")
        config = VigilConfig()

    # Apply CLI overrides
    if args.steps is not None:
        config.app.max_exploration_steps = args.steps
    if args.strategy is not None:
        config.app.exploration_strategy = args.strategy
    if args.minutes is not None:
        config.ape.running_minutes = args.minutes
    if args.ape_jar is not None:
        config.ape.jar_path = args.ape_jar

    backend = args.backend or config.app.exploration_backend

    # Auto-detect device serial if not provided
    serial = args.serial
    if serial is None:
        from adbutils import adb

        devices = adb.device_list()
        if not devices:
            logger.error("No ADB devices found. Connect a device and try again.")
            sys.exit(1)
        serial = devices[0].serial
        logger.info(f"Auto-detected device: {serial}")

    # Run exploration with selected backend
    output_dir = Path(args.output_dir) if args.output_dir else None

    # Extract Activity prior (optional)
    app_prior = None
    if args.manifest:
        from vigil.neuro.app_prior import AppPriorExtractor

        manifest_path = Path(args.manifest)
        if manifest_path.exists():
            app_prior = AppPriorExtractor().extract_from_manifest(manifest_path)
            logger.info(f"Prior loaded from manifest: {len(app_prior.activities)} Activities")
        else:
            logger.warning(f"Manifest not found: {manifest_path}")
    elif args.prior_from_device:
        from vigil.neuro.app_prior import AppPriorExtractor

        app_prior = AppPriorExtractor().extract_from_device_serial(serial, args.app)
        logger.info(f"Prior loaded from device: {len(app_prior.activities)} Activities")

    if backend == "ape":
        from vigil.neuro.ape_explorer import ApeExplorer

        explorer = ApeExplorer(
            device_serial=serial,
            app_package=args.app,
            config=config,
            output_dir=output_dir,
        )
    else:
        from vigil.neuro.explorer import AppExplorer

        explorer = AppExplorer(
            device_serial=serial,
            app_package=args.app,
            config=config,
            output_dir=output_dir,
            app_prior=app_prior,
        )

    result = explorer.explore()

    logger.info(
        f"Done: {result.total_steps} steps, {result.unique_screens} screens, "
        f"{result.duration_seconds}s — saved to {result.output_dir}"
    )


if __name__ == "__main__":
    main()
