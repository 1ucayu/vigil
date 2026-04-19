#!/usr/bin/env bash
# Launch / stop / wipe / status the shared emulator used by M3A and Mobile-Agent-v2.
# Spec §4.2 (adapted). The AVD on disk is `Pixel_6a` (underscore) — the task
# document's `Pixel 6a` (with space) premise is false on this machine.
#
# IMPORTANT (carry-forward from §1.2): we do NOT pass -grpc-use-token.
# That flag was the root cause of a prior failed attempt. If the default
# binary (emulator >=35) enforces JWT auth, the correct fix is to pin a
# 34.2.x emulator via EMULATOR_BIN, NOT to toggle auth flags. See §4.5.
#
# Usage: ./emulator.sh {start|stop|wipe|status}
set -euo pipefail

AVD_NAME="${EMULATOR_NAME:-Pixel_6a}"
EMULATOR_BIN="${EMULATOR_BIN:-$HOME/Library/Android/sdk/emulator/emulator}"
GRPC_PORT="${GRPC_PORT:-8554}"
BOOT_LOG="/tmp/emulator_boot.log"

case "${1:-start}" in
  start)
    if adb devices | awk 'NR>1 && /device$/{found=1} END{exit !found}'; then
      echo "An emulator/device is already connected:"
      adb devices | tail -n +2
      echo
      echo "--- previous gRPC auth mode (from $BOOT_LOG) ---"
      grep -E "Started GRPC server|security:|auth:" "$BOOT_LOG" 2>/dev/null | tail -n 5 \
        || echo "(no boot log; restart via: $0 stop && $0 start)"
      exit 0
    fi
    echo "Launching \"$AVD_NAME\" via $EMULATOR_BIN (gRPC $GRPC_PORT) ..."
    "$EMULATOR_BIN" -avd "$AVD_NAME" \
        -no-snapshot \
        -grpc "$GRPC_PORT" \
        -gpu auto -no-audio -no-boot-anim \
        -verbose >"$BOOT_LOG" 2>&1 &
    adb wait-for-device
    until [[ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; do
      sleep 2
    done
    echo "Boot complete."
    echo
    echo "--- gRPC auth mode ---"
    grep -E "Started GRPC server|security:|auth:" "$BOOT_LOG" | tail -n 5 || true
    echo
    adb devices
    ;;
  stop)
    adb emu kill || true
    ;;
  wipe)
    "$EMULATOR_BIN" -avd "$AVD_NAME" -wipe-data &
    ;;
  status)
    adb devices
    echo
    grep -E "Started GRPC server|security:|auth:" "$BOOT_LOG" 2>/dev/null | tail -n 5 \
      || echo "No boot log at $BOOT_LOG"
    ;;
  *)
    echo "Usage: $0 {start|stop|wipe|status}"; exit 1 ;;
esac
