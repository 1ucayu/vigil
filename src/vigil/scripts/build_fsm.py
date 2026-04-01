"""CLI entry point: vigil-build.

Builds an FSM from exploration traces.

Usage:
    vigil-build --trace <trace.json>
    vigil-build --trace <trace.json> --output <fsm.json>
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

    args = parser.parse_args()

    trace_path = Path(args.trace)
    if not trace_path.exists():
        logger.error(f"Trace file not found: {trace_path}")
        raise SystemExit(1)

    # Auto-detect app package from trace
    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    app_package = args.app or trace_data.get("app_package", "unknown")
    app_name = app_package.rsplit(".", maxsplit=1)[-1]

    # Determine output path
    output_path = Path(args.output) if args.output else Path(f"models/bundles/{app_name}/fsm.json")

    # Build FSM
    from vigil.neuro.fsm_builder import FsmBuilder

    builder = FsmBuilder(app_package=app_package)
    fsm = builder.build_from_trace(
        trace_path=trace_path,
        include_self_loops=args.include_self_loops,
    )

    # Serialize
    fsm.serialize(output_path)
    logger.info(f"FSM saved to {output_path}")
    logger.info(f"Summary: {fsm}")


if __name__ == "__main__":
    main()
