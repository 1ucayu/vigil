"""Variable-guided slot filling from user instructions.

Extracts concrete values for $intent.* placeholders in DSL guards.
This is NOT open-domain NLU — the variable names are a closed set
determined by offline guard generation. The extractor only maps
variable names to values found in the instruction.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from vigil.core.config import VigilConfig
from vigil.core.llm_client import LlmClient
from vigil.models.fsm import AppFSM
from vigil.symbolic.dsl_evaluator import IntentContext

_INTENT_PATTERN = re.compile(r"\$intent\.([a-zA-Z_][a-zA-Z0-9_]*)")

_SYSTEM_PROMPT = """\
You extract specific variable values from user instructions for a mobile app \
verification system.

You will receive:
1. A user instruction (may be in Chinese, English, or mixed)
2. A list of variable names to extract

Return ONLY a JSON object: {"variable_name": "extracted_value", ...}
- Extract the most relevant value from the instruction for each variable
- Variable names are semantic hints (e.g., "wifi_name" means the WiFi \
network name the user wants to connect to)
- If a value cannot be determined from the instruction, use ""
- For boolean-intent variables (contains "enabled", "toggle", "switch"), \
infer "true" or "false" from context ("turn on" → "true", "关掉" → "false")
- Do NOT explain. Do NOT use markdown. Return raw JSON only."""

_USER_PROMPT = """\
Instruction: "{instruction}"
Variables: {variables}"""

_RETRY_PROMPT = """\
Your previous response was not valid JSON. Return ONLY a raw JSON object \
with no markdown, no code fences, no explanation.

Instruction: "{instruction}"
Variables: {variables}"""

_QUOTED_PATTERN = re.compile(r""""([^"]+)"|'([^']+)'|「([^」]+)」|\u201c([^\u201d]+)\u201d""")


_ON_KEYWORDS = frozenset({"turn on", "enable", "open", "activate", "打开", "开启", "启用", "開啟"})
_OFF_KEYWORDS = frozenset(
    {"turn off", "disable", "close", "deactivate", "关闭", "关掉", "禁用", "關閉"}
)


