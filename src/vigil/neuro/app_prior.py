"""Stage 0: App Prior Extraction from AndroidManifest.xml.

Parses AndroidManifest.xml to extract structural prior knowledge:
- Activity names, labels, parent relationships → hierarchy skeleton
- Launcher activity → FSM initial state candidate
- Permissions → feature capability hints

Three extraction paths:
1. extract_from_manifest(path) — direct XML file
2. extract_from_apk(path) — via aapt2 dump
3. extract_from_device(device, package) — via adb dumpsys (fallback)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field

_ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _android(attr: str) -> str:
    return f"{{{_ANDROID_NS}}}{attr}"


class ActivityInfo(BaseModel):
    """Parsed info about a single <activity> from AndroidManifest."""

    name: str
    label: str | None = None
    parent_activity: str | None = None
    exported: bool = False
    launch_mode: str = "standard"
    intent_actions: list[str] = Field(default_factory=list)
    is_launcher: bool = False
    predicted_function: str | None = None


class AppPrior(BaseModel):
    """Prior knowledge extracted from an Android app."""

    package_name: str
    entry_activity: str | None = None
    activities: list[ActivityInfo] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    skeleton_edges: list[tuple[str, str]] = Field(default_factory=list)


class AppPriorExtractor:
    """Extract structural prior knowledge from Android app metadata."""

    def extract_from_manifest(self, manifest_path: Path) -> AppPrior:
        """Parse an AndroidManifest.xml file."""
        tree = ET.parse(manifest_path)  # noqa: S314
        return self._parse_manifest_tree(tree)

    def extract_from_manifest_string(self, xml_string: str) -> AppPrior:
        """Parse an AndroidManifest.xml from a string."""
        root = ET.fromstring(xml_string)  # noqa: S314
        tree = ET.ElementTree(root)
        return self._parse_manifest_tree(tree)

    def extract_from_apk(self, apk_path: Path) -> AppPrior:
        """Extract manifest from APK via aapt2 and parse it."""
        result = subprocess.run(
            ["aapt2", "dump", "xmltree", str(apk_path), "--file", "AndroidManifest.xml"],
            capture_output=True,
            text=True,
            check=True,
        )
        return self.extract_from_manifest_string(result.stdout)

    def extract_from_device(self, device, package: str) -> AppPrior:
        """Extract app info from a connected device via adb dumpsys."""
        output = device.shell(f"dumpsys package {package}")
        return self._parse_dumpsys(package, output)

    def _parse_manifest_tree(self, tree: ET.ElementTree) -> AppPrior:
        root = tree.getroot()
        package = root.get("package", "")

        activities: list[ActivityInfo] = []
        entry_activity: str | None = None
        skeleton_edges: list[tuple[str, str]] = []

        for elem in root.iter("activity"):
            info = self._parse_activity_element(elem, package)
            activities.append(info)
            if info.is_launcher and entry_activity is None:
                entry_activity = info.name
            if info.parent_activity:
                skeleton_edges.append((info.parent_activity, info.name))

        for elem in root.iter("activity-alias"):
            info = self._parse_activity_element(elem, package)
            activities.append(info)
            if info.is_launcher and entry_activity is None:
                target = elem.get(_android("targetActivity"), "")
                entry_activity = self._resolve_class_name(target, package)

        permissions: list[str] = []
        for elem in root.iter("uses-permission"):
            perm = elem.get(_android("name"), "")
            if perm:
                permissions.append(perm)

        return AppPrior(
            package_name=package,
            entry_activity=entry_activity,
            activities=activities,
            permissions=permissions,
            skeleton_edges=skeleton_edges,
        )

    def _parse_activity_element(self, elem: ET.Element, package: str) -> ActivityInfo:
        raw_name = elem.get(_android("name"), "")
        name = self._resolve_class_name(raw_name, package)
        label = elem.get(_android("label"))
        parent = elem.get(_android("parentActivityName"))
        if parent:
            parent = self._resolve_class_name(parent, package)

        exported_str = elem.get(_android("exported"))
        exported = exported_str == "true" if exported_str is not None else False

        launch_mode = elem.get(_android("launchMode"), "standard")

        intent_actions: list[str] = []
        is_launcher = False
        for intent_filter in elem.findall("intent-filter"):
            actions = [
                a.get(_android("name"), "")
                for a in intent_filter.findall("action")
                if a.get(_android("name"))
            ]
            intent_actions.extend(actions)
            categories = {c.get(_android("name"), "") for c in intent_filter.findall("category")}
            has_main = "android.intent.action.MAIN" in actions
            has_launcher = "android.intent.category.LAUNCHER" in categories
            if has_main and has_launcher:
                is_launcher = True

        return ActivityInfo(
            name=name,
            label=label,
            parent_activity=parent,
            exported=exported,
            launch_mode=launch_mode,
            intent_actions=intent_actions,
            is_launcher=is_launcher,
        )

    @staticmethod
    def _resolve_class_name(name: str, package: str) -> str:
        if not name:
            return name
        if name.startswith("."):
            return package + name
        return name

    def _parse_dumpsys(self, package: str, output: str) -> AppPrior:
        activities: list[ActivityInfo] = []
        permissions: list[str] = []
        entry_activity: str | None = None

        in_activity_section = False
        activity_pattern = re.compile(r"^\s+[0-9a-f]+ ([\w.]+)/([\w.$]+)")
        requested_perm_pattern = re.compile(r"^\s+(android\.permission\.[\w.]+)")

        in_requested_perms = False
        for line in output.splitlines():
            if "Activity Resolver Table:" in line:
                in_activity_section = True
                continue
            if in_activity_section and line.strip() and not line.startswith(" "):
                in_activity_section = False

            if in_activity_section:
                m = activity_pattern.match(line)
                if m:
                    pkg, cls = m.group(1), m.group(2)
                    full_name = cls if "." in cls else f"{pkg}.{cls}"
                    if not any(a.name == full_name for a in activities):
                        activities.append(ActivityInfo(name=full_name))

            if "requested permissions:" in line:
                in_requested_perms = True
                continue
            if in_requested_perms:
                if line.strip() and not line.startswith(" "):
                    in_requested_perms = False
                else:
                    pm = requested_perm_pattern.match(line)
                    if pm:
                        permissions.append(pm.group(1))

        # Detect launcher from dumpsys (look for MAIN/LAUNCHER in resolver table)
        launcher_pattern = re.compile(
            r"android\.intent\.action\.MAIN.*category.*LAUNCHER.*"
            + re.escape(package)
            + r"/([\w.$]+)"
        )
        for line in output.splitlines():
            m = launcher_pattern.search(line)
            if m:
                cls = m.group(1)
                entry_activity = cls if "." in cls else f"{package}.{cls}"
                for a in activities:
                    if a.name == entry_activity:
                        a.is_launcher = True
                break

        return AppPrior(
            package_name=package,
            entry_activity=entry_activity,
            activities=activities,
            permissions=permissions,
            skeleton_edges=[],
        )
