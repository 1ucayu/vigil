# mobile_agents — external GUI agents used by Vigil

This folder hosts three third-party mobile GUI agents so Vigil (Phase 1
of the MobiCom 2027 project) can observe their **failure cases** and use
the observations to design the runtime verifier. Vigil itself is not a
GUI agent — it is the safety/verification layer that wraps any of them.

## Folder map

```
mobile_agents/
├── README.md                      # this file
├── verify_setup.sh                # non-destructive env smoke check
├── common/
│   ├── emulator.sh                # start/stop/wipe/status the shared emulator
│   └── adb_sanity.py              # "is a device up?" one-liner
├── m3a/
│   ├── README.md                  # M3A-specific notes
│   ├── COMMIT.txt
│   ├── .env.example
│   └── android_world/             # cloned upstream (git-ignored)
│       └── .venv/                 # isolated Py3.11 venv
├── mobile-agent-v2/
│   ├── README.md
│   ├── COMMIT.txt
│   ├── .env.example
│   ├── adb_keyboard.apk           # IME for text input
│   └── MobileAgent/               # cloned upstream (git-ignored)
│       └── Mobile-Agent-v2/
│           ├── groundingdino/weights/groundingdino_swint_ogc.pth   # 662 MB
│           └── .venv/             # isolated Py3.11 venv
└── mobiagent/
    ├── README.md
    ├── COMMIT.txt
    └── MobiAgent/                 # cloned upstream (git-ignored)
        └── .venv/                 # isolated Py3.10 venv
```

Upstream clones, venvs, weights, APKs, and `.env` files are git-ignored.
Only scaffolding (this README, per-agent READMEs, `.env.example`,
`emulator.sh`, `verify_setup.sh`, pinned commit SHAs) is committed.

## Target AVD

| Field | Value |
|---|---|
| Name | **`Pixel_6a`** (underscore, not space) |
| API | 34 ("UpsideDownCake", Android 14) |
| Services | Google Play Store |
| System image | Google Play ARM 64 v8a |

The setup doc that spawned this folder claimed the AVD was literally
`Pixel 6a` (with a space) and insisted never to substitute the
underscore form. That claim was false on this machine —
`emulator -list-avds` returns `Pixel_6a`. `common/emulator.sh` uses the
real name; override with `EMULATOR_NAME=<other>` to target a different
AVD.

## Quickstart

```bash
# 1. Start the emulator (checks gRPC auth mode automatically)
mobile_agents/common/emulator.sh start

# 2. Non-destructive environment smoke check (no LLM, no agent run)
mobile_agents/verify_setup.sh

# 3. Point agents at real credentials (or keep the local proxy defaults)
cp mobile_agents/m3a/.env.example            mobile_agents/m3a/.env
cp mobile_agents/mobile-agent-v2/.env.example mobile_agents/mobile-agent-v2/.env

# 4. Run any agent through Vigil's unified runner
uv run vigil-agent-run --agent m3a             --task ContactsAddContact
uv run vigil-agent-run --agent mobile_agent_v2 --instruction "Open Settings and enable Wi-Fi"
uv run vigil-agent-run --agent mobiagent       --instruction "Open Settings"
```

Every run drops `stdout.log`, `stderr.log`, and a machine-readable
`manifest.json` into `data/agent_runs/<timestamp>_<agent>/` (git-ignored).

## Per-agent cheat-sheet

| | M3A (AndroidWorld) | Mobile-Agent-v2 | MobiAgent (IPADS-SAI) |
|---|---|---|---|
| Upstream | google-research/android_world | X-PLUG/MobileAgent | IPADS-SAI/MobiAgent |
| Pinned commit | `d9c569f7…` | `8cf3966e…` | `ed092126…` |
| Python | 3.11 | 3.11 | 3.10 |
| Planner LLM | GPT-4-turbo-class (OpenAI-compat) | GPT-4o + Qwen-VL caption | MobiMind-Decider-7B + Qwen3-4B (remote vLLM) |
| Extra model | — | GroundingDINO (CPU fallback on arm64) | optional OmniParser |
| Venv | `m3a/android_world/.venv` | `mobile-agent-v2/.../Mobile-Agent-v2/.venv` | `mobiagent/MobiAgent/.venv` |
| Smoke entrypoint | `minimal_task_runner.py --task=<Name>` | `run.py` (reads `MAV2_INSTRUCTION`) | `python -m runner.mobiagent.mobiagent` |

