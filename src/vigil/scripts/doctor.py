"""CLI entry point: vigil-doctor.

One-shot diagnostic that walks the entire device stack — adb, device
list, ``DeviceConfig`` resolver, uiautomator2 connection, accessibility
tree dump, screenshot, and a Settings-app launch round-trip — and
prints a per-step pass/fail summary. Useful when switching between
emulator and physical-device targets, or when validating a freshly
created AVD.

Exit code is ``0`` when all substantive checks pass, ``1`` otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import sys
import time
from pathlib import Path

from loguru import logger


def _ok(label: str, detail: str = "") -> None:
    line = f"  [PASS] {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def _fail(label: str, detail: str = "") -> None:
    line = f"  [FAIL] {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def main() -> None:
    """Run the diagnostic suite and exit with a status code."""
    parser = argparse.ArgumentParser(
        prog="vigil-doctor",
        description="Run a one-shot diagnostic of the Vigil device stack.",
    )
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--serial", default=None, help="Pin a specific ADB serial")
    parser.add_argument(
        "--device-type",
        choices=["emulator", "physical", "auto"],
        default=None,
    )
    args = parser.parse_args()

    from vigil.core.config import VigilConfig

    config_path = Path(args.config)
    if config_path.exists():
        config = VigilConfig.from_yaml(config_path)
    else:
        logger.warning(f"Config file not found: {config_path}, using defaults")
        config = VigilConfig()

    if args.serial is not None:
        config.device.serial = args.serial
    if args.device_type is not None:
        config.device.type = args.device_type

    failures = 0
    print("Vigil device-stack diagnostic")
    print("=" * 40)

    # 1. ADB binary on PATH
    adb_path = shutil.which("adb")
    if adb_path is None:
        _fail(
            "adb on PATH",
            "install Android platform-tools (e.g. ~/Library/Android/sdk/platform-tools) "
            "and add it to PATH or set $ANDROID_HOME",
        )
        failures += 1
    else:
        _ok("adb on PATH", adb_path)

    # 2. ADB device list
    visible: list[str] = []
    try:
        from adbutils import adb as adb_client

        visible = [d.serial for d in adb_client.device_list()]
    except Exception as exc:  # noqa: BLE001 — diagnostic context, surface anything
        _fail("adb device_list()", repr(exc))
        failures += 1

    if not visible:
        _fail("ADB sees ≥1 device", "no devices visible — start an emulator or plug in a phone")
        failures += 1
    else:
        from vigil.core.device_resolver import _is_emulator

        classified = ", ".join(
            f"{s} ({'emulator' if _is_emulator(s) else 'physical'})" for s in visible
        )
        _ok("ADB sees ≥1 device", classified)

    # 3. Device resolver
    serial: str | None = None
    try:
        from vigil.core.device_resolver import resolve_device_serial

        serial = resolve_device_serial(config.device)
        _ok("device resolver", f"selected {serial}")
    except RuntimeError as exc:
        _fail("device resolver", str(exc))
        failures += 1

    device = None
    if serial is not None:
        # 4. uiautomator2 connect
        try:
            import uiautomator2 as u2

            device = u2.connect(serial)
            info = device.info
            _ok(
                "uiautomator2 connect",
                f"product={info.get('productName')} sdk={info.get('sdkInt')} "
                f"size={info.get('displayWidth')}x{info.get('displayHeight')}",
            )
        except Exception as exc:  # noqa: BLE001
            _fail("uiautomator2 connect", repr(exc))
            failures += 1
            device = None

    if device is not None:
        # 5. Accessibility tree dump
        try:
            xml = device.dump_hierarchy()
            if xml:
                _ok("accessibility tree dump", f"{len(xml)} chars")
            else:
                _fail("accessibility tree dump", "returned empty string")
                failures += 1
        except Exception as exc:  # noqa: BLE001
            _fail("accessibility tree dump", repr(exc))
            failures += 1

        # 6. Screenshot
        screenshot_path = "/tmp/vigil_doctor.png"
        try:
            device.screenshot(screenshot_path)
            _ok("screenshot", screenshot_path)
        except Exception as exc:  # noqa: BLE001
            _fail("screenshot", repr(exc))
            failures += 1

        # 7. Launch Settings app
        try:
            device.app_start("com.android.settings")
            time.sleep(2)
            current = device.app_current()
            pkg = current.get("package") if isinstance(current, dict) else None
            if pkg == "com.android.settings":
                _ok("launch com.android.settings", f"foreground package={pkg}")
            else:
                _fail("launch com.android.settings", f"foreground package={pkg!r}")
                failures += 1
        except Exception as exc:  # noqa: BLE001
            _fail("launch com.android.settings", repr(exc))
            failures += 1

        # 8. Best-effort cleanup — return to launcher
        with contextlib.suppress(Exception):
            device.press("home")

    print("=" * 40)
    if failures == 0:
        print("[PASS] All checks passed. Vigil is ready to use on this device.")
        sys.exit(0)
    else:
        print(
            f"[FAIL] {failures} check(s) failed — "
            "resolve issues above before running vigil-explore."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
