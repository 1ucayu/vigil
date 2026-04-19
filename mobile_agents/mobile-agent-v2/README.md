# Mobile-Agent-v2

Upstream: <https://github.com/X-PLUG/MobileAgent/tree/main/Mobile-Agent-v2>

## Pinned commit

See [`README_COMMIT.txt`](./README_COMMIT.txt). Initial setup:

```
8cf3966e968717e13a4f00cdb7afce1a06cc6e26  Add news section with updates on UI-S1
```

GroundingDINO pin: `856dde20aee659246248e20734ef9ba5214f5e44`.

## Environment

- Python: 3.11.13
- Isolated venv: `mobile-agent-v2/MobileAgent/Mobile-Agent-v2/.venv`
- Caption backend: **api** (Qwen-VL via DashScope). Local 7B+ model path is
  documented upstream but not wired here — requires ~15GB of weights.

## Install log

```bash
cd mobile_agents/mobile-agent-v2
git clone https://github.com/X-PLUG/MobileAgent.git
cd MobileAgent/Mobile-Agent-v2

uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install --upgrade "setuptools<81" wheel
uv pip install -r requirements.txt
uv pip install "git+https://github.com/IDEA-Research/GroundingDINO.git" \
  --no-build-isolation

# Download checkpoint (~662MB)
mkdir -p groundingdino/weights
curl -L -o groundingdino/weights/groundingdino_swint_ogc.pth \
  https://github.com/IDEA-Research/GroundingDINO/releases/download/v0.1.0-alpha/groundingdino_swint_ogc.pth

# ADB Keyboard (from the parent mobile-agent-v2/ folder)
cd ../..
curl -L -o adb_keyboard.apk \
  https://github.com/senzhk/ADBKeyBoard/raw/master/ADBKeyboard.apk
adb install -r adb_keyboard.apk
# On Android 14 `ime enable` refuses the package, so enable via settings:
adb shell settings put secure enabled_input_methods \
  "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME:com.google.android.tts/com.google.android.apps.speech.tts.googletts.settings.asr.voiceime.VoiceInputMethodService:com.android.adbkeyboard/.AdbIME"
adb shell ime set com.android.adbkeyboard/.AdbIME
```

### Fixes applied

1. **`--no-build-isolation`** for GroundingDINO — its `setup.py` tries to
   `pip install torch` inside the build env, which fails without an arm64
   Torch wheel cache. Installing in the venv (where Torch already lives)
   avoids the re-download.
2. **Setuptools pinned <81** — same `pkg_resources` story as M3A.
3. **`supervision` upgrade** — GroundingDINO depends on a newer release
   than the requirements pin (0.21 → 0.27). Verified import path still
   works.
4. **Android 14 IME workaround** — `adb shell ime enable` is rejected for
   unpre-approved IMEs on API 34; writing `enabled_input_methods` directly
   works. See commands above.
5. **`run.py` config block** — replaced hardcoded strings with `os.environ`
   lookups so API keys stay in `.env`. No other source edits (CLAUDE.md §11
   rule 5).

## Running an instruction

```bash
cd mobile_agents/mobile-agent-v2/MobileAgent/Mobile-Agent-v2
source .venv/bin/activate
set -a && source ../../.env && set +a
python run.py
```

Change the instruction via `MAV2_INSTRUCTION` env var, or edit it in
`run.py` line 28.

## Notes / known quirks on Apple Silicon

- GroundingDINO's CUDA `_C` extension does not build on macOS — the library
  falls back to its pure-Python path. First inference call is slow
  (~30 s) while weights load lazily; subsequent steps are fast.
- The caption path requires a DashScope key (Qwen-VL). Without it, icon
  captioning silently returns empty strings and the planner loses spatial
  grounding. Supply `DASHSCOPE_API_KEY` in `.env` or switch to
  `MAV2_CAPTION_METHOD=local` if you bring your own Qwen-VL checkpoint.

## Known blocker: modelscope ↔ GroundingDINO API drift

First end-to-end run reaches model download (~2 GB of Qwen-VL + GroundingDINO
weights via modelscope) then crashes on:

```
TypeError: GroundingdinoGenerationPipeline: load_model() takes from 2 to 3
positional arguments but 4 were given
```

Root cause: `modelscope_modules/GroundingDINO/ms_wrapper.py` still calls
`groundingdino.util.inference.load_model(config, checkpoint, device,
something_else)` with 4 positional args, but upstream GroundingDINO
(`856dde20`) narrowed the signature to `(config, checkpoint, device=...)`.
This breaks before any agent loop runs.

Workarounds (tried in order of increasing effort):

1. **Downgrade GroundingDINO** to a commit predating the signature change.
   Search for the last revision of `util/inference.py` with 4 positional
   args.
2. **Monkey-patch `ms_wrapper.py`** from a `sitecustomize.py` in the venv,
   rewriting the call to match the new signature.
3. **Skip modelscope entirely** by setting `MAV2_CAPTION_METHOD=local` and
   pointing `run.py` at a locally loaded GroundingDINO checkpoint
   (requires editing `run.py` beyond the config block, so it violates
   CLAUDE.md §11 rule 5 unless done via a wrapper).

All three require a dedicated follow-up task; not in scope for this
initial setup pass. Until fixed, Mobile-Agent-v2 will crash at startup
before reaching the emulator.