Per-agent READMEs under each subdir contain the full install log and
every fix applied.

## Known issues & resolutions

1. **Do NOT add `-grpc-use-token` to `emulator.sh`.** That flag only
   affects Android Studio's embedded emulator window; in a standalone
   launch it creates an auth requirement out of thin air. On this
   machine (emulator 36.5.10-15081367, darwin-aarch64) the default
   launch with just `-grpc 8554` yields `auth: none` —
   AndroidWorld's `grpc.local_channel_credentials()` works fine.
   If a future emulator upgrade flips to `auth: +token`, **downgrade**
   the emulator binary to 34.2.x per §4.5 of the original setup doc
   and pin via `EMULATOR_BIN`. Do not try to attach JWT bearer tokens
   on top of local-channel credentials — the server validates signed
   JWTs against a JWKS, not free-form Bearer strings, so that path
   has no exit.

2. **Google Play system image restrictions.** `Pixel_6a` uses a Google
   Play ARM 64 image, which disallows `adb root`. AndroidWorld tasks
   that call `adb shell date` during `setup_task` fail with
   `Operation not permitted`. Either pick tasks whose setup doesn't
   touch the clock, or create a second AVD on a **Google APIs** image
   (`sdkmanager "system-images;android-34;google_apis;arm64-v8a"`) and
   point `EMULATOR_NAME` at it.

3. **GroundingDINO CUDA compile failure on Apple Silicon is expected.**
   `Failed to load custom C++ ops. Running on CPU mode Only!` is the
   pure-Python fallback kicking in — functional but slower on first
   forward pass.

4. **MobiAgent needs external GPU inference.** vLLM is CUDA-only and
   does not build on Mac. The smoke test validates imports + CLI
   parsing only; end-to-end execution requires a remote vLLM (Modal /
   RunPod / Lambda) hosting the three MobiMind models and surfacing
   them on `MOBIMIND_SERVICE_IP` + `MOBIMIND_DECIDER_PORT` /
   `MOBIMIND_PLANNER_PORT` (+ optional `MOBIMIND_GROUNDER_PORT`).
   See `mobiagent/README.md`.

5. **`requirements_simple.txt` is incomplete.** MobiAgent HEAD imports
   `cv2` and `python-dotenv` without listing them. The runner install
   log does the extra pip install; if upstream adds them, the step is
   a no-op.

6. **setuptools ≥81 breaks M3A's editable install.** Pin
   `"setuptools<81"` before `pip install -e . --no-build-isolation`.
   See `m3a/README.md`.

7. **Android 14 IME restrictions.** If `adb shell ime set` refuses the
   ADB Keyboard on some configs, writing `enabled_input_methods`
   directly via `adb shell settings put secure …` is the documented
   workaround. Not needed on `Pixel_6a` on this machine.

## Where the failure traces go

Every invocation via `uv run vigil-agent-run …` writes to
`data/agent_runs/<timestamp>_<agent>/`:

- `stdout.log`, `stderr.log`
- `manifest.json` — machine-readable record (cmd, cwd, return code,
  log paths, agent name, task/instruction)

Phase 2 of Vigil (offline FSM construction) ingests these manifests to
identify where real agents make mistakes. The folder is git-ignored.

## Entry points in Vigil source

- `src/vigil/integration/agent_runner.py` — subprocess runner and CLI
- `tests/test_agent_runner.py` — unit tests (no emulator, no LLM)
- CLI registered in `pyproject.toml` as `vigil-agent-run`
