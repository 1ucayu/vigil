# M3A (AndroidWorld)

Upstream: <https://github.com/google-research/android_world>

## Pinned commit

```
d9c569f764b3a5629321858de03ff653d0f24056  Kotlinc 2.3.0 update pre-work
```

See also `mobile_agents/m3a/COMMIT.txt`.

## Environment

- Python: 3.11.13 (`uv python`)
- Isolated venv: `m3a/android_world/.venv` (do **not** reuse Vigil's root venv)
- Emulator: `Pixel_6a` (API 34, Google Play arm64-v8a), launched by
  `mobile_agents/common/emulator.sh` with `-grpc 8554` and NO `-grpc-use-token` flag.
- Emulator binary: Android SDK `emulator 36.5.10.0` (build 15081367).

## Install log (what actually worked on macOS arm64)

```bash
cd mobile_agents/m3a
git clone https://github.com/google-research/android_world.git
cd android_world
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install --upgrade "setuptools<81" wheel      # setuptools >=81 drops pkg_resources
uv pip install -r requirements.txt
pip install -e . --no-build-isolation                # editable install
```

### Fixes applied

1. **setuptools<81 pin** — upstream `setup.py` imports `pkg_resources`, which
   setuptools 81 removed. Without the pin, `pip install -e .` fails with
   `ModuleNotFoundError: No module named 'pkg_resources'`.
2. **`--no-build-isolation`** for the editable install so the pinned
   setuptools is honored by the build backend.

No source edits were made to the agent.

## Proxy routing (Vigil convention)

Vigil routes all LLM traffic through a local OpenAI-compatible proxy at
`http://localhost:4141/v1` and a local Anthropic-compatible proxy at
`http://localhost:4141`. M3A's `Gpt4Wrapper` in
`android_world/agents/infer.py` hardcodes
`https://api.openai.com/v1/chat/completions`, so we monkey-patch it from
outside the repo:

- `.venv/lib/python3.11/site-packages/vigil_proxy_patch.py` — replacement
  `predict_mm` that reads `OPENAI_BASE_URL` and `OPENAI_MODEL` from env.
- `.venv/lib/python3.11/site-packages/sitecustomize.py` — auto-imports the
  patch at interpreter startup.

## gRPC auth — resolved (the whole point of §4 of the setup doc)

**On this machine (emulator 36.5.10-15081367, macOS arm64), launching
with `-grpc 8554` and no other flags yields `auth: none` — unauthenticated
local gRPC.** AndroidWorld's `grpc.local_channel_credentials()` works
fine against that.

The prior session's failure was self-inflicted: they added `-grpc-use-token`
(only meaningful to Android Studio's embedded window) and then tried to
attach JWT bearer tokens on top of local-channel credentials. Don't do
that. The correct launch is in `mobile_agents/common/emulator.sh`.

Verify with `./common/emulator.sh status` — look for `auth: none`. If a
future emulator upgrade flips to `auth: +token`, downgrade the emulator
binary per §4.5 of the setup doc (pin 34.2.x via `EMULATOR_BIN`), do NOT
toggle auth flags.

## Running a task

```bash
cd mobile_agents/m3a/android_world
source .venv/bin/activate
set -a && source ../.env && set +a
python minimal_task_runner.py --task=ContactsAddContact
```

Full task catalog: `android_world/task_evals/`.

## Known blocker on Google Play system image: `adb shell date`

The `Pixel_6a` AVD uses a **Google Play** arm64-v8a image (required by
our setup doc §1.1). Many AndroidWorld tasks call `adb shell date ...`
to pin a deterministic clock during task setup. That command requires
root — allowed on "Google APIs" images, rejected on "Google Play" images:

```
cannot set date: Operation not permitted
adb -P 5037 -s emulator-5554 shell date 1015153423.00  →  exit 1
```

This blocks `minimal_task_runner.py` at setup for tasks that set a date.
gRPC connectivity itself is fine.

**Mitigations:**

- Run tasks whose setup doesn't touch the clock (inspect
  `android_world/task_evals/single/<Task>.py` for `setup_task`).
- Or create a second AVD using a **Google APIs** system image
  (not Google Play), e.g.
  `sdkmanager "system-images;android-34;google_apis;arm64-v8a"`, then
  `EMULATOR_NAME=AndroidWorldAvd mobile_agents/common/emulator.sh start`.
  That image permits `adb root` and `date` writes.

Do NOT attempt to unlock root on the current AVD — the Google Play image
explicitly disallows it.

## Notes / deviations

- AVD is `Pixel_6a` (API 34, Google Play arm64-v8a), not the setup doc's
  `Pixel 6a` (with space). The doc's premise was wrong; `-list-avds`
  returns the underscore version.
- `-grpc 8554` is sufficient; no `-grpc-use-token`.
- LLM traffic routed through Vigil's localhost:4141 proxy (`.env`).
