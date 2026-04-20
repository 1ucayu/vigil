"""Stage 0: App Prior Extraction.

Primary path: Androguard on a device-pulled APK. Loads manifest, resolves
string resources, and decodes all ``res/layout/*.xml`` files in a single
pass — no external ``aapt2`` dependency.

Fallback paths, kept for system apps whose APK isn't pullable or for
offline users:

- ``extract_from_manifest(path)`` — legacy text-XML parse for
  pre-decompiled AOSP manifests passed via ``--manifest``.
- ``_parse_dumpsys`` — regex scrape of ``adb shell dumpsys package`` when
  ``adb pull`` is denied (e.g., privileged system apps on non-root
  builds).

``extract_from_device_serial`` tries Androguard first and transparently
falls back to ``_parse_dumpsys`` on any documented failure.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from loguru import logger
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


class WidgetDecl(BaseModel):
    """A widget declared in a layout XML file."""

    widget_id: str
    widget_class: str
    layout_file: str


class AppPrior(BaseModel):
    """Prior knowledge extracted from an Android app."""

    package_name: str
    entry_activity: str | None = None
    activities: list[ActivityInfo] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    skeleton_edges: list[tuple[str, str]] = Field(default_factory=list)
    string_constants: dict[str, str] = Field(default_factory=dict)
    string_arrays: dict[str, list[str]] = Field(default_factory=dict)
    widget_declarations: list[WidgetDecl] = Field(default_factory=list)

    # Transient (excluded from the persisted JSON) — the extractor sets
    # these when available so ``save(static_dir)`` can write the raw
    # decoded sources alongside ``app_prior.json``.
    raw_manifest_xml: str | None = Field(default=None, exclude=True)
    raw_strings_xml: str | None = Field(default=None, exclude=True)
    layout_xmls: dict[str, str] = Field(default_factory=dict, exclude=True)

    def save(self, static_dir: Path) -> None:
        """Persist the prior JSON + any attached raw decoded XMLs to the
        ``static/`` directory of an app data dir.

        Layout::

            static_dir/app_prior.json
            static_dir/AndroidManifest.xml   (if raw_manifest_xml is set)
            static_dir/strings.xml           (if raw_strings_xml is set)
            static_dir/layouts/<name>.xml    (one per layout_xmls entry)

        Each write is individually guarded against ``OSError`` — a
        non-writable dir or a single corrupt layout does not abort the
        whole save.
        """
        static_dir = Path(static_dir)
        static_dir.mkdir(parents=True, exist_ok=True)
        try:
            (static_dir / "app_prior.json").write_text(
                self.model_dump_json(indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(f"Failed to write app_prior.json: {exc}")

        if self.raw_manifest_xml:
            try:
                (static_dir / "AndroidManifest.xml").write_text(
                    self.raw_manifest_xml, encoding="utf-8"
                )
            except OSError as exc:
                logger.warning(f"Failed to write AndroidManifest.xml: {exc}")

        if self.raw_strings_xml:
            try:
                (static_dir / "strings.xml").write_text(self.raw_strings_xml, encoding="utf-8")
            except OSError as exc:
                logger.warning(f"Failed to write strings.xml: {exc}")

        if self.layout_xmls:
            layouts_dir = static_dir / "layouts"
            try:
                layouts_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(f"Failed to create layouts/: {exc}")
                return
            for name, content in self.layout_xmls.items():
                safe = name.replace("/", "_")
                try:
                    (layouts_dir / safe).write_text(content, encoding="utf-8")
                except OSError as exc:
                    logger.warning(f"Failed to write layouts/{safe}: {exc}")

    @classmethod
    def load(cls, static_dir: Path) -> AppPrior:
        """Load an ``AppPrior`` previously saved via :meth:`save`. Reads
        ``app_prior.json`` only — raw XMLs stay on disk for downstream
        stages that want them."""
        path = Path(static_dir) / "app_prior.json"
        return cls.load_file(path)

    @classmethod
    def load_file(cls, path: Path) -> AppPrior:
        """Load from an explicit JSON path. Back-compat shim for legacy
        ``static/prior.json`` / ``prior.json`` layouts."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class AppPriorExtractor:
    """Extract structural prior knowledge from Android app metadata."""

    def extract_from_apk_file(self, apk_path: Path) -> AppPrior:
        """Load an APK via Androguard and populate an :class:`AppPrior`.

        Exercises Androguard's high-level APK object (activities, main
        activity, permissions), walks the decoded AndroidManifest XML to
        recover per-activity ``parentActivityName`` / ``launchMode`` /
        ``label`` attributes (which the high-level accessors drop), and
        iterates ``res/layout*/`` AXML files for widget declarations.
        Raw decoded XMLs are stashed on the transient fields so
        :meth:`AppPrior.save` can mirror them to ``static/``.
        """
        from androguard.core.apk import APK  # type: ignore[import-untyped]
        from androguard.core.axml import AXMLPrinter  # type: ignore[import-untyped]

        apk = APK(str(apk_path))
        package = apk.get_package() or ""
        manifest_axml = apk.get_android_manifest_axml()
        raw_manifest = manifest_axml.get_xml().decode("utf-8", errors="replace")

        # Parse the decoded manifest XML for full activity attributes —
        # Androguard's get_activities() returns bare names, and we need
        # parentActivityName / launchMode / label / intent filters.
        prior = self.extract_from_manifest_string(raw_manifest)
        prior.raw_manifest_xml = raw_manifest
        if not prior.package_name:
            prior.package_name = package

        # Supplement permissions from Androguard in case the XML parse
        # missed any (e.g. merged from platform manifests).
        for perm in apk.get_permissions() or []:
            if perm and perm not in prior.permissions:
                prior.permissions.append(perm)

        # Strings: walk the ARSC package table directly. Androguard exposes
        # resolved entries at ``arsc.values[pkg][locale]['string']`` as a list
        # of ``[name, text]`` pairs. The default locale key is the null-byte
        # string ``'\x00\x00'`` — NOT ``'DEFAULT'`` (that label only appears
        # in ``get_resolved_strings()``'s alternate view, which keys by
        # integer resource id, not by symbolic name).
        arsc = apk.get_android_resources()
        if arsc is not None:
            # ``arsc.values`` is lazy-populated; calling get_resolved_strings()
            # triggers the full package/locale/type walk that fills it.
            try:
                arsc.get_resolved_strings()
            except Exception as exc:
                logger.debug(f"get_resolved_strings() failed: {exc}", exc_info=True)
            for pkg_name in arsc.get_packages_names() or []:
                pkg_locales = getattr(arsc, "values", {}).get(pkg_name, {})
                if not isinstance(pkg_locales, dict):
                    continue
                locale_data = pkg_locales.get("\x00\x00")
                if not (isinstance(locale_data, dict) and locale_data.get("string")):
                    locale_data = next(
                        (
                            d
                            for d in pkg_locales.values()
                            if isinstance(d, dict) and d.get("string")
                        ),
                        None,
                    )
                if not locale_data:
                    continue
                for entry in locale_data.get("string", []):
                    if (
                        isinstance(entry, list | tuple)
                        and len(entry) >= 2
                        and isinstance(entry[0], str)
                        and entry[0]
                        and isinstance(entry[1], str)
                    ):
                        prior.string_constants.setdefault(entry[0], entry[1])

        # Best-effort strings.xml serialization for ``static/strings.xml``.
        if prior.string_constants:
            lines = ['<?xml version="1.0" encoding="utf-8"?>', "<resources>"]
            for name, text in prior.string_constants.items():
                escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                lines.append(f'    <string name="{name}">{escaped}</string>')
            lines.append("</resources>")
            prior.raw_strings_xml = "\n".join(lines)

        # Layouts: decode every res/layout*/*.xml file via AXMLPrinter.
        for fn in apk.get_files():
            if not (fn.startswith("res/layout") and fn.endswith(".xml")):
                continue
            try:
                payload = apk.get_file(fn)
                if not payload:
                    continue
                axml = AXMLPrinter(payload)
                decoded = axml.get_xml().decode("utf-8", errors="replace")
            except Exception as exc:
                logger.debug(f"Failed to decode {fn}: {exc}", exc_info=True)
                continue
            short = Path(fn).name
            prior.layout_xmls[short] = decoded
            self._parse_layout_xml_string(decoded, Path(fn).stem, prior)

        logger.info(
            f"Androguard extraction: {len(prior.activities)} activities, "
            f"{len(prior.permissions)} permissions, "
            f"{len(prior.string_constants)} strings, "
            f"{len(prior.widget_declarations)} widgets from "
            f"{len(prior.layout_xmls)} layouts"
        )
        return prior

    def extract_from_manifest(self, manifest_path: Path) -> AppPrior:
        """Legacy: parse a pre-decompiled AndroidManifest.xml text file.

        Kept for users who pass ``--manifest foo.xml`` directly. Prefer
        :meth:`extract_from_apk_file` when an APK is available.
        """
        tree = ET.parse(manifest_path)  # noqa: S314
        return self._parse_manifest_tree(tree)

    def extract_from_manifest_string(self, xml_string: str) -> AppPrior:
        """Parse an AndroidManifest.xml from a string."""
        root = ET.fromstring(xml_string)  # noqa: S314
        tree = ET.ElementTree(root)
        return self._parse_manifest_tree(tree)

    def extract_from_device(self, device, package: str) -> AppPrior:
        """Extract app info from a connected device via adb dumpsys."""
        output = device.shell(f"dumpsys package {package}")
        return self._parse_dumpsys(package, output)

    def extract_from_device_serial(self, serial: str, package: str) -> AppPrior:
        """Preferred device extraction path: pull the APK, parse with Androguard.

        Falls back to ``_parse_dumpsys`` when any of the following occurs,
        with the exact failure logged so the operator can diagnose:
          - ``adb shell pm path`` returns no APK path.
          - ``adb pull`` fails (permission denied, device offline, etc.).
          - Androguard raises while parsing the pulled APK.

        Never raises at the outer level.
        """
        prior, _raw = self.extract_from_device_serial_with_dump(serial, package)
        return prior

    def extract_from_device_serial_with_dump(
        self, serial: str, package: str
    ) -> tuple[AppPrior, str]:
        """Device extraction that also returns raw ``dumpsys`` output when the
        fallback path is taken. Androguard path returns an empty dump
        string (``""``) — callers looking to cache the raw device info
        should also write the APK separately.
        """
        try:
            path_result = subprocess.run(
                ["adb", "-s", serial, "shell", "pm", "path", package],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning(f"adb pm path failed ({exc}); falling back to dumpsys")
            return self._dumpsys_fallback(serial, package)
        if path_result.returncode != 0:
            logger.warning(
                f"pm path returned rc={path_result.returncode}: "
                f"{path_result.stderr.strip()!r}; falling back to dumpsys"
            )
            return self._dumpsys_fallback(serial, package)

        apk_paths = [
            ln.removeprefix("package:").strip()
            for ln in path_result.stdout.splitlines()
            if ln.startswith("package:")
        ]
        if not apk_paths:
            logger.warning(
                f"pm path returned no APK entries for {package}: "
                f"{path_result.stdout!r}; falling back to dumpsys"
            )
            return self._dumpsys_fallback(serial, package)

        # Prefer a ``base.apk`` entry if present (common for user-installed
        # apps); otherwise pick the first APK path (system apps on Pixel /
        # AOSP use names like ``SettingsGoogle.apk``). Split APKs without a
        # base entry aren't handled — fall through to dumpsys.
        remote_apk = next(
            (p for p in apk_paths if p.endswith("/base.apk")),
            apk_paths[0] if len(apk_paths) == 1 else None,
        )
        if remote_apk is None:
            logger.warning(
                f"Split APK layout without base.apk detected for {package}: "
                f"{apk_paths}; falling back to dumpsys"
            )
            return self._dumpsys_fallback(serial, package)

        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "app.apk"
            try:
                pull_result = subprocess.run(
                    ["adb", "-s", serial, "pull", remote_apk, str(local)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                logger.warning(f"adb pull failed ({exc}); falling back to dumpsys")
                return self._dumpsys_fallback(serial, package)
            if pull_result.returncode != 0 or not local.exists():
                logger.warning(
                    f"adb pull {remote_apk} failed rc={pull_result.returncode}: "
                    f"{pull_result.stderr.strip()!r}; falling back to dumpsys"
                )
                return self._dumpsys_fallback(serial, package)

            try:
                prior = self.extract_from_apk_file(local)
            except Exception as exc:
                logger.warning(
                    f"Androguard failed to parse {remote_apk}: {exc}; falling back to dumpsys"
                )
                return self._dumpsys_fallback(serial, package)

            return prior, ""

    def _dumpsys_fallback(self, serial: str, package: str) -> tuple[AppPrior, str]:
        """Regex-scrape ``adb shell dumpsys package`` when APK extraction isn't possible."""
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "dumpsys", "package", package],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning(f"adb dumpsys failed: {exc}")
            return AppPrior(package_name=package), ""
        if result.returncode != 0:
            logger.warning(f"dumpsys failed: {result.stderr}")
            return AppPrior(package_name=package), result.stdout or ""
        return self._parse_dumpsys(package, result.stdout), result.stdout

    def extract_resources(self, apk_dir: Path, prior: AppPrior) -> None:
        """Extract resources from an apktool-decompiled APK directory.

        Parses res/values/strings.xml and res/layout/*.xml into the prior.
        """
        strings_path = apk_dir / "res" / "values" / "strings.xml"
        if strings_path.exists():
            self._parse_strings_xml(strings_path, prior)

        layout_dir = apk_dir / "res" / "layout"
        if layout_dir.is_dir():
            for layout_file in sorted(layout_dir.glob("*.xml")):
                self._parse_layout_xml(layout_file, prior)

        logger.info(
            f"Resources extracted: {len(prior.string_constants)} strings, "
            f"{len(prior.string_arrays)} arrays, "
            f"{len(prior.widget_declarations)} widgets"
        )

    @staticmethod
    def _parse_strings_xml(path: Path, prior: AppPrior) -> None:
        """Parse res/values/strings.xml."""
        try:
            tree = ET.parse(path)  # noqa: S314
        except ET.ParseError:
            return

        root = tree.getroot()
        for elem in root.findall("string"):
            name = elem.get("name", "")
            text = elem.text or ""
            if name and text:
                prior.string_constants[name] = text

        for elem in root.findall("string-array"):
            name = elem.get("name", "")
            if not name:
                continue
            items = [item.text or "" for item in elem.findall("item")]
            if items:
                prior.string_arrays[name] = items

    @staticmethod
    def _parse_layout_xml(path: Path, prior: AppPrior) -> None:
        """Parse a single res/layout/*.xml for widget declarations."""
        try:
            tree = ET.parse(path)  # noqa: S314
        except ET.ParseError:
            return

        android_ns = "http://schemas.android.com/apk/res/android"
        layout_name = path.stem

        for elem in tree.iter():
            widget_id = elem.get(f"{{{android_ns}}}id", "")
            if not widget_id:
                continue
            widget_id = widget_id.replace("@+id/", "").replace("@id/", "")
            widget_class = elem.tag.rsplit(".", 1)[-1] if "." in elem.tag else elem.tag
            prior.widget_declarations.append(
                WidgetDecl(
                    widget_id=widget_id,
                    widget_class=widget_class,
                    layout_file=layout_name,
                )
            )

    @staticmethod
    def _parse_layout_xml_string(xml_string: str, layout_name: str, prior: AppPrior) -> None:
        """In-memory sibling of :meth:`_parse_layout_xml` for Androguard-decoded
        layouts. Same widget-extraction rules, no file I/O."""
        try:
            root = ET.fromstring(xml_string)  # noqa: S314
        except ET.ParseError:
            return
        android_ns = "http://schemas.android.com/apk/res/android"
        for elem in root.iter():
            widget_id = elem.get(f"{{{android_ns}}}id", "")
            if not widget_id:
                continue
            widget_id = widget_id.replace("@+id/", "").replace("@id/", "")
            widget_class = elem.tag.rsplit(".", 1)[-1] if "." in elem.tag else elem.tag
            prior.widget_declarations.append(
                WidgetDecl(
                    widget_id=widget_id,
                    widget_class=widget_class,
                    layout_file=layout_name,
                )
            )

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
