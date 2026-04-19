"""Smoke tests for vigil-doctor."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from vigil.scripts import doctor


def test_doctor_exits_nonzero_with_no_devices(capsys, monkeypatch):
    """When no devices are visible, doctor must report failure and exit 1."""
    monkeypatch.setattr(sys, "argv", ["vigil-doctor", "--config", "/nonexistent/path.yaml"])
    with (
        patch("adbutils.adb.device_list", return_value=[]),
        pytest.raises(SystemExit) as excinfo,
    ):
        doctor.main()
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "ADB sees" in out
