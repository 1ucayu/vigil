"""Stage 4: DSL Semantic Guard Generation.

Annotates FSM transitions with guard conditions using a constrained formal grammar
(`output_docs/dsl_grammar.lark`, falling back to the historical `docs/` path).
Uses LLM + multimodal input (screenshots + element tables) to generate syntactically
correct guards.

Every click transition gets a guard via trace-guided LLM prompts showing the
source → action → target triple. Back/home/scroll transitions are skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lark import Lark
from loguru import logger

from vigil.core.config import VigilConfig
from vigil.core.llm_client import LlmClient
from vigil.core.paths import resolve_dsl_grammar_path
from vigil.models.fsm import AbstractState, AppFSM, Transition

_MAX_ELEMENTS_PER_SCREEN = 30

_SKIP_ACTIONS = frozenset({"navigate_back", "navigate_home", "scroll_up", "scroll_down"})

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a formal verification expert generating DSL guard expressions \
for a mobile app's FSM transitions.

A guard is a precondition that must be true BEFORE a UI action is allowed. \
Guards verify that the agent's proposed action matches the user's intent.

Guards are written in this grammar:

{grammar}

Available predicates:
- read(element, property) op value — check a UI element's property
- value(element) op value — shorthand for read(element, value)
- action(property) op value — check the proposed action's metadata
- contains(element, value) — check if element contains a value
- count(element) op value — check child count
- in_state(name) — check current FSM state
- time_in(HH:MM, HH:MM) — time range check

Readable properties for read(elem, prop) — use these names exactly:
  text, content_description, value, class_name, resource_id, bounds,
  is_clickable, is_long_clickable, is_checkable, is_checked,
  is_enabled, is_editable, is_scrollable, is_focusable,
  is_focused, is_selected, is_password, children_count
Any other property name will evaluate to null at runtime.

Use $intent.variable_name for values that depend on the user's instruction \
and are only known at runtime. Choose descriptive variable names \
(e.g., $intent.wifi_name, $intent.target_setting, $intent.device_name).

RULES:
1. For element references, use the Alias from the element table (resource_id \
or synthesized alias like Switch_0). NEVER use e_XXXX IDs.
2. Element and property names are bare identifiers — no quotes around them.
3. For menu/navigation clicks, generate: action(target_text) == $intent.<variable>
4. For toggles/switches, generate: read(<alias>, is_checked) == true/false
5. For Cancel/Dismiss buttons on dialogs, return "null" — cancellation needs no precondition.
6. For OK/Confirm buttons on dialogs with input controls (TimePicker, DatePicker, \
EditText), generate a correctness guard checking the input value.

GUARD CATEGORIES:
7. Safety guards (intent-independent) verify structural preconditions: \
element is enabled/clickable/editable, list has items. Use ONLY literal values.
8. Correctness guards (intent-dependent) verify user intent alignment: \
clicked item matches target, input value matches specification. \
Use $intent.variable_name for runtime values.
9. When both apply, combine: safety_guard && correctness_guard

Respond with ONLY the guard expression or "null". No explanation, no markdown."""

_USER_PROMPT = """\
## Transition Context
Source state: {source_name}
{source_description}\
{source_page_function}\
{source_expected_actions}\
Action: {action_type} on element "{target_text}" (alias: {target_alias})
Target state: {target_name}
{target_description}\

## Source Screen Elements (before action)
{source_table}

## Target Screen Elements (after action)
{target_table}

## What changed (source → target)
{diff_summary}

{widget_hint}\
{closed_set_hint}\
{extra_context}\
Generate a guard expression (or "null" if no precondition is needed):"""


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