class IntentExtractor:
    """Variable-guided slot filling from user instructions.

    Given guard templates that contain $intent.* placeholders, extracts
    concrete values from the user's natural language instruction.

    This is NOT open-domain NLU. The variable names are a closed set
    determined by offline guard generation. The extractor only maps
    variable names to values found in the instruction.

    Two extraction modes:
    1. LLM-based: sends (instruction, variable_names) to LLM, gets JSON back
    2. Rule-based fallback: regex/heuristic extraction for simple cases

    Args:
        fsm: The app's FSM (used to collect guard variables per state).
        config: Vigil configuration (for LLM settings).
    """

    def __init__(self, fsm: AppFSM, config: VigilConfig | None = None) -> None:
        self._fsm = fsm
        self._llm: LlmClient | None = None
        if config is not None:
            try:
                self._llm = LlmClient(config.llm)
            except Exception:
                logger.warning("LlmClient init failed — LLM extraction disabled")

    def extract(
        self,
        instruction: str,
        current_state_id: str,
        existing_context: IntentContext | None = None,
    ) -> IntentContext:
        """Extract intent variables needed by current state's outgoing guards.

        Workflow:
        1. Collect all $intent.* variable names from outgoing transitions
           of current_state_id (via collect_required_variables)
        2. Filter out variables already present in existing_context
        3. If no new variables needed → return existing_context unchanged (no LLM call)
        4. Call LLM to extract missing variable values from instruction
        5. Merge with existing_context and return

        Args:
            instruction: User's natural language instruction
                (e.g., "帮我连HKU的WiFi", "Turn off Bluetooth").
            current_state_id: Current FSM state ID. Used to determine
                which $intent.* variables are needed.
            existing_context: Previously extracted variables from earlier
                steps in the same task. Avoids redundant LLM calls.

        Returns:
            IntentContext with all extracted variables (merged with existing).
            On LLM failure, returns existing_context or empty IntentContext.
        """
        ctx = existing_context or IntentContext(raw_instruction=instruction)

        required = self.collect_required_variables(self._fsm, current_state_id)
        if not required:
            return ctx

        # Filter out variables already resolved
        missing = required - set(ctx.variables.keys())
        if not missing:
            return ctx

        # Extract via LLM or rules
        if self._llm is not None:
            extracted = self._extract_via_llm(instruction, missing)
        else:
            extracted = self._extract_via_rules(instruction, missing)

        # Merge
        merged_vars = dict(ctx.variables)
        merged_vars.update(extracted)
        return IntentContext(raw_instruction=instruction, variables=merged_vars)

    @staticmethod
    def collect_required_variables(fsm: AppFSM, state_id: str) -> set[str]:
        """Scan outgoing transition guards for $intent.* variable names.

        Parses guard strings with regex to find all $intent.XXX references.
        Returns the set of variable names (without the $intent. prefix).

        Example:
            Guard: "action(target_text) == $intent.wifi_name"
            Returns: {"wifi_name"}

            Guards: "action(target_text) == $intent.target_setting",
                    "read(switch, is_checked) == true"
            Returns: {"target_setting"}  (second guard has no $intent)
        """
        variables: set[str] = set()
        for transition in fsm.transitions:
            if transition.source != state_id:
                continue
            if transition.guard is None:
                continue
            variables.update(_INTENT_PATTERN.findall(transition.guard))
        return variables

    @staticmethod
    def collect_all_variables(fsm: AppFSM) -> dict[str, set[str]]:
        """Collect required variables for ALL states in the FSM.

        Returns:
            {state_id: {var_name, ...}, ...}
            Useful for pre-analysis and debugging.
        """
        result: dict[str, set[str]] = {}
        for state_id in fsm.states:
            variables = IntentExtractor.collect_required_variables(fsm, state_id)
            if variables:
                result[state_id] = variables
        return result

    def _extract_via_llm(
        self,
        instruction: str,
        variable_names: set[str],
    ) -> dict[str, str]:
        """Call LLM to extract variable values from instruction.

        Sends (instruction, variable_names) to LLM, expects JSON back.
        On parse failure, retries once with a correction prompt.

        Returns:
            {variable_name: extracted_value} dict. Empty string for
            variables that couldn't be extracted.
        """
        assert self._llm is not None
        sorted_vars = sorted(variable_names)
        user_msg = _USER_PROMPT.format(instruction=instruction, variables=sorted_vars)

        response = self._llm.generate(_SYSTEM_PROMPT, user_msg)
        result = self._parse_json_response(response)
        if result is not None:
            return {k: str(v) for k, v in result.items() if k in variable_names}

        # Retry with correction prompt
        logger.debug("First LLM extraction response was not valid JSON, retrying")
        retry_msg = _RETRY_PROMPT.format(instruction=instruction, variables=sorted_vars)
        response = self._llm.generate(_SYSTEM_PROMPT, retry_msg)
        result = self._parse_json_response(response)
        if result is not None:
            return {k: str(v) for k, v in result.items() if k in variable_names}

        logger.warning("LLM extraction failed after retry")
        return {}

    @staticmethod
    def _extract_via_rules(
        instruction: str,
        variable_names: set[str],
    ) -> dict[str, str]:
        """Rule-based fallback extraction for simple cases.

        Heuristics:
        - If instruction contains quoted strings, map them to variables
          that semantically match
        - For boolean-ish variables (e.g., target_enabled), check for
          "turn on/off", "enable/disable" keywords

        This is best-effort — returns empty dict if nothing matches.
        Not called when LLM is available; serves as offline test fallback.
        """
        result: dict[str, str] = {}

        # Extract quoted strings
        quoted = []
        for m in _QUOTED_PATTERN.finditer(instruction):
            val = m.group(1) or m.group(2) or m.group(3) or m.group(4)
            if val:
                quoted.append(val)

        # Boolean detection for variables containing boolean-ish keywords
        bool_hints = {"enabled", "toggle", "switch", "on_off"}
        instruction_lower = instruction.lower()
        for var in variable_names:
            if any(hint in var for hint in bool_hints):
                if any(kw in instruction_lower for kw in _ON_KEYWORDS):
                    result[var] = "true"
                elif any(kw in instruction_lower for kw in _OFF_KEYWORDS):
                    result[var] = "false"

        # Map quoted strings to remaining unresolved variables
        remaining = sorted(variable_names - set(result.keys()))
        for i, var in enumerate(remaining):
            if i < len(quoted):
                result[var] = quoted[i]

        return result

    @staticmethod
    def _parse_json_response(response: str) -> dict[str, Any] | None:
        """Parse LLM response as JSON, stripping markdown fences if present."""
        text = response.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None
