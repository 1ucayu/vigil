"""Stage 4: DSL Semantic Guard Generation.

Annotates FSM transitions with guard conditions using a constrained formal grammar
(docs/dsl_grammar.lark). Uses LLM + multimodal input (screenshots + element tables)
to generate syntactically correct guards.

Transitions are classified into categories (content_selection, state_mutation,
structural_nav, etc.) to determine guard generation strategy — only semantically
meaningful transitions (content selection, state mutation) trigger LLM calls.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from lark import Lark
from loguru import logger

from vigil.core.config import VigilConfig
from vigil.core.llm_client import LlmClient
from vigil.models.fsm import AbstractState, AppFSM, ContainerType, Transition

_GRAMMAR_PATH = Path(__file__).parent.parent.parent.parent / "docs" / "dsl_grammar.lark"

_MAX_ELEMENTS_PER_SCREEN = 30

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a formal verification expert generating DSL guard expressions \
for a mobile app's FSM transitions.

A guard is a precondition that must be true before a UI action is allowed. \
Guards are written in this grammar:

{grammar}

Available predicates:
- read(element, property) op value — check a UI element's property
- value(element) op value — shorthand for read(element, value)
- action(property) op value — check the proposed action's metadata
- contains(element, value) — check if element contains a value
- count(element) op value — check child count of element
- time_in(HH:MM, HH:MM) — time range check
- in_state(name) — FSM state check

CRITICAL RULES:
1. For element references, use the Alias from the element table. This is the \
resource_id (e.g., com.android.settings:id/switchWidget) or a synthesized alias \
(e.g., Switch_0, EditText_0). Do NOT invent descriptive names.
2. NEVER use element IDs like e_0000, e_0037, e_0150 — these are session-specific \
parsing artifacts that change every capture.
3. Element names and property names are bare identifiers — NEVER wrap them in quotes.

Use $intent.variable_name for values only known at runtime.

Respond with ONLY the guard expression or the word "null" (no guard needed). \
No explanation, no markdown, no quotes around the expression."""

_USER_PROMPT_TEMPLATE = """\
## Transition
Action: {action_type} on element {target_alias}
Text: "{target_text}"
From state: {source_name} → To state: {target_name}

## Source State Elements
{source_table}

## Target State Elements
{target_table}

## Guidelines
- Simple menu navigation (clicking a list item to open a page) → null
- Toggle/switch/checkbox → read(element_alias, is_checked) == false
- Selecting a specific item from a dynamic list → action(target_text) == $intent.item_name
- Confirmation/save buttons → null (or check required fields if visible)
- Scrolling, back, home → null

Your guard expression (or "null"):"""

_CONTENT_SELECTION_SYSTEM_PROMPT = """\
You are a formal verification expert generating DSL guard expressions \
for a mobile app's FSM transitions.

Guards are written in this grammar:

{grammar}

This transition clicks an item from a dynamic content list (e.g., WiFi networks, \
Bluetooth devices, apps). The user's intent determines WHICH item to click.

Generate a guard using: action(target_text) == $intent.<descriptive_variable_name>

Choose a descriptive variable name based on context:
- WiFi network → $intent.wifi_name
- Bluetooth device → $intent.device_name
- App name → $intent.app_name
- Generic → $intent.selected_item

CRITICAL: You MUST return a guard expression. Do NOT return "null".
Respond with ONLY the guard expression. No explanation, no markdown, no quotes."""

_CONTENT_SELECTION_USER_PROMPT = """\
## Transition
Action: {action_type} on "{target_text}" (alias: {target_alias})
From state: {source_name} → To state: {target_name}

## Source State Elements (dynamic content list)
{source_table}

Generate an action(target_text) == $intent.<variable_name> guard.
Your guard expression:"""

_STATE_MUTATION_SYSTEM_PROMPT = """\
You are a formal verification expert generating DSL guard expressions \
for a mobile app's FSM transitions.

Guards are written in this grammar:

{grammar}

This action changes a UI state (toggle, checkbox, text input, confirmation).
Generate a guard that checks the PRECONDITION — what must be true BEFORE this action.

Common patterns:
- Toggle on → read(<alias>, is_checked) == false
- Toggle off → read(<alias>, is_checked) == true
- Confirm after text input → value(<alias>) == $intent.<variable>
- Checkbox → read(<alias>, is_checked) == false
- Dialog confirmation (Pair, Delete, OK/Cancel) → usually no precondition → "null"

Use the element aliases from the provided table. These are either resource_ids \
(e.g., com.android.settings:id/switchWidget) or synthesized aliases \
(e.g., Switch_0, EditText_0).

If unsure whether a precondition applies, return "null". \
A missing guard is safer than a wrong guard.

Respond with ONLY the guard expression or "null". \
No explanation, no markdown, no quotes."""

