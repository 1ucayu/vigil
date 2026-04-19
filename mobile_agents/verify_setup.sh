#!/usr/bin/env bash
# Non-destructive environment smoke check — no LLM calls, no agent runs.
# Spec §9 (adapted for Pixel_6a and the three-agent layout).
set -euo pipefail
cd "$(dirname "$0")"

pass() { echo "OK   - $1"; }
fail() { echo "FAIL - $1"; exit 1; }

echo "==> ADB sanity"
python3 common/adb_sanity.py || fail "emulator not ready (run common/emulator.sh start)"

echo "==> Emulator gRPC auth mode"
if grep -q "auth: +token" /tmp/emulator_boot.log 2>/dev/null; then
  echo "FAIL: emulator enforcing gRPC JWT auth; AndroidWorld will not work."
  echo "      See mobile_agents/m3a/README.md (§4.5 of the setup doc) for the downgrade path."
  echo "      Do NOT add -grpc-use-token to emulator.sh."
  exit 1
fi
pass "gRPC unauthenticated (auth: none)"

echo "==> M3A venv + import"
( cd m3a/android_world && .venv/bin/python -c "import android_world" ) \
  && pass "import android_world" || fail "cannot import android_world"

echo "==> Mobile-Agent-v2 venv + GroundingDINO import"
( cd mobile-agent-v2/MobileAgent/Mobile-Agent-v2 \
    && .venv/bin/python -c "import groundingdino; from groundingdino.util import inference; \
                             import inspect; sig = inspect.signature(inference.load_model); \
                             params = list(sig.parameters); assert len(params) <= 3, params" ) \
  && pass "groundingdino load_model signature OK (<=3 args)" \
  || fail "groundingdino load_model has wrong signature — re-pin per mobile-agent-v2/README.md"

echo "==> GroundingDINO checkpoint present"
test -f mobile-agent-v2/MobileAgent/Mobile-Agent-v2/groundingdino/weights/groundingdino_swint_ogc.pth \
  && pass "groundingdino checkpoint" || fail "missing groundingdino checkpoint"

echo "==> ADB Keyboard installed on device"
adb shell pm list packages | grep -q com.android.adbkeyboard \
  && pass "com.android.adbkeyboard" || fail "install adb_keyboard.apk"

echo "==> ADB Keyboard is active IME"
current_ime="$(adb shell settings get secure default_input_method | tr -d '\r')"
if [[ "$current_ime" == "com.android.adbkeyboard/.AdbIME" ]]; then
  pass "IME is adbkeyboard"
else
  echo "WARN - active IME is '$current_ime'; Mobile-Agent-v2 text input will fail until you run:"
  echo "       adb shell ime set com.android.adbkeyboard/.AdbIME"
fi

echo "==> MobiAgent venv + runner CLI parses"
( cd mobiagent/MobiAgent && .venv/bin/python -m runner.mobiagent.mobiagent --help >/dev/null ) \
  && pass "mobiagent --help" || fail "mobiagent runner CLI broken"

echo
echo "All green. End-to-end run commands:"
echo "  uv run vigil-agent-run --agent m3a            --task ContactsAddContact"
echo "  uv run vigil-agent-run --agent mobile_agent_v2 --instruction \"Open Settings\""
echo "  uv run vigil-agent-run --agent mobiagent       --instruction \"Open Settings\"  # needs MOBIMIND_*_URL env"
