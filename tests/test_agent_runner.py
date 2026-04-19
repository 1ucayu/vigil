"""Unit tests for vigil.integration.agent_runner (no emulator, no LLM)."""

from __future__ import annotations

import json

import pytest

from vigil.integration import agent_runner


def test_build_command_m3a_requires_task():
    with pytest.raises(ValueError):
        agent_runner.build_command(agent_runner.M3A, task=None, instruction=None)


def test_build_command_m3a_with_task():
    cmd = agent_runner.build_command(agent_runner.M3A, task="ContactsAddContact", instruction=None)
    assert cmd[0].endswith("/bin/python")
    assert "minimal_task_runner.py" in cmd
    assert "--task=ContactsAddContact" in cmd


def test_build_command_mav2_no_cli_flag_for_instruction():
    cmd = agent_runner.build_command(
        agent_runner.MOBILE_AGENT_V2, task=None, instruction="open settings"
    )
    # run.py reads instruction from env, not CLI
    assert not any("open settings" in part for part in cmd)
    assert "run.py" in cmd[-1]


def test_build_env_injects_mav2_instruction(tmp_path):
    spec = agent_runner.MOBILE_AGENT_V2
    env = agent_runner.build_env(spec, instruction="Open app")
    assert env["MAV2_INSTRUCTION"] == "Open app"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_dry_run_m3a(capsys):
    rc = agent_runner.run_agent("m3a", task="ContactsAddContact", instruction=None, dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["agent"] == "m3a"
    assert parsed["task"] == "ContactsAddContact"
    assert "minimal_task_runner.py" in " ".join(parsed["cmd"])


def test_dry_run_mav2(capsys):
    rc = agent_runner.run_agent(
        "mobile_agent_v2", task=None, instruction="Open Wi-Fi", dry_run=True
    )
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["agent"] == "mobile_agent_v2"
    assert parsed["instruction"] == "Open Wi-Fi"


def test_dry_run_mobiagent(capsys):
    rc = agent_runner.run_agent("mobiagent", task=None, instruction="Open Settings", dry_run=True)
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["agent"] == "mobiagent"
    cmd_str = " ".join(parsed["cmd"])
    assert "runner.mobiagent.mobiagent" in cmd_str
    assert "--service_ip" in cmd_str
    assert "--decider_port" in cmd_str


def test_build_command_mobiagent_uses_env(monkeypatch):
    monkeypatch.setenv("MOBIMIND_SERVICE_IP", "10.0.0.5")
    monkeypatch.setenv("MOBIMIND_DECIDER_PORT", "9000")
    monkeypatch.setenv("MOBIMIND_PLANNER_PORT", "9002")
    cmd = agent_runner.build_command(agent_runner.MOBIAGENT, task=None, instruction="x")
    assert "10.0.0.5" in cmd
    assert "9000" in cmd
    assert "9002" in cmd


def test_check_mobiagent_env_missing():
    with pytest.raises(SystemExit, match="MOBIMIND"):
        agent_runner._check_mobiagent_env({})


def test_unknown_agent():
    with pytest.raises(SystemExit):
        agent_runner.run_agent("bogus", dry_run=True)


def test_load_dotenv_missing_file_is_empty(tmp_path):
    assert agent_runner._load_dotenv(tmp_path / "nope.env") == {}


def test_load_dotenv_parses_simple(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nFOO=bar\nEMPTY=\nBAZ= qux \n")
    parsed = agent_runner._load_dotenv(f)
    assert parsed == {"FOO": "bar", "EMPTY": "", "BAZ": "qux"}
