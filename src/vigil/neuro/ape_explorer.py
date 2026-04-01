"""APE-based exploration backend for Stage 1.

Replaces the native BFS/DFS explorer with APE's CEGAR-based exploration.
Runs APE on the device, pulls output, and parses it into ExplorationResult.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.config import VigilConfig
from vigil.neuro.ape_parser import ApeOutputParser
from vigil.neuro.ape_runner import ApeRunner
from vigil.neuro.explorer import ExplorationResult


class ApeExplorer:
    """APE-based exploration with the same interface as AppExplorer.

    Args:
        device_serial: ADB serial of the target device.
        app_package: Android package name to explore.
        config: Vigil configuration.
        output_dir: Base output directory (default: data/apps/<app_name>/).
    """

    def __init__(
        self,
        device_serial: str,
        app_package: str,
        config: VigilConfig,
        output_dir: Path | None = None,
    ) -> None:
        self._serial = device_serial
        self._app_package = app_package
        self._config = config

        if output_dir is None:
            app_name = app_package.rsplit(".", maxsplit=1)[-1]
            self._output_dir = Path(f"data/apps/{app_name}")
        else:
            self._output_dir = output_dir

        self._output_dir.mkdir(parents=True, exist_ok=True)

    def explore(self) -> ExplorationResult:
        """Run APE exploration and return structured results."""
        start_time = time.monotonic()

        # Step 1: Run APE on device
        runner = ApeRunner(
            device_serial=self._serial,
            app_package=self._app_package,
            config=self._config,
            output_dir=self._output_dir,
        )
        ape_output_dir = runner.run()

        # Step 2: Parse APE output into ExplorationResult
        parser = ApeOutputParser(
            output_dir=ape_output_dir,
            app_package=self._app_package,
        )
        result = parser.parse()

        elapsed = time.monotonic() - start_time
        result.duration_seconds = round(elapsed, 2)
        result.output_dir = str(self._output_dir)

        # Step 3: Save result in Vigil's trace format
        self._save_result(result)

        logger.info(
            f"APE exploration complete: {result.total_steps} steps, "
            f"{result.unique_screens} unique screens, {elapsed:.1f}s"
        )
        return result

    def _save_result(self, result: ExplorationResult) -> None:
        """Save the exploration result as a JSON trace file."""
        traces_dir = self._output_dir / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        trace_path = traces_dir / f"exploration_{timestamp}.json"

        compact_screens: dict[str, Any] = {}
        for sid, s in result.screens.items():
            interactable = s.get_interactable_elements()
            compact_screens[sid] = {
                "screen_id": s.screen_id,
                "activity_name": s.activity_name,
                "package_name": s.package_name,
                "screenshot_path": s.screenshot_path,
                "xml_tree_path": s.xml_tree_path,
                "fingerprint": s.get_structural_fingerprint(),
                "total_elements": len(s.elements),
                "interactable_elements": [e.model_dump(mode="json") for e in interactable],
                "timestamp": s.timestamp,
            }

        data: dict[str, Any] = {
            "app_package": result.app_package,
            "device_serial": self._serial,
            "exploration_backend": "ape",
            "ape_mode": self._config.ape.ape_mode,
            "total_steps": result.total_steps,
            "unique_screens": result.unique_screens,
            "duration_seconds": result.duration_seconds,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "screens": compact_screens,
            "traces": [t.model_dump(mode="json") for t in result.traces],
        }

        trace_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(f"Exploration trace saved to {trace_path}")
