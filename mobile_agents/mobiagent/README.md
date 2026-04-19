# MobiAgent (IPADS-SAI)

Upstream: <https://github.com/IPADS-SAI/MobiAgent>
Paper: <https://arxiv.org/abs/2509.00531>

## Pinned commit

```
ed092126539336c8e88b4c0357462b8831de6dac  feat(mobiagent): Add api_key parameter
```

See also `mobile_agents/mobiagent/COMMIT.txt`.

## Environment

- Python: 3.10.19 (MobiAgent requires <3.11 for some deps)
- Isolated venv: `mobiagent/MobiAgent/.venv`
- Do NOT install vLLM on this Mac — it's CUDA-only. MobiAgent inference
  must be served remotely.

## Install log (macOS arm64)

```bash
cd mobile_agents/mobiagent
git clone https://github.com/IPADS-SAI/MobiAgent.git
cd MobiAgent
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install --upgrade pip wheel
uv pip install -r requirements_simple.txt   # NOT requirements.txt (pulls vLLM)
# Two runtime deps missing from requirements_simple.txt on current HEAD:
pip install opencv-python python-dotenv
```

Note: `opencv-python` and `python-dotenv` are imported by
`runner/mobiagent/mobiagent.py` but absent from `requirements_simple.txt`.
Filed upstream (TODO). If they add them, the extra `pip install` is a
no-op.

## Smoke test (install validation only — no live inference)

```bash
cd mobile_agents/mobiagent/MobiAgent
source .venv/bin/activate

# Create a trivial task file
cat > runner/mobiagent/task.json <<'JSON'
[{"task_description": "Open Settings and read the device name",
  "app_name": "Settings", "package_name": "com.android.settings"}]
JSON

python -m runner.mobiagent.mobiagent \
  --service_ip 127.0.0.1 --decider_port 8000 --planner_port 8002 \
  --user_profile off --use_graphrag off
```

**Acceptable outcome:** CLI parses, runner initializes, fails with
`Connection refused` on the planner/decider HTTP port. That proves the
install is sound and only external inference endpoints are missing.

**Unacceptable:** `ImportError` / `ModuleNotFoundError` / Python crash
before the HTTP call. That means the install is broken — re-check the
extra pip installs above.

Verified `2026-04-19`: `Connection refused` on `http://127.0.0.1:8002/v1/chat/completions`.

## End-to-end execution requires external inference (BYO)

MobiAgent calls three locally-served specialized models via vLLM:

| Env var (expected by the runner CLI flags) | Model |
|---|---|
| `--service_ip <host> --decider_port <p>`  | `IPADS-SAI/MobiMind-Decider-7B` |
| `--grounder_port <p>`                     | `IPADS-SAI/MobiMind-Grounder-3B` (deprecated on recent versions; --use_qwen3 on merges it into the decider) |
| `--planner_port <p>`                      | `Qwen/Qwen3-4B-Instruct` |

vLLM does not run on Apple Silicon. Options to run end-to-end:

1. **Remote vLLM on a GPU host** — Modal / RunPod / Lambda Labs.
   Deploy the three models behind one public IP; pass it as `--service_ip`.
2. **OpenAI-compatible proxy** — some providers host these models; point
   all three ports at the same proxy URL.
3. **Deferred** — smoke-test the install only (what this README
   currently verifies). This is acceptable for the current milestone.

No end-to-end run has been executed on this machine.

## ADB + ADB Keyboard

Already installed and set as active IME by the Mobile-Agent-v2 setup
(`com.android.adbkeyboard/.AdbIME`). MobiAgent inherits it.

## Optional auxiliary models

MobiAgent can use OmniParser v2 for icon detection. Skipped on this
machine; enable only when actually serving models:

```bash
huggingface-cli download microsoft/OmniParser-v2.0 icon_detect/model.pt       --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_detect/model.yaml     --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_detect/train_args.yaml --local-dir weights
```
