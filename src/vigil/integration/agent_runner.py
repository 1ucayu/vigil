"""Subprocess-isolated runner for external GUI agents (M3A, Mobile-Agent-v2, MobiAgent).

The two upstream agents have conflicting Python dependencies (AndroidWorld
pins grpcio/protobuf/tensorflow; Mobile-Agent-v2 brings in Torch + TF +
GroundingDINO). We therefore never import them as libraries — each lives in
its own venv under ``mobile_agents/`` and is invoked as a subprocess.

Run manifests (one JSON per run) accumulate under
``data/agent_runs/<timestamp>/`` so downstream Vigil phases can replay /
analyse them.

Usage::

    uv run vigil-agent-run --agent m3a --task ContactsAddContact
    uv run vigil-agent-run --agent mobile_agent_v2 \\
        --instruction "Open Settings and enable Wi-Fi"
    uv run vigil-agent-run --agent m3a --task X --dry-run

See ``mobile_agents/README.md`` for prerequisites (emulator + ADB Keyboard).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
MOBILE_AGENTS = REPO_ROOT / "mobile_agents"
RUNS_DIR = REPO_ROOT / "data" / "agent_runs"


@dataclass
class AgentSpec:
    """Static description of how to invoke one external agent."""

    name: str
    cwd: Path
    venv: Path
    entrypoint: list[str]
    env_file: Path | None = None
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def python(self) -> Path:
        return self.venv / "bin" / "python"


M3A = AgentSpec(
    name="m3a",
    cwd=MOBILE_AGENTS / "m3a" / "android_world",
    venv=MOBILE_AGENTS / "m3a" / "android_world" / ".venv",
    entrypoint=["minimal_task_runner.py"],
    env_file=MOBILE_AGENTS / "m3a" / ".env",
)

MOBILE_AGENT_V2 = AgentSpec(
    name="mobile_agent_v2",
    cwd=MOBILE_AGENTS / "mobile-agent-v2" / "MobileAgent" / "Mobile-Agent-v2",
    venv=MOBILE_AGENTS / "mobile-agent-v2" / "MobileAgent" / "Mobile-Agent-v2" / ".venv",
    entrypoint=["run.py"],
    env_file=MOBILE_AGENTS / "mobile-agent-v2" / ".env",
)

MOBIAGENT = AgentSpec(
    name="mobiagent",
    cwd=MOBILE_AGENTS / "mobiagent" / "MobiAgent",
    venv=MOBILE_AGENTS / "mobiagent" / "MobiAgent" / ".venv",
    entrypoint=["-m", "runner.mobiagent.mobiagent"],
    env_file=MOBILE_AGENTS / "mobiagent" / ".env",
)

AGENTS: dict[str, AgentSpec] = {
    "m3a": M3A,
    "mobile_agent_v2": MOBILE_AGENT_V2,
    "mobiagent": MOBIAGENT,
}

_MOBIAGENT_REQUIRED_ENV = (
    "MOBIMIND_SERVICE_IP",
    "MOBIMIND_DECIDER_PORT",
    "MOBIMIND_PLANNER_PORT",
)


def _load_dotenv(path: Path) -> dict[str, str]:
    """Very small .env parser — no quoting/interpolation tricks, just KEY=VAL."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def build_command(spec: AgentSpec, task: str | None, instruction: str | None) -> list[str]:
    cmd: list[str] = [str(spec.python), *map(str, spec.entrypoint)]
    if spec.name == "m3a":
        if not task:
            raise ValueError("M3A requires --task")
        cmd += [f"--task={task}"]
    elif spec.name == "mobiagent":
        # Runner CLI flags come from env vars; instruction goes into task.json.
        env = os.environ
        cmd += [
            "--service_ip",
            env.get("MOBIMIND_SERVICE_IP", "127.0.0.1"),
            "--decider_port",
            env.get("MOBIMIND_DECIDER_PORT", "8000"),
            "--planner_port",
            env.get("MOBIMIND_PLANNER_PORT", "8002"),
            "--user_profile",
            "off",
            "--use_graphrag",
            "off",
        ]
        if grounder := env.get("MOBIMIND_GROUNDER_PORT"):
            cmd += ["--grounder_port", grounder]
        if api_key := env.get("MOBIMIND_API_KEY"):
            cmd += ["--api_key", api_key]
    # Mobile-Agent-v2 reads MAV2_INSTRUCTION from env — no CLI flag.
    return cmd


