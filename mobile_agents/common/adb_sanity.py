"""Confirm a local emulator is reachable and responsive.

Run:  python mobile_agents/common/adb_sanity.py
Exits non-zero on failure, which lets verify_setup.sh detect problems.
"""

from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def main() -> int:
    try:
        devices = run(["adb", "devices"])
    except FileNotFoundError:
        print("FAIL: adb not on PATH")
        return 1

    lines = [line for line in devices.splitlines()[1:] if line.strip()]
    if not lines:
        print("FAIL: no emulator connected. Run mobile_agents/common/emulator.sh start")
        return 2

    serial = lines[0].split()[0]
    boot = run(["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"])
    if boot != "1":
        print(f"FAIL: {serial} reports boot_completed={boot!r}; still booting?")
        return 3

    version = run(["adb", "-s", serial, "shell", "getprop", "ro.build.version.release"])
    api = run(["adb", "-s", serial, "shell", "getprop", "ro.build.version.sdk"])
    print(f"OK: {serial} is up (Android {version}, API {api})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
