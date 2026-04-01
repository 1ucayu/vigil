"""APE process runner.

Pushes ape.jar to an Android device, runs APE exploration, and pulls
the output directory back to the host for parsing.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from vigil.core.config import VigilConfig


class ApeRunner:
    """Run APE on an Android device and collect output.

    Execution flow:
    1. Push ape.jar to device
    2. Run APE via adb shell (as a Monkey replacement)
    3. Wait for completion
    4. Pull output from /sdcard/ to local directory
    """

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

        ape_cfg = config.ape
        jar = Path(ape_cfg.jar_path)
        if not jar.is_absolute():
            # Resolve relative to project root (where pyproject.toml lives)
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            jar = project_root / jar
        self._jar_path = jar
        self._device_jar_path = ape_cfg.device_jar_path
        self._device_output_dir = ape_cfg.device_output_dir
        self._running_minutes = ape_cfg.running_minutes
        self._ape_mode = ape_cfg.ape_mode

    def run(self) -> Path:
        """Execute APE exploration and return local path to pulled output.

        Raises:
            FileNotFoundError: If ape.jar is not found at the configured path.
            RuntimeError: If APE fails to run.
        """
        self._push_jar()
        self._push_config()

        # Clean previous APE output on device
        ape_output_pattern = (
            f"/sdcard/sata-{self._app_package}-ape-{self._ape_mode}"
            f"-running-minutes-{self._running_minutes}"
        )
        self._adb("shell", "rm", "-rf", ape_output_pattern)

        proc = self._start_ape()
        self._wait_for_completion(proc)

        return self._pull_output()

    def _push_jar(self) -> None:
        """Push ape.jar to the device."""
        if not self._jar_path.exists():
            raise FileNotFoundError(
                f"ape.jar not found at {self._jar_path}. "
                f"Build from https://github.com/tianxiaogu/ape and place at {self._jar_path}"
            )

        logger.info(f"Pushing {self._jar_path} to {self._device_jar_path}")
        self._adb("push", str(self._jar_path), self._device_jar_path)

    def _push_config(self) -> None:
        """Push ape.properties to disable text truncation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".properties", delete=False) as f:
            f.write("ape.truncateTextLength = 200\n")
            tmp_path = f.name
        try:
            self._adb("push", tmp_path, "/sdcard/ape.properties")
            logger.info("Pushed ape.properties (truncateTextLength=200)")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _start_ape(self) -> subprocess.Popen:
        """Start APE as an adb shell process."""
        cmd = [
            "adb",
            "-s",
            self._serial,
            "shell",
            f"CLASSPATH={self._device_jar_path}",
            "/system/bin/app_process",
            "/data/local/tmp/",
            "com.android.commands.monkey.Monkey",
            "-p",
            self._app_package,
            "--running-minutes",
            str(self._running_minutes),
            "--ape",
            self._ape_mode,
            "-v",
            "-v",
            "1000000",  # event count (APE stops on --running-minutes, not count)
        ]

        logger.info(
            f"Starting APE: {self._app_package} for {self._running_minutes} min "
            f"(mode={self._ape_mode})"
        )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return proc

    def _wait_for_completion(self, proc: subprocess.Popen) -> None:
        """Wait for APE process to finish, streaming output to logger."""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logger.debug(f"[APE] {line}")

        proc.wait()
        if proc.returncode != 0:
            logger.warning(f"APE exited with code {proc.returncode}")
        else:
            logger.info("APE exploration completed")

    def _pull_output(self) -> Path:
        """Pull APE output from device to local directory."""
        # APE creates output at: /sdcard/sata-<pkg>-ape-<mode>-running-minutes-<N>
        expected_path = (
            f"/sdcard/sata-{self._app_package}-ape-{self._ape_mode}"
            f"-running-minutes-{self._running_minutes}"
        )
        result = self._adb("shell", "ls", "-d", expected_path, capture=True)
        if "No such file" in result:
            # Try listing /sdcard/sata-* to find any APE output
            result = self._adb(
                "shell", "ls", "-d", f"/sdcard/sata-{self._app_package}-*", capture=True
            )
            if "No such file" in result:
                raise RuntimeError(f"APE output not found at {expected_path}")
            device_path = result.strip().split("\n")[0]
        else:
            device_path = expected_path

        local_ape_dir = self._output_dir / "ape_output"
        local_ape_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Pulling APE output from {device_path} to {local_ape_dir}")
        self._adb("pull", device_path + "/.", str(local_ape_dir))

        # Verify we got step files
        step_files = list(local_ape_dir.glob("step-*.xml"))
        logger.info(f"Pulled {len(step_files)} step files")

        return local_ape_dir

    def _adb(self, *args: str, capture: bool = False) -> str:
        """Run an adb command with the device serial."""
        cmd = ["adb", "-s", self._serial, *args]
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.stdout + result.stderr
        subprocess.run(cmd, check=True, timeout=300)
        return ""