def _write_mobiagent_task(spec: AgentSpec, instruction: str) -> Path:
    """Write the instruction into MobiAgent's task.json (required format)."""
    task_path = spec.cwd / "runner" / "mobiagent" / "task.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        json.dumps(
            [
                {
                    "task_description": instruction,
                    "app_name": "Settings",
                    "package_name": "com.android.settings",
                }
            ],
            indent=2,
        )
    )
    return task_path


def _check_mobiagent_env(env: dict[str, str]) -> None:
    missing = [k for k in _MOBIAGENT_REQUIRED_ENV if not env.get(k)]
    if missing:
        raise SystemExit(
            f"MobiAgent requires env vars {missing}. See "
            "mobile_agents/mobiagent/README.md (BYO inference endpoints)."
        )


def build_env(spec: AgentSpec, instruction: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(_load_dotenv(spec.env_file) if spec.env_file else {})
    env.update(spec.extra_env)
    env["PYTHONUNBUFFERED"] = "1"
    if spec.name == "mobile_agent_v2" and instruction:
        env["MAV2_INSTRUCTION"] = instruction
    return env


def _write_manifest(manifest_dir: Path, data: dict[str, Any]) -> Path:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / "manifest.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def run_agent(
    agent: str,
    task: str | None = None,
    instruction: str | None = None,
    dry_run: bool = False,
) -> int:
    if agent not in AGENTS:
        raise SystemExit(f"Unknown agent {agent!r}. Choices: {sorted(AGENTS)}")
    spec = AGENTS[agent]
    if not spec.python.exists():
        raise SystemExit(
            f"Agent venv missing: {spec.python}. Did you run the mobile_agents/ setup?"
        )

    env = build_env(spec, instruction)
    # Expose env vars for build_command's MobiAgent CLI-flag lookup.
    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        cmd = build_command(spec, task, instruction)
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    if spec.name == "mobiagent" and instruction and not dry_run:
        _check_mobiagent_env(env)
        _write_mobiagent_task(spec, instruction)

    if dry_run:
        print(
            json.dumps(
                {
                    "agent": spec.name,
                    "cwd": str(spec.cwd),
                    "cmd": cmd,
                    "task": task,
                    "instruction": instruction,
                },
                indent=2,
            )
        )
        return 0

    timestamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_DIR / f"{timestamp}_{spec.name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    # Open logs unbuffered so `tail -f` works during the run. Inherit stdio
    # to the child via file descriptors rather than PIPE, so the subprocess
    # writes directly to disk and to our terminal through `tee(1)`-free
    # mirroring: we duplicate the log file onto the parent's fds using
    # a small "tee" helper thread.
    print(f"[vigil-agent-run] logs: {run_dir}")

    with stdout_path.open("wb", buffering=0) as out_f, stderr_path.open("wb", buffering=0) as err_f:
        proc = subprocess.Popen(
            cmd,
            cwd=str(spec.cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert proc.stdout is not None and proc.stderr is not None

        import threading

        def _tee(src, file_f, mirror) -> None:
            while True:
                chunk = src.read(1)
                if not chunk:
                    break
                file_f.write(chunk)
                mirror.write(chunk)
                mirror.flush()

        t_out = threading.Thread(
            target=_tee, args=(proc.stdout, out_f, sys.stdout.buffer), daemon=True
        )
        t_err = threading.Thread(
            target=_tee, args=(proc.stderr, err_f, sys.stderr.buffer), daemon=True
        )
        t_out.start()
        t_err.start()
        proc.wait()
        t_out.join()
        t_err.join()
    returncode = proc.returncode

    manifest = {
        "agent": spec.name,
        "task": task,
        "instruction": instruction,
        "cmd": cmd,
        "cwd": str(spec.cwd),
        "returncode": returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "artifacts_dir": str(run_dir),
        "started_at": timestamp,
    }
    manifest_path = _write_manifest(run_dir, manifest)
    print(f"\n[vigil-agent-run] manifest: {manifest_path}")
    return returncode


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="vigil-agent-run", description=__doc__)
    p.add_argument("--agent", required=True, choices=sorted(AGENTS))
    p.add_argument("--task", help="M3A task name (e.g. ContactsAddContact)")
    p.add_argument("--instruction", help="Natural-language instruction for Mobile-Agent-v2")
    p.add_argument("--dry-run", action="store_true", help="Print command without executing")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_agent(
        agent=args.agent,
        task=args.task,
        instruction=args.instruction,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
