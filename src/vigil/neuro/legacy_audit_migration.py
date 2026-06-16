"""Opt-in, offline-only migration of *legacy* prompt-only guard/invariant audit artifacts.

The active generation path is schema-constrained structured output (see
:mod:`vigil.neuro.guard_contract_llm` / :mod:`vigil.neuro.invariant_guard_llm`) and never
parses raw/fenced JSON or repairs model output. This module exists **solely** to read OLD audit
artifacts produced by the previous prompt-only path so they remain historically readable. It:

- is opt-in (reached only from an explicit script subcommand / direct call),
- is offline / debug / migration only,
- is never invoked by default generation,
- never repairs or reinterprets *new* structured outputs,
- never affects admission or runtime verification, and
- records every structural normalization it performs in ``normalization_warnings``.

It applies a *narrow, deterministic* shape normalizer for known schema-alias drift only
(``slot`` -> ``name``, ``type`` -> ``slot_type``, string ``binding_requirements`` ->
:class:`BindingRequirement`, ``precondition`` -> ``contract``). It must not invent semantic
slots, predicates, aliases, literals, app-specific values, or domain facts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vigil.models.guard import GuardContract, LlmGuardContractCandidate
from vigil.models.invariant_candidate import InvariantGuardCandidatePacket
from vigil.neuro.invariant_guard_llm import LlmInvariantPacketCandidate

# ---------------------------------------------------------------------------
# Legacy JSON extraction (moved off the active path)
# ---------------------------------------------------------------------------


def parse_legacy_json(response: str) -> Any | None:
    """Parse possibly fenced / prose-wrapped JSON, returning ``None`` on failure.

    Legacy-only: the active generation path uses provider structured output and never calls
    this. Preserved here so old prompt-only audit text can still be read.
    """
    text = (response or "").strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    candidate = _first_balanced_json_object(text)
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


def _first_balanced_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


# ---------------------------------------------------------------------------
# Narrow deterministic shape normalizer (structural aliases only)
# ---------------------------------------------------------------------------


def _normalize_slot(slot: Any, warnings: list[str]) -> Any:
    if not isinstance(slot, dict):
        return slot
    out = dict(slot)
    if "name" not in out and "slot" in out:
        out["name"] = out.pop("slot")
        warnings.append("required_slots: renamed legacy 'slot' -> 'name'")
    if "slot_type" not in out and "type" in out:
        out["slot_type"] = out.pop("type")
        warnings.append("required_slots: renamed legacy 'type' -> 'slot_type'")
    return out


def _normalize_binding(binding: Any, warnings: list[str]) -> Any:
    if isinstance(binding, str):
        warnings.append(f"binding_requirements: wrapped legacy string {binding!r} as object")
        return {"name": binding}
    return binding


def normalize_contract_payload(payload: Any, warnings: list[str]) -> dict[str, Any]:
    """Normalize a legacy contract dict (structural aliases only). Never invents content."""
    if not isinstance(payload, dict):
        return {}
    out = dict(payload)
    if isinstance(out.get("required_slots"), list):
        out["required_slots"] = [_normalize_slot(s, warnings) for s in out["required_slots"]]
    if isinstance(out.get("binding_requirements"), list):
        out["binding_requirements"] = [
            _normalize_binding(b, warnings) for b in out["binding_requirements"]
        ]
    return out


def _unwrap_contract(payload: Any, warnings: list[str]) -> Any:
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("contract"), dict):
        return payload["contract"]
    if isinstance(payload.get("precondition"), dict):
        warnings.append("unwrapped legacy 'precondition' -> 'contract'")
        return payload["precondition"]
    return payload


# ---------------------------------------------------------------------------
# Public migration entry points
# ---------------------------------------------------------------------------


def migrate_legacy_guard_audit(path: str | Path) -> LlmGuardContractCandidate:
    """Read one legacy guard audit artifact into a candidate (never raises).

    Records structural normalizations in ``normalization_warnings``. Offline migration only —
    the result is for inspection/replay, not a fresh generation.
    """
    warnings: list[str] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - migration degrades, never crashes
        reason = f"failed to read legacy audit {path}: {exc}"
        return LlmGuardContractCandidate(
            parsed_ok=False, rejection_reason=reason, parse_errors=[reason]
        )

    raw_responses = [str(item) for item in (payload.get("raw_responses") or [])]
    contract_payload = _unwrap_contract(payload, warnings)
    if not isinstance(contract_payload, dict) and raw_responses:
        # Fall back to re-parsing the recorded raw text the old way.
        parsed = parse_legacy_json(raw_responses[-1])
        contract_payload = _unwrap_contract(parsed, warnings)
    normalized = normalize_contract_payload(contract_payload, warnings)

    try:
        contract = GuardContract.model_validate(normalized)
    except ValidationError as exc:
        reason = f"legacy contract validation failed: {exc.error_count()} error(s)"
        return LlmGuardContractCandidate(
            parsed_ok=False,
            rejection_reason=reason,
            parse_errors=[reason],
            raw_responses=raw_responses,
            normalization_warnings=warnings,
        )
    return LlmGuardContractCandidate(
        contract=contract,
        parsed_ok=True,
        semantic_binding_incomplete=contract.semantic_binding_incomplete,
        rejection_reason=str(payload.get("rejection_reason") or ""),
        raw_responses=raw_responses,
        normalization_warnings=warnings,
        schema_constraint_mode="legacy_audit_migration",
    )


def migrate_legacy_invariant_audit(path: str | Path) -> LlmInvariantPacketCandidate:
    """Read one legacy invariant audit artifact into a packet candidate (never raises)."""
    warnings: list[str] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        reason = f"failed to read legacy audit {path}: {exc}"
        return LlmInvariantPacketCandidate(
            parsed_ok=False, rejection_reason=reason, parse_errors=[reason]
        )

    packet_payload = payload.get("packet")
    if not isinstance(packet_payload, dict):
        raw_responses = [str(item) for item in (payload.get("raw_responses") or [])]
        packet_payload = parse_legacy_json(raw_responses[-1]) if raw_responses else {}
    packet_payload = packet_payload if isinstance(packet_payload, dict) else {}

    # Normalize any embedded transition-guard candidate contracts (structural aliases only).
    candidates = packet_payload.get("transition_guard_candidates")
    if isinstance(candidates, list):
        normalized_candidates = []
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("contract"), dict):
                candidate = dict(candidate)
                candidate["contract"] = normalize_contract_payload(candidate["contract"], warnings)
            normalized_candidates.append(candidate)
        packet_payload = dict(packet_payload)
        packet_payload["transition_guard_candidates"] = normalized_candidates

    try:
        packet = InvariantGuardCandidatePacket.model_validate(packet_payload)
    except ValidationError as exc:
        reason = f"legacy packet validation failed: {exc.error_count()} error(s)"
        return LlmInvariantPacketCandidate(
            parsed_ok=False,
            rejection_reason=reason,
            parse_errors=[reason],
            normalization_warnings=warnings,
        )
    return LlmInvariantPacketCandidate(
        packet=packet,
        parsed_ok=True,
        rejection_reason=str(payload.get("rejection_reason") or ""),
        normalization_warnings=warnings,
        schema_constraint_mode="legacy_audit_migration",
    )