_STATE_MUTATION_USER_PROMPT = """\
## Transition
Action: {action_type} on element "{target_text}" (alias: {target_alias})
From state: {source_name} → To state: {target_name}
Element class: {target_class}
Checkable: {is_checkable} | Checked: {is_checked}

## Source State Elements
{source_table}

Generate a precondition guard for this state-changing action.
Your guard expression:"""


# ---------------------------------------------------------------------------
# Transition classification
# ---------------------------------------------------------------------------


class TransitionCategory(StrEnum):
    """Classification of FSM transitions for guard generation strategy."""

    CONTENT_SELECTION = "content_selection"
    STATE_MUTATION = "state_mutation"
    STRUCTURAL_NAVIGATION = "structural_nav"
    BACK_NAVIGATION = "back_navigation"
    SCROLL = "scroll"


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


class DslGenerator:
    """Generate DSL guard templates for FSM transitions using LLM + screenshots."""

    def __init__(self, fsm: AppFSM, config: VigilConfig) -> None:
        self._fsm = fsm
        self._config = config
        self._llm = LlmClient(config.llm)
        grammar_path = _GRAMMAR_PATH
        self._grammar_text = grammar_path.read_text()
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

        Classifies each transition, then generates guards only for
        CONTENT_SELECTION and STATE_MUTATION transitions.

        Returns:
            The same AppFSM with transition.guard fields populated.
        """
        trace_data = json.loads(trace_path.read_text())
        raw_screens = trace_data.get("screens", {})

        skipped = 0
        generated = 0
        null_count = 0
        failed = 0
        cat_counts: dict[str, int] = {}

        for transition in self._fsm.transitions:
            source_state = self._fsm.states.get(transition.source)
            target_state = self._fsm.states.get(transition.target)
            if not source_state or not target_state:
                failed += 1
                continue

            source_elements = self._get_elements(source_state, raw_screens)
            category = self._classify_transition(
                transition, source_state, target_state, source_elements
            )
            cat_counts[category] = cat_counts.get(category, 0) + 1
            action_type = transition.action.get("type", "")
            logger.debug(
                f"  {source_state.name} -> {target_state.name} [{action_type}]: {category.value}"
            )

            if category in (
                TransitionCategory.BACK_NAVIGATION,
                TransitionCategory.SCROLL,
                TransitionCategory.STRUCTURAL_NAVIGATION,
            ):
                transition.guard = None
                skipped += 1
                continue

            target_elements = self._get_elements(target_state, raw_screens)
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
                    category=category,
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
        logger.info(f"Classification: {cat_counts}")
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
        category: TransitionCategory | None = None,
    ) -> str | None:
        """Generate a single guard for one transition.

        Args:
            category: If provided, uses category-specific prompts.
                CONTENT_SELECTION forces a fallback guard if LLM returns null.

        Validation pipeline:
        1. Auto-fix quoted identifiers (common LLM mistake)
        2. Lark syntax validation
        3. Reject ephemeral element IDs (e_XXXX)
        4. Verify referenced elements exist in provided element lists

        Returns:
            Guard expression string, or None if no guard needed.
        """
        # Build element reference table with stable aliases
        source_with_aliases = self._build_element_reference_table(source_elements)
        target_with_aliases = self._build_element_reference_table(target_elements)

        # Build prompts based on category
        if category == TransitionCategory.CONTENT_SELECTION:
            system_prompt, user_prompt = self._build_content_selection_prompt(
                transition,
                source_state,
                target_state,
                source_with_aliases,
                target_with_aliases,
            )
        elif category == TransitionCategory.STATE_MUTATION:
            system_prompt, user_prompt = self._build_state_mutation_prompt(
                transition,
                source_state,
                target_state,
                source_with_aliases,
                target_with_aliases,
            )
        else:
            system_prompt, user_prompt = self._build_prompt(
                transition,
                source_state,
                target_state,
                source_with_aliases,
                target_with_aliases,
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
                if category == TransitionCategory.CONTENT_SELECTION:
                    return "action(target_text) == $intent.selected_item"
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
                    f"Guard uses ephemeral element ID (attempt {attempt + 1}/{max_retries + 1})"
                    f": {response}"
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

        # All retries failed — fallback for content selection
        if category == TransitionCategory.CONTENT_SELECTION:
            return "action(target_text) == $intent.selected_item"
        logger.error(f"Failed to generate valid guard for {transition.source}→{transition.target}")
        return None

    # ------------------------------------------------------------------
    # Transition classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_transition(
        transition: Transition,
        source_state: AbstractState,
        target_state: AbstractState,
        source_elements: list[dict[str, Any]],
    ) -> TransitionCategory:
        """Classify a transition to determine guard generation strategy.

        Priority order:
            1. Action type shortcuts (back/home, scroll)
            2. Find target element
            3. Back-arrow buttons (small, top area, no text)
            4. Checkable elements -> state mutation
            5. Dialog confirmation states
            6. Confirmation buttons near input fields
            7. Content selection from CONTENT containers
            8. Fallback heuristic for unclassified states (homogeneous siblings)
            9. Default: structural navigation

        Args:
            transition: The FSM transition to classify.
            source_state: The source abstract state.
            target_state: The target abstract state.
            source_elements: Interactable elements from the source screen.

        Returns:
            The classified TransitionCategory.
        """
        action_type = transition.action.get("type", "")

        # Rule 1: Action type shortcuts
        if action_type in ("navigate_back", "navigate_home"):
            return TransitionCategory.BACK_NAVIGATION
        if action_type in ("scroll_up", "scroll_down"):
            return TransitionCategory.SCROLL

        # Rule 2: Find target element
        target_el = DslGenerator._find_target_element(transition, source_elements)

        # Rule 3: Back-arrow buttons (small, top area, no text)
        if target_el and DslGenerator._is_back_button(target_el):
            return TransitionCategory.BACK_NAVIGATION

        # Rule 4: Checkable elements -> state mutation
        if target_el and target_el.get("is_checkable"):
            return TransitionCategory.STATE_MUTATION

        # Rule 5: Dialog confirmation states
        if DslGenerator._is_dialog_state(source_state, source_elements):
            return TransitionCategory.STATE_MUTATION

        # Rule 6: Confirmation buttons near input fields
        has_input = any(
            e.get("is_editable") or "EditText" in e.get("class_name", "") for e in source_elements
        )
        if has_input and target_el:
            cls = target_el.get("class_name", "")
            if "Button" in cls or "TextView" in cls:
                return TransitionCategory.STATE_MUTATION

        # Rule 7: Content selection -- from CONTENT containers
        if (
            source_state.container_type == ContainerType.CONTENT
            and DslGenerator._is_element_in_content_area(target_el, source_elements, source_state)
        ):
            return TransitionCategory.CONTENT_SELECTION

        # Rule 8: Fallback heuristic for unclassified states
        if (
            source_state.container_type == ContainerType.NONE
            and target_el
            and DslGenerator._has_homogeneous_siblings(target_el, source_elements, min_count=5)
        ):
            return TransitionCategory.CONTENT_SELECTION

        # Rule 9: Default
        return TransitionCategory.STRUCTURAL_NAVIGATION

    @staticmethod
    def _find_target_element(
        transition: Transition, source_elements: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Find the element targeted by this transition's action.

        Args:
            transition: The transition whose action target to look up.
            source_elements: Elements from the source screen.

        Returns:
            The matching element dict, or None if not found.
        """
        target_id = transition.action.get("target")
        if not target_id:
            return None
        for el in source_elements:
            if el.get("element_id") == target_id:
                return el
        return None

    @staticmethod
    def _is_back_button(element: dict[str, Any]) -> bool:
        """Detect back-arrow buttons by size, position, and absence of text.

        Heuristic: small icon (< 200x200), positioned in the top-left corner
        (y < 300, x < 300), with no meaningful text label.

        Args:
            element: The element dict to check.

        Returns:
            True if the element looks like a back/navigate-up button.
        """
        bounds = element.get("bounds", [])
        if not bounds or len(bounds) != 4:
            return False
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        text = element.get("text") or element.get("content_description") or ""
        is_small = width < 200 and height < 200
        is_top = top < 300
        is_left = left < 300
        has_no_text = len(text.strip()) == 0 or text.strip().lower() in (
            "back",
            "navigate up",
        )
        return is_small and is_top and is_left and has_no_text

    @staticmethod
    def _is_dialog_state(state: AbstractState, elements: list[dict[str, Any]]) -> bool:
        """Detect dialog/confirmation states by name keywords or button patterns.

        A state is classified as a dialog if:
        - Its name contains dialog-related keywords (e.g., "?", "confirm", "delete"), OR
        - It has few interactable elements (<=5) whose text matches confirm/cancel patterns.

        Args:
            state: The abstract state to check.
            elements: Interactable elements from this state.

        Returns:
            True if the state looks like a dialog or confirmation prompt.
        """
        name = state.name.lower()
        dialog_keywords = [
            "?",
            "confirm",
            "delete",
            "unblock",
            "remove",
            "pair with",
            "discard",
            "cancel",
            "are you sure",
            "warning",
        ]
        if any(kw in name for kw in dialog_keywords):
            return True

        interactable = [e for e in elements if e.get("is_clickable") or e.get("is_checkable")]
        if len(interactable) <= 5:
            button_texts: list[str] = []
            for e in interactable:
                text = (e.get("text") or "").lower()
                if text:
                    button_texts.append(text)
            confirm_words = {
                "ok",
                "cancel",
                "yes",
                "no",
                "confirm",
                "pair",
                "unpair",
                "unblock",
                "block",
                "delete",
                "remove",
                "save",
                "discard",
                "accept",
                "deny",
                "allow",
            }
            if any(word in " ".join(button_texts) for word in confirm_words):
                return True
        return False

    @staticmethod
    def _is_element_in_content_area(
        target_el: dict[str, Any] | None,
        source_elements: list[dict[str, Any]],
        source_state: AbstractState,
    ) -> bool:
        """Check whether an element falls within the content (non-toolbar) area.

        Uses the container_resource_id depth comparison when available,
        otherwise falls back to a y-coordinate heuristic (top < 300 is toolbar).

        Args:
            target_el: The element to check.
            source_elements: All elements from the source screen.
            source_state: The source abstract state (for container metadata).

        Returns:
            True if the element is in the content area.
        """
        if target_el is None:
            return False
        container_rid = source_state.container_resource_id
        if container_rid:
            container_el = None
            for e in source_elements:
                if e.get("resource_id") == container_rid:
                    container_el = e
                    break
            if container_el:
                container_depth = container_el.get("depth", 0)
                target_depth = target_el.get("depth", 0)
                return target_depth > container_depth
        bounds = target_el.get("bounds", [])
        return not (bounds and len(bounds) == 4 and bounds[1] < 300)

    @staticmethod
    def _has_homogeneous_siblings(
        target_el: dict[str, Any],
        source_elements: list[dict[str, Any]],
        min_count: int = 5,
    ) -> bool:
        """Check whether the target has enough same-class, same-depth clickable siblings.

        Used as a fallback heuristic when container_type is NONE: if the element
        is surrounded by many structurally identical siblings, it is likely a
        dynamic content list rather than a fixed menu.

        Args:
            target_el: The element to check siblings for.
            source_elements: All elements from the source screen.
            min_count: Minimum number of siblings required (default 5).

        Returns:
            True if at least ``min_count`` same-class, same-depth clickable elements exist.
        """
        target_class = target_el.get("class_name", "")
        target_depth = target_el.get("depth", -1)
        if not target_class or target_depth < 0:
            return False
        siblings = [
            e
            for e in source_elements
            if e.get("class_name") == target_class
            and e.get("is_clickable")
            and e.get("depth") == target_depth
        ]
        return len(siblings) >= min_count

    # ------------------------------------------------------------------
    # Element reference table
    # ------------------------------------------------------------------

    @staticmethod
    def _build_element_reference_table(
        elements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Assign stable aliases to elements for guard references.

        Priority:
        1. resource_id if available (e.g., "com.android.settings:id/switchWidget")
        2. Synthesized alias from class + position: e.g., "Switch_0", "EditText_0"
        """
        class_counts: dict[str, int] = {}
        result = []
        for el in elements:
            resource_id = el.get("resource_id") or ""
            class_name = el.get("class_name", "")
            short_class = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name

            if resource_id:
                alias = resource_id
            elif short_class:
                idx = class_counts.get(short_class, 0)
                class_counts[short_class] = idx + 1
                alias = f"{short_class}_{idx}"
            else:
                alias = el.get("element_id", "")

            result.append({**el, "_alias": alias})
        return result

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        transition: Transition,
        source_state: AbstractState,
        target_state: AbstractState,
        source_elements: list[dict[str, Any]],
        target_elements: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Build generic (system_prompt, user_prompt) for LLM call."""
        action = transition.action
        target_eid = action.get("target", "")

        target_alias = ""
        target_text = ""
        for el in source_elements:
            if el.get("element_id") == target_eid:
                target_alias = el.get("_alias", el.get("resource_id", "")) or ""
                target_text = el.get("text", "") or ""
                break

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(grammar=self._grammar_text)
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            action_type=action.get("type", ""),
            target_alias=target_alias,
            target_text=target_text,
            source_name=source_state.name,
            target_name=target_state.name,
            source_table=self._format_elements(source_elements),
            target_table=self._format_elements(target_elements),
        )
        return system_prompt, user_prompt

    def _build_content_selection_prompt(
        self,
        transition: Transition,
        source_state: AbstractState,
        target_state: AbstractState,
        source_elements: list[dict[str, Any]],
        _target_elements: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Build prompts for content selection transitions."""
        action = transition.action
        target_eid = action.get("target", "")

        target_alias = ""
        target_text = ""
        for el in source_elements:
            if el.get("element_id") == target_eid:
                target_alias = el.get("_alias", "") or ""
                target_text = el.get("text", "") or ""
                break

        system_prompt = _CONTENT_SELECTION_SYSTEM_PROMPT.format(grammar=self._grammar_text)
        user_prompt = _CONTENT_SELECTION_USER_PROMPT.format(
            action_type=action.get("type", ""),
            target_alias=target_alias,
            target_text=target_text,
            source_name=source_state.name,
            target_name=target_state.name,
            source_table=self._format_elements(source_elements),
        )
        return system_prompt, user_prompt

    def _build_state_mutation_prompt(
        self,
        transition: Transition,
        source_state: AbstractState,
        target_state: AbstractState,
        source_elements: list[dict[str, Any]],
        _target_elements: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Build prompts for state mutation transitions."""
        action = transition.action
        target_eid = action.get("target", "")

        target_alias = ""
        target_text = ""
        target_class = ""
        is_checkable = False
        is_checked = False
        for el in source_elements:
            if el.get("element_id") == target_eid:
                target_alias = el.get("_alias", "") or ""
                target_text = el.get("text", "") or ""
                cls = el.get("class_name", "")
                target_class = cls.rsplit(".", 1)[-1] if "." in cls else cls
                is_checkable = el.get("is_checkable", False)
                is_checked = el.get("is_checked", False)
                break

        system_prompt = _STATE_MUTATION_SYSTEM_PROMPT.format(grammar=self._grammar_text)
        user_prompt = _STATE_MUTATION_USER_PROMPT.format(
            action_type=action.get("type", ""),
            target_alias=target_alias,
            target_text=target_text,
            source_name=source_state.name,
            target_name=target_state.name,
            target_class=target_class,
            is_checkable=is_checkable,
            is_checked=is_checked,
            source_table=self._format_elements(source_elements),
        )
        return system_prompt, user_prompt

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
                            "is_scrollable": e.is_scrollable,
                            "is_editable": e.is_editable,
                            "is_checkable": e.is_checkable,
                            "is_checked": e.is_checked,
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
        """Format element list as a compact table with aliases."""
        if not elements:
            return "(no interactable elements)"
        lines = ["| Alias (use in guards) | Class | Text | Clickable | Checkable | Checked |"]
        for el in elements:
            alias = el.get("_alias", el.get("resource_id", el.get("element_id", "")))
            cls = el.get("class_name", "")
            if cls and "." in cls:
                cls = cls.rsplit(".", maxsplit=1)[-1]
            text = (el.get("text", "") or "")[:40]
            clickable = el.get("is_clickable", False)
            chkable = el.get("is_checkable", False)
            chked = el.get("is_checked", False)
            lines.append(f"| {alias} | {cls} | {text} | {clickable} | {chkable} | {chked} |")
        return "\n".join(lines)
