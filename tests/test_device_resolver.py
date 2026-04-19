"""Tests for vigil.core.device_resolver."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vigil.core.config import DeviceConfig
from vigil.core.device_resolver import resolve_device_serial


def _mock_devices(*serials: str) -> list[SimpleNamespace]:
    """Build adb-style device stubs that expose a ``.serial`` attribute."""
    return [SimpleNamespace(serial=s) for s in serials]


def test_explicit_serial_passes_through_without_adb():
    """When ``serial`` is set, the resolver must not query ADB at all."""
    cfg = DeviceConfig(serial="foo123", type="auto")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.side_effect = AssertionError("adb should not be queried when serial is pinned")
        assert resolve_device_serial(cfg) == "foo123"


def test_type_emulator_filters_correctly():
    cfg = DeviceConfig(type="emulator")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.return_value = _mock_devices("emulator-5554", "ABCDEF12345")
        assert resolve_device_serial(cfg) == "emulator-5554"


def test_type_physical_filters_correctly():
    cfg = DeviceConfig(type="physical")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.return_value = _mock_devices("emulator-5554", "ABCDEF12345")
        assert resolve_device_serial(cfg) == "ABCDEF12345"


def test_type_auto_with_single_device_returns_it():
    cfg = DeviceConfig(type="auto")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.return_value = _mock_devices("emulator-5554")
        assert resolve_device_serial(cfg) == "emulator-5554"


def test_type_auto_with_multiple_devices_raises():
    cfg = DeviceConfig(type="auto")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.return_value = _mock_devices("emulator-5554", "ABCDEF12345")
        with pytest.raises(RuntimeError, match="Ambiguous"):
            resolve_device_serial(cfg)


def test_type_emulator_with_no_match_raises_with_visible_serials():
    cfg = DeviceConfig(type="emulator")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.return_value = _mock_devices("ABCDEF12345")
        with pytest.raises(RuntimeError) as excinfo:
            resolve_device_serial(cfg)
        msg = str(excinfo.value)
        assert "emulator" in msg
        assert "ABCDEF12345" in msg


def test_empty_device_list_raises():
    cfg = DeviceConfig(type="auto")
    with patch("vigil.core.device_resolver.adb.device_list") as m:
        m.return_value = []
        with pytest.raises(RuntimeError, match="No ADB devices"):
            resolve_device_serial(cfg)
