"""Tests for vigil.neuro.app_prior — Stage 0: App Prior Extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vigil.neuro.app_prior import ActivityInfo, AppPrior, AppPriorExtractor

_BASIC_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />
    <application>
        <activity
            android:name=".MainActivity"
            android:label="Main"
            android:exported="true"
            android:launchMode="singleTop">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <activity
            android:name=".SettingsActivity"
            android:label="Settings"
            android:parentActivityName=".MainActivity"
            android:exported="false" />
        <activity
            android:name="com.example.app.DetailActivity"
            android:parentActivityName=".SettingsActivity" />
    </application>
</manifest>
"""

_MINIMAL_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.minimal.app">
    <application>
        <activity android:name=".OnlyActivity" />
    </application>
</manifest>
"""

_EMPTY_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.empty.app">
    <application />
</manifest>
"""

_MULTI_INTENT_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">
    <application>
        <activity android:name=".BrowserActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""


@pytest.fixture
def extractor() -> AppPriorExtractor:
    return AppPriorExtractor()


class TestBasicExtraction:
    def test_package_name(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        assert prior.package_name == "com.example.app"

    def test_activity_count(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        assert len(prior.activities) == 3

    def test_activity_names_resolved(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        names = {a.name for a in prior.activities}
        assert "com.example.app.MainActivity" in names
        assert "com.example.app.SettingsActivity" in names
        assert "com.example.app.DetailActivity" in names

    def test_activity_labels(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        by_name = {a.name: a for a in prior.activities}
        assert by_name["com.example.app.MainActivity"].label == "Main"
        assert by_name["com.example.app.SettingsActivity"].label == "Settings"
        assert by_name["com.example.app.DetailActivity"].label is None

    def test_exported_flag(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        by_name = {a.name: a for a in prior.activities}
        assert by_name["com.example.app.MainActivity"].exported is True
        assert by_name["com.example.app.SettingsActivity"].exported is False
        assert by_name["com.example.app.DetailActivity"].exported is False

    def test_launch_mode(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        by_name = {a.name: a for a in prior.activities}
        assert by_name["com.example.app.MainActivity"].launch_mode == "singleTop"
        assert by_name["com.example.app.SettingsActivity"].launch_mode == "standard"


class TestLauncherDetection:
    def test_launcher_activity_detected(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        assert prior.entry_activity == "com.example.app.MainActivity"

    def test_launcher_flag_on_activity(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        by_name = {a.name: a for a in prior.activities}
        assert by_name["com.example.app.MainActivity"].is_launcher is True
        assert by_name["com.example.app.SettingsActivity"].is_launcher is False

    def test_no_launcher(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_MINIMAL_MANIFEST)
        assert prior.entry_activity is None
        assert prior.activities[0].is_launcher is False

    def test_multi_intent_filter_launcher(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_MULTI_INTENT_MANIFEST)
        assert prior.entry_activity == "com.example.app.BrowserActivity"
        a = prior.activities[0]
        assert a.is_launcher is True
        assert "android.intent.action.VIEW" in a.intent_actions
        assert "android.intent.action.MAIN" in a.intent_actions


class TestSkeletonEdges:
    def test_parent_child_edges(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        edges = prior.skeleton_edges
        assert ("com.example.app.MainActivity", "com.example.app.SettingsActivity") in edges
        assert ("com.example.app.SettingsActivity", "com.example.app.DetailActivity") in edges
        assert len(edges) == 2

    def test_no_edges_without_parents(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_MINIMAL_MANIFEST)
        assert prior.skeleton_edges == []


class TestPermissions:
    def test_permissions_extracted(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        assert "android.permission.INTERNET" in prior.permissions
        assert "android.permission.CAMERA" in prior.permissions
        assert len(prior.permissions) == 2

    def test_no_permissions(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_MINIMAL_MANIFEST)
        assert prior.permissions == []


class TestEdgeCases:
    def test_empty_manifest(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_EMPTY_MANIFEST)
        assert prior.package_name == "com.empty.app"
        assert prior.activities == []
        assert prior.permissions == []
        assert prior.skeleton_edges == []
        assert prior.entry_activity is None

    def test_fully_qualified_name_passthrough(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        names = {a.name for a in prior.activities}
        assert "com.example.app.DetailActivity" in names

    def test_dot_prefix_resolved(self, extractor: AppPriorExtractor) -> None:
        prior = extractor.extract_from_manifest_string(_BASIC_MANIFEST)
        names = {a.name for a in prior.activities}
        assert "com.example.app.MainActivity" in names
        assert ".MainActivity" not in names

    def test_file_based_extraction(self, extractor: AppPriorExtractor, tmp_path) -> None:
        manifest_path = tmp_path / "AndroidManifest.xml"
        manifest_path.write_text(_BASIC_MANIFEST)
        prior = extractor.extract_from_manifest(manifest_path)
        assert prior.package_name == "com.example.app"
        assert len(prior.activities) == 3


class TestExtractFromDeviceSerial:
    def test_basic_dumpsys_parsing(self, extractor: AppPriorExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "Activity Resolver Table:\n"
            "  Non-Data Actions:\n"
            "      android.intent.action.MAIN:\n"
            "        12345 com.android.settings/.Settings filter abcdef\n"
            "        12346 com.android.settings/.wifi.WifiSettings\n"
            "      requested permissions:\n"
            "        android.permission.INTERNET\n"
            "        android.permission.ACCESS_WIFI_STATE\n"
        )
        with patch("subprocess.run", return_value=mock_result):
            prior = extractor.extract_from_device_serial("fake_serial", "com.android.settings")
        assert prior.package_name == "com.android.settings"
        assert len(prior.activities) >= 1

    def test_adb_failure_returns_empty(self, extractor: AppPriorExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "device not found"
        with patch("subprocess.run", return_value=mock_result):
            prior = extractor.extract_from_device_serial("bad_serial", "com.pkg")
        assert prior.package_name == "com.pkg"
        assert prior.activities == []

    def test_adb_timeout_returns_empty(self, extractor: AppPriorExtractor) -> None:
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("adb", 30)):
            prior = extractor.extract_from_device_serial("slow", "com.pkg")
        assert prior.package_name == "com.pkg"
        assert prior.activities == []


# ============================================================
# Directory-form save / load + transient raw-XML persistence
# ============================================================


class TestSaveLoadRoundTrip:
    def test_app_prior_json_round_trip(self, tmp_path: Path) -> None:
        prior = AppPrior(
            package_name="com.example",
            entry_activity="com.example.Main",
            activities=[ActivityInfo(name="com.example.Main", is_launcher=True)],
            permissions=["android.permission.INTERNET"],
        )
        prior.save(tmp_path / "static")
        assert (tmp_path / "static" / "app_prior.json").exists()
        loaded = AppPrior.load(tmp_path / "static")
        assert loaded == prior

    def test_transient_fields_not_in_json(self, tmp_path: Path) -> None:
        prior = AppPrior(
            package_name="com.example",
            raw_manifest_xml="<manifest />",
            raw_strings_xml='<?xml version="1.0"?><resources/>',
            layout_xmls={"a.xml": "<LinearLayout/>", "b.xml": "<FrameLayout/>"},
        )
        prior.save(tmp_path / "static")
        # Raw XMLs written to their named files.
        assert (tmp_path / "static" / "AndroidManifest.xml").read_text() == "<manifest />"
        assert "resources" in (tmp_path / "static" / "strings.xml").read_text()
        assert (tmp_path / "static" / "layouts" / "a.xml").read_text() == "<LinearLayout/>"
        assert (tmp_path / "static" / "layouts" / "b.xml").read_text() == "<FrameLayout/>"
        # app_prior.json excludes the transient fields.
        import json

        data = json.loads((tmp_path / "static" / "app_prior.json").read_text())
        assert "raw_manifest_xml" not in data
        assert "raw_strings_xml" not in data
        assert "layout_xmls" not in data

    def test_load_file_back_compat(self, tmp_path: Path) -> None:
        prior = AppPrior(package_name="com.example")
        legacy = tmp_path / "prior.json"
        legacy.write_text(prior.model_dump_json(indent=2), encoding="utf-8")
        assert AppPrior.load_file(legacy) == prior

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            AppPrior.load(tmp_path / "static")
