"""DroidBot subprocess runner.

Invokes DroidBot CLI as a subprocess for UI exploration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from vigil.core.config import VigilConfig


class DroidBotRunner:
    """Run DroidBot exploration via subprocess."""

    def __init__(
        self,
        device_serial: str,
        app_package: str,
        config: VigilConfig,
        output_dir: Path,
    ) -> None:
        self._serial = device_serial
        self._app_package = app_package
        self._config = config
        self._output_dir = output_dir

    def run(self) -> Path:
        """Execute DroidBot and return the output directory path."""
        droidbot_bin = shutil.which("droidbot")
        if droidbot_bin is None:
            msg = (
                "droidbot not found. Install with: uv add droidbot && uv sync\n"
                "See: https://github.com/honeynet/droidbot"
            )
            raise FileNotFoundError(msg)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command()

        logger.info(f"Starting DroidBot: {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None

        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    if any(kw in line for kw in ("New state", "Explored", "UTG", "Total")):
                        logger.info(f"[DroidBot] {line}")
                    else:
                        logger.debug(f"[DroidBot] {line}")

            proc.wait()
            if proc.returncode != 0:
                msg = f"DroidBot exited with code {proc.returncode}"
                raise RuntimeError(msg)

        except KeyboardInterrupt:
            logger.warning("DroidBot interrupted by user")
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)

        utg_file = self._output_dir / "utg.js"
        if not utg_file.exists():
            msg = f"DroidBot did not produce utg.js in {self._output_dir}"
            raise RuntimeError(msg)

        states_dir = self._output_dir / "states"
        state_count = len(list(states_dir.glob("*.json"))) if states_dir.exists() else 0
        logger.info(f"DroidBot complete: {state_count} states in {self._output_dir}")

        return self._output_dir

    def _build_command(self) -> list[str]:
        db = self._config.droidbot
        cmd = [
            "droidbot",
            "-a",
            self._app_package,
            "-o",
            str(self._output_dir),
            "-d",
            self._serial,
            "-policy",
            db.policy,
            "-count",
            str(db.count),
        ]

        if db.timeout > 0:
            cmd.extend(["-timeout", str(db.timeout)])
        if db.grant_perm:
            cmd.append("-grant_perm")
        if db.keep_app:
            cmd.append("-keep_app")
        if db.keep_env:
            cmd.append("-keep_env")
        if db.ignore_ad:
            cmd.append("-ignore_ad")
        if db.extra_args:
            cmd.extend(db.extra_args)

        return cmd
