"""Context-aware quotient action keys for behavioral refinement.

``quotient_action_key`` is intentionally action-local: it can strip
numeric / index-like selector fragments, but it must not guess that
``product.espresso.open`` and ``product.mocha.open`` are list-row
instances without seeing their source context. This module adds that
source-local context for the behavioral quotient pass only.

The canonical action stored on ``Transition`` remains untouched. These
keys are used only to compare observed transition behavior during state
coarsening.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from vigil.neuro.behavioral_signature import quotient_action_key

_TOKEN_SPLIT_RE = re.compile(r"[._\-]+")
_ROW_TOKEN = "<row>"
_INPUT_ACTION_TYPES = frozenset({"input_text", "set_text", "enter_text", "text"})


@dataclass(frozen=True)
class ContextualActionPattern:
    """A source-local row wildcard inferred from sibling transitions."""

    tokens: tuple[str, ...]
    prefix_len: int
    suffix_len: int
    pair_wildcard_width: int

    @property
    def terminal(self) -> bool:
        return self.suffix_len == 0

    @property
    def wildcard_rid(self) -> str:
        return ".".join(self.tokens)


@dataclass(frozen=True)
class ContextualActionCandidate:
    """Diagnostic record for one inferred contextual action class."""

    source: str
    target_partition: str
    contextual_rid: str
    members: tuple[str, ...]
    current_quotient_rids: tuple[str, ...]
    action_type: str | None
    target_class: str | None
    reason: str

    @property
    def newly_collapsed(self) -> bool:
        return len(set(self.current_quotient_rids)) > 1


@dataclass(frozen=True)
class ContextualActionQuotient:
    """Result of source-local contextual action-key inference."""

    action_keys_by_index: dict[int, tuple[tuple[str, Any], ...]]
    candidates: tuple[ContextualActionCandidate, ...]


def _short_rid(rid: str | None) -> str:
    if not rid:
        return ""
    if ":id/" in rid:
        return rid.split(":id/", 1)[1]
    return rid


def _tokens(rid: str) -> tuple[str, ...]:
    return tuple(tok for tok in _TOKEN_SPLIT_RE.split(_short_rid(rid)) if tok)


def _action_rid(action: dict[str, Any]) -> str:
    return str(action.get("resource_id") or action.get("target_resource_id") or "")


def _class_leaf(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return value.rsplit(".", 1)[-1]


def _qdict(action: dict[str, Any]) -> dict[str, Any]:
    return dict(quotient_action_key(action))


def _current_qrid(action: dict[str, Any]) -> str:
    qd = _qdict(action)
    return str(qd.get("resource_id") or qd.get("target_resource_id") or "")


def _target_class(action: dict[str, Any], qd: dict[str, Any]) -> str:
    return _class_leaf(
        qd.get("target_class")
        or qd.get("target_class_name")
        or action.get("target_class")
        or action.get("target_class_name")
    )


def _coarse_value_identity(qd: dict[str, Any]) -> tuple[Any, Any]:
    return qd.get("text"), qd.get("value")


def _pattern_from_pair(left: str, right: str) -> ContextualActionPattern | None:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or len(left_tokens) != len(right_tokens) or left_tokens == right_tokens:
        return None

    varying = [
        idx
        for idx, (left_tok, right_tok) in enumerate(zip(left_tokens, right_tokens, strict=True))
        if left_tok != right_tok
    ]
    if not varying:
        return None
    if varying != list(range(varying[0], varying[-1] + 1)):
        return None

    prefix_len = varying[0]
    suffix_len = len(left_tokens) - varying[-1] - 1
    if prefix_len <= 0:
        return None

    if suffix_len > 0:
        static_count = prefix_len + suffix_len
        if static_count < 2:
            return None
    elif prefix_len < 2:
        # Terminal wildcard has no role suffix after the instance token,
        # so require at least two stable prefix tokens.
        return None

    tokens = left_tokens[:prefix_len] + (_ROW_TOKEN,) + left_tokens[varying[-1] + 1 :]
    return ContextualActionPattern(
        tokens=tokens,
        prefix_len=prefix_len,
        suffix_len=suffix_len,
        pair_wildcard_width=len(varying),
    )


def _wildcard_width_for(rid: str, pattern: ContextualActionPattern) -> int | None:
    tokens = _tokens(rid)
    static_count = len(pattern.tokens) - 1
    wildcard_width = len(tokens) - static_count
    if wildcard_width <= 0:
        return None
    expected_len = len(pattern.tokens) - 1 + wildcard_width
    if len(tokens) != expected_len:
        return None

    prefix = pattern.tokens[: pattern.prefix_len]
    suffix = pattern.tokens[pattern.prefix_len + 1 :]
    if tuple(tokens[: pattern.prefix_len]) != prefix:
        return None
    if suffix and tuple(tokens[-len(suffix) :]) != suffix:
        return None
    return wildcard_width


def _matches_pattern(rid: str, pattern: ContextualActionPattern) -> bool:
    return _wildcard_width_for(rid, pattern) is not None


def _replace_selector_rid(selector: Any, rid: str) -> Any:
    if not isinstance(selector, tuple):
        return selector
    out: list[tuple[str, Any]] = []
    found = False
    for item in selector:
        if not isinstance(item, tuple) or len(item) != 2:
            out.append(item)  # type: ignore[arg-type]
            continue
        key, value = item
        if key == "resource_id":
            out.append((key, rid))
            found = True
        elif key in {"content_description", "nearby_text"}:
            # Once the RID's row instance is wildcarded, nearby labels
            # are treated as instance-correlated and dropped.
            continue
        else:
            out.append((key, value))
    if not found:
        out.append(("resource_id", rid))
    return tuple(sorted(out, key=lambda kv: str(kv[0])))


def _hashable(value: Any) -> Hashable:
    if isinstance(value, dict):
        return tuple(sorted((str(k), _hashable(v)) for k, v in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(_hashable(v) for v in value))
    return value


def _contextual_key(
    action: dict[str, Any],
    contextual_rid: str | None,
) -> tuple[tuple[str, Any], ...]:
    qd = _qdict(action)
    if contextual_rid:
        qd["resource_id"] = contextual_rid
        qd["target_resource_id"] = contextual_rid
        qd["target_selector"] = _replace_selector_rid(qd.get("target_selector"), contextual_rid)
        qd["target_text"] = None
        qd["target_content_desc"] = None
    return tuple(sorted((str(k), _hashable(v)) for k, v in qd.items()))


def contextual_quotient_action_key(
    action: Any,
    contextual_rid: str | None = None,
) -> tuple[tuple[str, Any], ...]:
    """Return a quotient action key with an optional source-local row RID.

    Without ``contextual_rid`` this is equivalent to
    :func:`quotient_action_key`. With it, selector-bearing RID fields use
    the inferred row template, while the canonical action object is left
    unchanged.
    """
    if not isinstance(action, dict):
        return tuple()
    return _contextual_key(action, contextual_rid)


def _candidate_reason(pattern: ContextualActionPattern, size: int) -> str:
    if pattern.terminal:
        return (
            f"terminal wildcard accepted: {size} same-source/same-target-block siblings, "
            f"{pattern.prefix_len} stable prefix tokens"
        )
    return (
        f"bracketed wildcard accepted: {size} same-source/same-target-block siblings, "
        f"{pattern.prefix_len} prefix and {pattern.suffix_len} suffix tokens"
    )


def infer_contextual_action_quotient(
    transitions: Sequence[Any],
    *,
    target_partition_key: Callable[[str], Hashable] | None = None,
) -> ContextualActionQuotient:
    """Infer source-local row-template action keys for transition rows.

    Candidate groups are accepted only when high-trust transitions from
    the same source agree on action type, target class, coarse value
    class, and target partition. ``target_partition_key`` lets callers
    pass a preliminary state quotient block, so rows that lead to
    different raw screens but the same abstract target can share one
    structural action class.
    """
    partition = target_partition_key or (lambda target: target)
    groups: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for idx, transition in enumerate(transitions):
        if bool(getattr(transition, "low_trust", False)):
            continue
        action = getattr(transition, "action", None)
        if not isinstance(action, dict):
            continue
        rid = _action_rid(action)
        if not rid:
            continue
        qd = _qdict(action)
        action_type = qd.get("type") or action.get("type") or action.get("action_type")
        if str(action_type) in _INPUT_ACTION_TYPES:
            continue
        source = str(getattr(transition, "source", ""))
        target = str(getattr(transition, "target", ""))
        group_key = (
            source,
            partition(target),
            action_type,
            _target_class(action, qd),
            _coarse_value_identity(qd),
        )
        groups[group_key].append((idx, action))

    candidates: list[ContextualActionCandidate] = []
    assigned_rid_by_transition: dict[int, str] = {}

    for group_key, rows in groups.items():
        if len(rows) < 2:
            continue
        source, target_partition, action_type, target_class, _value_identity = group_key
        rid_to_indexes: dict[str, list[int]] = defaultdict(list)
        rid_to_action: dict[str, dict[str, Any]] = {}
        for idx, action in rows:
            rid = _action_rid(action)
            rid_to_indexes[rid].append(idx)
            rid_to_action.setdefault(rid, action)
        unique_rids = sorted(rid_to_indexes)
        if len(unique_rids) < 2:
            continue

        pattern_counter: Counter[ContextualActionPattern] = Counter()
        for left, right in combinations(unique_rids, 2):
            pattern = _pattern_from_pair(left, right)
            if pattern is not None:
                pattern_counter[pattern] += 1

        proposed: list[ContextualActionCandidate] = []
        for pattern in pattern_counter:
            members = [rid for rid in unique_rids if _matches_pattern(rid, pattern)]
            if len(members) < 2:
                continue
            wildcard_widths = {
                width
                for rid in members
                for width in [_wildcard_width_for(rid, pattern)]
                if width is not None
            }
            if pattern.terminal and len(members) < 3:
                continue
            if pattern.terminal and wildcard_widths != {1}:
                # No suffix means no stable role after the row token.
                # Keep this to single-token instances so controls such as
                # hour.increment / minute.increment are not generalized.
                continue

            member_token_sets = {_tokens(rid) for rid in members}
            if len(member_token_sets) < 2:
                continue

            current_qrids = tuple(_current_qrid(rid_to_action[rid]) for rid in members)
            if len(set(current_qrids)) > 1 and not pattern.terminal and len(members) < 3:
                # Without explicit source-screen list evidence in this
                # helper, two-row bracketed collapses are too easy to
                # confuse with distinct functional controls.
                continue
            proposed.append(
                ContextualActionCandidate(
                    source=str(source),
                    target_partition=str(target_partition),
                    contextual_rid=pattern.wildcard_rid,
                    members=tuple(members),
                    current_quotient_rids=current_qrids,
                    action_type=str(action_type) if action_type is not None else None,
                    target_class=str(target_class) if target_class is not None else None,
                    reason=_candidate_reason(pattern, len(members)),
                )
            )

        proposed.sort(
            key=lambda candidate: (
                -len(candidate.members),
                candidate.contextual_rid.endswith(_ROW_TOKEN),
                candidate.contextual_rid,
            )
        )
        used: set[str] = set()
        for candidate in proposed:
            if any(member in used for member in candidate.members):
                continue
            candidates.append(candidate)
            used.update(candidate.members)
            for rid in candidate.members:
                for idx in rid_to_indexes[rid]:
                    assigned_rid_by_transition[idx] = candidate.contextual_rid

    action_keys_by_index: dict[int, tuple[tuple[str, Any], ...]] = {}
    for idx, transition in enumerate(transitions):
        action = getattr(transition, "action", None)
        if isinstance(action, dict):
            action_keys_by_index[idx] = _contextual_key(
                action,
                assigned_rid_by_transition.get(idx),
            )

    candidates.sort(
        key=lambda c: (
            c.source,
            c.target_partition,
            c.contextual_rid,
        )
    )
    return ContextualActionQuotient(
        action_keys_by_index=action_keys_by_index,
        candidates=tuple(candidates),
    )


__all__ = [
    "ContextualActionCandidate",
    "ContextualActionPattern",
    "ContextualActionQuotient",
    "contextual_quotient_action_key",
    "infer_contextual_action_quotient",
]