class DslGenerator:
    """Generate DSL guard templates for FSM transitions using LLM + screenshots."""

    def __init__(
        self,
        fsm: AppFSM,
        config: VigilConfig,
        app_prior: Any | None = None,
    ) -> None:
        self._fsm = fsm
        self._config = config
        self._llm = LlmClient(config.llm)
        self._app_prior = app_prior
        grammar_path = resolve_dsl_grammar_path()
        self._grammar_text = grammar_path.read_text(encoding="utf-8")
        self._parser = Lark(self._grammar_text, parser="earley", start="start")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_all_guards(
        self,
        trace_path: Path,
        use_images: bool = True,
    ) -> AppFSM:
        """Generate guard templates for all transitions in the FSM.

        Every click transition gets a guard via LLM. Back/home/scroll are skipped.

        Returns:
            The same AppFSM with transition.guard fields populated.
        """
        trace_data = json.loads(trace_path.read_text())
        raw_screens = trace_data.get("screens", {})

        skipped = 0
        generated = 0
        null_count = 0
        failed = 0

        for transition in self._fsm.transitions:
            action_type = transition.action.get("type", "")

            # Only skip back/home/scroll — everything else gets a guard
            if action_type in _SKIP_ACTIONS:
                transition.guard = None
                skipped += 1
                continue

            source_state = self._fsm.states.get(transition.source)
            target_state = self._fsm.states.get(transition.target)
            if not source_state or not target_state:
                failed += 1
                continue

            source_elements = self._get_elements(source_state, raw_screens)
            target_elements = self._get_elements(target_state, raw_screens)

            # Compute diff between source and target elements
            diff_summary = self._compute_diff(source_elements, target_elements)

            # Collect contrastive examples from sibling transitions
            extra_context = self._collect_sibling_transitions(transition, source_state, raw_screens)

            # Resolve screenshots
            source_ss = self._resolve_screenshot(source_state, trace_data) if use_images else None
            target_ss = self._resolve_screenshot(target_state, trace_data) if use_images else None

            try:
                guard = self.generate_guard(
                    transition,
                    source_state,
                    target_state,
                    source_elements,
                    target_elements,
                    source_ss,
                    target_ss,
                    diff_summary=diff_summary,
                    extra_context=extra_context,
                )
            except Exception:
                logger.exception(f"LLM error for {transition.source}→{transition.target}, skipping")
                failed += 1
                continue

            transition.guard = guard
            if guard is None:
                null_count += 1
            else:
                generated += 1

        logger.info(
            f"Guard generation: {generated} generated, "
            f"{null_count} null, {skipped} skipped, {failed} failed"
        )
        return self._fsm

    def generate_guard(
        self,
        transition: Transition,
        source_state: AbstractState,
        target_state: AbstractState,
        source_elements: list[dict[str, Any]],
        target_elements: list[dict[str, Any]],
        source_screenshot: Path | None = None,
        target_screenshot: Path | None = None,
        diff_summary: str = "",
        extra_context: str = "",
    ) -> str | None:
        """Generate a single guard for one transition.

        Validation pipeline:
        1. Auto-fix quoted identifiers (common LLM mistake)
        2. Lark syntax validation
        3. Reject ephemeral element IDs (e_XXXX)
        4. Verify referenced elements exist in provided element lists

        Returns:
            Guard expression string, or None if no guard needed.
        """
        # Build element reference table with stable aliases
        icon_labels = None
        if source_state.semantic_profile:
            icon_labels = source_state.semantic_profile.icon_labels or None
        source_with_aliases = self._build_element_reference_table(
            source_elements, icon_labels=icon_labels
        )
        target_with_aliases = self._build_element_reference_table(target_elements)

        system_prompt, user_prompt = self._build_prompt(
            transition,
            source_state,
            target_state,
            source_with_aliases,
            target_with_aliases,
            diff_summary=diff_summary,
            extra_context=extra_context,
        )

        valid_ids = self._collect_valid_element_ids(source_with_aliases, target_with_aliases)

        max_retries = 2
        for attempt in range(max_retries + 1):
            if source_screenshot and target_screenshot:
                response = self._llm.generate_with_images(
                    system_prompt,
                    user_prompt,
                    images=[source_screenshot, target_screenshot],
                    image_labels=["Source state screenshot:", "Target state screenshot:"],
                )
            else:
                response = self._llm.generate(system_prompt, user_prompt)

            response = self._clean_response(response)

            if response.lower() == "null" or response == "":
                return None

            # Step 1: Auto-fix quoted identifiers
            response = self._strip_quoted_identifiers(response)

            # Step 2: Lark syntax validation
            if not self._validate_guard(response):
                logger.warning(
                    f"Guard syntax invalid (attempt {attempt + 1}/{max_retries + 1}): {response}"
                )
                if attempt < max_retries:
                    user_prompt += (
                        f"\n\nYour previous response '{response}' had a syntax error. "
                        "Try again, strictly following the grammar."
                    )
                continue

            # Step 3: Reject ephemeral element IDs
            if re.search(r"\be_\d{3,}\b", response):
                logger.warning(
                    f"Guard uses ephemeral element ID "
                    f"(attempt {attempt + 1}/{max_retries + 1}): {response}"
                )
                if attempt < max_retries:
                    user_prompt += (
                        f"\n\nYour previous response '{response}' used an ephemeral "
                        "element ID (e_XXXX). Use the alias from the element "
                        "table instead."
                    )
                continue

            # Step 4: Verify referenced elements exist
            refs = self._extract_element_references(response)
            invalid = [r for r in refs if r not in valid_ids]
            if invalid:
                logger.warning(
                    f"Guard references unknown elements {invalid} "
                    f"(attempt {attempt + 1}/{max_retries + 1}): {response}"
                )
                if attempt < max_retries:
                    user_prompt += (
                        f"\n\nYour previous response '{response}' referenced elements "
                        f"not in the provided list: {invalid}. Use only exact "
                        "aliases from the element tables above."
                    )
                continue

            return response

        logger.error(f"Failed to generate valid guard for {transition.source}→{transition.target}")
        return None

    # ------------------------------------------------------------------
    # Diff and sibling context
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_diff(
        source_elements: list[dict[str, Any]],
        target_elements: list[dict[str, Any]],
    ) -> str:
        """Compute human-readable diff between source and target element states.

        Compares elements by resource_id, reports changes in text, is_checked,
        is_enabled, and structural additions/removals.
        """
        source_by_rid: dict[str, dict[str, Any]] = {}
        for el in source_elements:
            rid = el.get("resource_id")
            if rid:
                source_by_rid[rid] = el

        target_by_rid: dict[str, dict[str, Any]] = {}
        for el in target_elements:
            rid = el.get("resource_id")
            if rid:
                target_by_rid[rid] = el

        changes: list[str] = []
        tracked_props = ("text", "is_checked", "is_enabled")

        for rid, src_el in source_by_rid.items():
            tgt_el = target_by_rid.get(rid)
            if tgt_el is None:
                changes.append(f'- Element "{rid}": removed in target state')
                continue
            for prop in tracked_props:
                src_val = src_el.get(prop)
                tgt_val = tgt_el.get(prop)
                if src_val != tgt_val:
                    changes.append(
                        f'- Element "{rid}": {prop} changed from {src_val!r} to {tgt_val!r}'
                    )

        for rid in target_by_rid:
            if rid not in source_by_rid:
                changes.append(f'- Element "{rid}": new in target state')

        return "\n".join(changes) if changes else "(no significant changes)"

    def _collect_sibling_transitions(
        self,
        current_transition: Transition,
        source_state: AbstractState,
        raw_screens: dict[str, Any],
    ) -> str:
        """Find other click transitions from the same source state.

        Provides contrastive context — showing what OTHER items could be clicked
        helps the LLM understand this is a list/menu and generate parameterized guards.

        Returns:
            Formatted string with sibling examples, or empty string.
        """
        siblings = [
            t
            for t in self._fsm.transitions
            if t.source == current_transition.source
            and t.action.get("type") == "click"
            and t is not current_transition
        ]
        if not siblings:
            return ""

        source_elements = self._get_elements(source_state, raw_screens)
        lines = ["## Other click targets from the same screen:"]
        for sib in siblings[:5]:
            target_state = self._fsm.states.get(sib.target)
            target_name = target_state.name if target_state else sib.target
            target_eid = sib.action.get("target", "")
            clicked_text = ""
            for el in source_elements:
                if el.get("element_id") == target_eid:
                    clicked_text = el.get("text", "") or ""
                    break
            lines.append(f'- Click on "{clicked_text}" → {target_name}')

        return "\n".join(lines) + "\n\n"

    # ------------------------------------------------------------------
    # Element reference table
    # ------------------------------------------------------------------

    @staticmethod
    def _build_element_reference_table(
        elements: list[dict[str, Any]],
        icon_labels: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Assign stable aliases to elements for guard references.

        Priority:
        1. Icon label from semantic profile (e.g., "delete_button")
        2. resource_id if available (e.g., "com.android.settings:id/switchWidget")
        3. Synthesized alias from class + position: e.g., "Switch_0", "EditText_0"
        """
        class_counts: dict[str, int] = {}
        result = []
        for el in elements:
            eid = el.get("element_id", "")
            resource_id = el.get("resource_id") or ""
            class_name = el.get("class_name", "")
            short_class = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name

            if icon_labels and eid in icon_labels:
                alias = icon_labels[eid]
            elif resource_id:
                alias = resource_id
            elif short_class:
                idx = class_counts.get(short_class, 0)
                class_counts[short_class] = idx + 1
                alias = f"{short_class}_{idx}"
            else:
                alias = eid

            result.append({**el, "_alias": alias})
        return result

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        transition: Transition,
        source_state: AbstractState,
        target_state: AbstractState,
        source_elements: list[dict[str, Any]],
        target_elements: list[dict[str, Any]],
        diff_summary: str = "",
        extra_context: str = "",
    ) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for LLM call."""
        action = transition.action
        target_eid = action.get("target", "")

        target_alias = ""
        target_text = action.get("target_text", "")
        for el in source_elements:
            if el.get("element_id") == target_eid:
                target_alias = el.get("_alias", el.get("resource_id", "")) or ""
                if not target_text:
                    target_text = el.get("text", "") or ""
                break

        # Semantic profile context
        source_description = ""
        source_page_function = ""
        source_expected_actions = ""
        target_description = ""

        if source_state.semantic_profile:
            sp = source_state.semantic_profile
            if sp.alt_text:
                source_description = f"Description: {sp.alt_text}\n"
            if sp.page_function:
                source_page_function = f"Page function: {sp.page_function}\n"
            if sp.expected_actions:
                source_expected_actions = f"Expected actions: {', '.join(sp.expected_actions)}\n"

        if target_state.semantic_profile and target_state.semantic_profile.alt_text:
            target_description = f"Description: {target_state.semantic_profile.alt_text}\n"

        # Widget template hint
        widget_hint = ""
        target_class = action.get("target_class", "")
        if target_class:
            resolved_class, template = self._resolve_widget_type(
                target_class, action.get("target_resource_id", "")
            )
            if template:
                short_name = resolved_class.rsplit(".", 1)[-1]
                parts = [f"## Widget type: {short_name}"]
                if resolved_class != target_class:
                    parts.append(f"(resolved from layout XML; runtime class was {target_class})")
                if template.get("safety"):
                    parts.append(f"Suggested safety: {template['safety']}")
                if template.get("correctness"):
                    parts.append(f"Suggested correctness: {template['correctness']}")
                widget_hint = "\n".join(parts) + "\n\n"

        closed_set_hint = self._get_closed_set_hint(target_text)

        system_prompt = _SYSTEM_PROMPT.format(grammar=self._grammar_text)
        user_prompt = _USER_PROMPT.format(
            action_type=action.get("type", ""),
            target_alias=target_alias,
            target_text=target_text,
            source_name=source_state.name,
            target_name=target_state.name,
            source_description=source_description,
            source_page_function=source_page_function,
            source_expected_actions=source_expected_actions,
            target_description=target_description,
            source_table=self._format_elements(source_elements),
            target_table=self._format_elements(target_elements),
            diff_summary=diff_summary or "(no significant changes)",
            widget_hint=widget_hint,
            closed_set_hint=closed_set_hint,
            extra_context=extra_context,
        )
        return system_prompt, user_prompt

    # ------------------------------------------------------------------
    # Widget type resolution + closed-set hints
    # ------------------------------------------------------------------

    _CONTAINER_CLASSES: set[str] = {
        "LinearLayout",
        "RelativeLayout",
        "FrameLayout",
        "ConstraintLayout",
        "CoordinatorLayout",
        "CardView",
    }

    def _resolve_widget_type(
        self, target_class: str, target_resource_id: str
    ) -> tuple[str, dict[str, str | None] | None]:
        """Resolve widget type with layout XML fallback for containers."""
        from vigil.core.platform_priors import get_guard_template

        template = get_guard_template(target_class)
        if template is not None:
            return target_class, template

        if self._app_prior is None:
            return target_class, None

        short_class = target_class.rsplit(".", 1)[-1] if "." in target_class else target_class
        if short_class not in self._CONTAINER_CLASSES:
            return target_class, None

        if target_resource_id:
            clean_rid = (
                target_resource_id.split("/")[-1]
                if "/" in target_resource_id
                else target_resource_id
            )
            for decl in self._app_prior.widget_declarations:
                if decl.widget_id == clean_rid:
                    t = get_guard_template(decl.widget_class)
                    if t is not None:
                        logger.debug(
                            f"Layout XML fallback: {target_class} → "
                            f"{decl.widget_class} from {decl.layout_file}"
                        )
                        return decl.widget_class, t

        return target_class, None

    def _get_closed_set_hint(self, target_text: str) -> str:
        """Check if target_text matches a string_array, providing value domain."""
        if not self._app_prior or not self._app_prior.string_arrays:
            return ""

        for array_name, values in self._app_prior.string_arrays.items():
            if target_text in values:
                return (
                    f"## Closed-set constraint (from strings.xml)\n"
                    f"The value must be one of: {values}\n"
                    f"(source: array '{array_name}')\n\n"
                )

        return ""

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_guard(self, guard_expr: str) -> bool:
        """Parse guard with Lark to verify syntactic correctness."""
        try:
            self._parser.parse(guard_expr)
            return True
        except Exception:
            return False

    @staticmethod
    def _strip_quoted_identifiers(guard: str) -> str:
        """Strip quotes around element/property names in predicates.

        Transforms read("foo", "bar") → read(foo, bar) etc.
        """

        def _unquote_pred(m: re.Match[str]) -> str:
            name = m.group(1)
            args = m.group(2)
            parts = []
            for part in args.split(","):
                stripped = part.strip()
                if (
                    stripped.startswith('"')
                    and stripped.endswith('"')
                    and not stripped[1:].startswith("$")
                ):
                    inner = stripped[1:-1]
                    if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.:\/]*", inner):
                        parts.append(f" {inner}")
                        continue
                parts.append(part)
            return f"{name}({','.join(parts).strip()})"

        return re.sub(
            r"(read|value|contains|count|action)\(([^)]+)\)",
            _unquote_pred,
            guard,
        )

    @staticmethod
    def _extract_element_references(guard: str) -> list[str]:
        """Extract element names referenced in predicates.

        Pulls the first argument from read(), value(), contains(), count().
        Skips action() since its argument is a property name, not an element.
        """
        refs: list[str] = []
        for m in re.finditer(r"(?:read|value|contains|count)\(\s*([^,)]+)", guard):
            ref = m.group(1).strip()
            if ref.startswith("$"):
                continue
            refs.append(ref)
        return refs

    @staticmethod
    def _collect_valid_element_ids(
        source_elements: list[dict[str, Any]],
        target_elements: list[dict[str, Any]],
    ) -> set[str]:
        """Collect all valid element identifiers including synthesized aliases."""
        ids: set[str] = set()
        for el in [*source_elements, *target_elements]:
            rid = el.get("resource_id")
            if rid:
                ids.add(rid)
            eid = el.get("element_id")
            if eid:
                ids.add(eid)
            alias = el.get("_alias")
            if alias:
                ids.add(alias)
        return ids

    @staticmethod
    def _clean_response(response: str) -> str:
        """Clean LLM response: strip whitespace, code fences, quotes."""
        text = response.strip()
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        for line in text.split("\n"):
            line = line.strip()
            if line:
                text = line
                break
        text = text.strip("\"'`")
        return text

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _resolve_screenshot(self, state: AbstractState, trace_data: dict[str, Any]) -> Path | None:
        """Find the screenshot file for a state's first raw_screen."""
        if not state.raw_screens:
            return None
        screen_id = state.raw_screens[0]
        screens = trace_data.get("screens", {})
        screen = screens.get(screen_id, {})
        rel_path = screen.get("screenshot_path")
        if not rel_path:
            return None
        path = Path(rel_path)
        if path.exists():
            return path
        return None

    def _get_elements(
        self, state: AbstractState, raw_screens: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Get element list for a state from trace data.

        If elements aren't stored inline in the trace JSON, falls back to
        parsing the XML tree file (xml_tree_path) via ui_parser.
        """
        if not state.raw_screens:
            return []
        screen_id = state.raw_screens[0]
        screen = raw_screens.get(screen_id, {})
        elements = screen.get("elements", [])

        # Fallback: parse XML tree if no inline elements
        if not elements:
            xml_path_str = screen.get("xml_tree_path")
            if xml_path_str:
                xml_path = Path(xml_path_str)
                if xml_path.exists():
                    from vigil.core.ui_parser import parse_hierarchy_xml

                    app_package = screen.get("package_name")
                    xml_text = xml_path.read_text(encoding="utf-8")
                    ui_elements = parse_hierarchy_xml(xml_text, app_package)
                    elements = [
                        {
                            "element_id": e.element_id,
                            "class_name": e.class_name or "",
                            "resource_id": e.resource_id or "",
                            "text": e.text or "",
                            "content_description": e.content_description or "",
                            "is_clickable": e.is_clickable,
                            "is_long_clickable": e.is_long_clickable,
                            "is_scrollable": e.is_scrollable,
                            "is_editable": e.is_editable,
                            "is_checkable": e.is_checkable,
                            "is_checked": e.is_checked,
                            "is_enabled": e.is_enabled,
                            "is_focusable": e.is_focusable,
                            "is_focused": e.is_focused,
                            "is_selected": e.is_selected,
                            "is_password": e.is_password,
                            "bounds": e.bounds,
                        }
                        for e in ui_elements
                    ]

        interactable = [
            e
            for e in elements
            if e.get("is_clickable")
            or e.get("is_scrollable")
            or e.get("is_editable")
            or e.get("is_checkable")
        ]
        return interactable[:_MAX_ELEMENTS_PER_SCREEN]

    @staticmethod
    def _format_elements(elements: list[dict[str, Any]]) -> str:
        """Format element list as a compact table with aliases.

        Columns mirror the property keys exposed by the runtime
        ``DecisionEngine._build_screen_context`` so the LLM only generates
        guards over properties that exist at evaluation time.
        """
        if not elements:
            return "(no interactable elements)"

        prop_cols: list[tuple[str, str]] = [
            ("Clickable", "is_clickable"),
            ("LongClickable", "is_long_clickable"),
            ("Checkable", "is_checkable"),
            ("Checked", "is_checked"),
            ("Editable", "is_editable"),
            ("Enabled", "is_enabled"),
            ("Scrollable", "is_scrollable"),
            ("Focused", "is_focused"),
            ("Selected", "is_selected"),
            ("Password", "is_password"),
        ]
        # Drop columns that are uniformly False across the screen — saves tokens
        # without hiding signal the LLM could act on.
        active_cols = [
            (label, key) for label, key in prop_cols if any(el.get(key) for el in elements)
        ]

        header = (
            "| Alias (use in guards) | Class | Text | "
            + " | ".join(label for label, _ in active_cols)
            + " |"
        )
        lines = [header]
        for el in elements:
            alias = el.get("_alias", el.get("resource_id", el.get("element_id", "")))
            cls = el.get("class_name", "")
            if cls and "." in cls:
                cls = cls.rsplit(".", maxsplit=1)[-1]
            text = (el.get("text", "") or "")[:30]
            row_vals = [str(el.get(key, False)) for _, key in active_cols]
            lines.append(f"| {alias} | {cls} | {text} | " + " | ".join(row_vals) + " |")
        return "\n".join(lines)
