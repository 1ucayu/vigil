"""Trace-observation-compatible behavioral quotient.

Given an observed FSM as a set of states, transitions, a screen-local
label function, and a quotient action-class function, this module
computes a partition ``P`` of states such that:

* States in the same block share the screen-local label (initial
  partition).
* For every quotient action class ``q`` and every state ``s`` in a
  block ``B``, the *observed* ``(q -> block_id(target))`` map agrees
  with every other state in ``B`` on **shared** keys (compatibility).
  Disjoint observations do not force a split, so under-observed leaves
  coalesce with peers whose observations do not contradict them.

This relaxation is deliberate. It is **not** exact textbook bisimulation
or DFA minimization (which would require pointwise-equal action maps and
would refuse to merge under-observed states). Vigil's verifier-preserving
semantics are "could behave the same under our observations" rather than
"did behave identically," so the relaxation is sound for the verifier's
acceptance rule:

    ALLOW iff (s, a, s') in delta AND Reach(s', goal) AND ...

and the lifted ``delta`` is determinized post-hoc by the builder's
quotient-aware guard (see
``vigil.neuro.fsm_builder.FsmBuilder._coarsen_behavioral_duplicates``),
which splits any source block that still maps one quotient action class
to multiple target blocks among high-trust edges.

The implementation is a deliberate **deterministic fixed-point
refinement**, not Paige-Tarjan or Hopcroft. The FSMs Vigil builds are
small (hundreds of states at most) and the fixed-point form is easier
to trace and debug.

The module is *pure*: it operates on plain dicts / tuples / strings,
takes user-supplied ``label_fn`` and ``action_key_fn`` callables, and
returns a redirect map plus diagnostics. It does **not** import
``vigil.models`` or mutate any FSM. The caller applies the redirect to
the live ``AppFSM``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any

# Sentinel target for intra-block self-loops in the refinement signature.
# Distinguishes a state that loops back to its own block from one that
# leaves the block under the same quotient action class.
_SELF_TARGET = "__SELF__"


@dataclass(frozen=True)
class TransitionRow:
    """Plain transition record consumed by the quotient pass."""

    source: str
    target: str
    action: Any
    low_trust: bool = False


@dataclass
class QuotientResult:
    """Output of :func:`quotient_states`."""

    state_to_block: dict[str, str]
    block_to_members: dict[str, set[str]]
    block_to_representative: dict[str, str]
    block_label_hash: dict[str, str]
    evolution_log_entries: list[dict[str, Any]] = field(default_factory=list)
    refinement_passes: int = 0

    def redirect_map(self) -> dict[str, str]:
        """state_id -> representative_state_id (identity for unchanged states)."""
        out: dict[str, str] = {}
        for state_id, block_id in self.state_to_block.items():
            out[state_id] = self.block_to_representative[block_id]
        return out


def quotient_states(
    states: Iterable[str],
    transitions: Iterable[TransitionRow],
    *,
    label_fn: Callable[[str], dict[str, Any]],
    label_hash_fn: Callable[[dict[str, Any]], str],
    action_key_fn: Callable[[Any], Hashable],
    initial_state: str | None = None,
    max_passes: int = 64,
) -> QuotientResult:
    """Compute the trace-observation-compatible behavioral quotient.

    The result is a partition of ``states`` such that, within each
    block, all members agree on every observed ``(quotient_action_key
    -> block(target))`` pair. Members may differ on actions one has
    observed and another has not — that's the trace-observation
    relaxation; the caller's determinism guard is responsible for
    splitting any block that still violates verifier determinism after
    lifting.

    Args:
        states: All state ids in the FSM.
        transitions: Iterable of :class:`TransitionRow`. Low-trust rows
            are excluded from refinement but recorded for diagnostics.
        label_fn: State id -> deterministic, JSON-serializable label dict
            (typically ``compute_quotient_label`` applied to the state's
            representative raw screen).
        label_hash_fn: Hash function over labels (typically
            :func:`vigil.neuro.behavioral_signature.signature_hash`).
        action_key_fn: Action dict -> hashable quotient action class
            (typically :func:`quotient_action_key`).
        initial_state: When supplied, the block containing this state
            picks ``initial_state`` as its representative so the FSM's
            entry never breaks across the rewire.
        max_passes: Safety bound. Each pass either strictly refines the
            partition or terminates the loop, so the natural fixed point
            is reached well below the bound; the bound exists only to
            catch bugs.

    Returns:
        :class:`QuotientResult`. Apply ``.redirect_map()`` to rewrite
        the FSM.
    """
    state_list = list(states)
    if not state_list:
        return QuotientResult(
            state_to_block={},
            block_to_members={},
            block_to_representative={},
            block_label_hash={},
        )

    # ---- 1. Initial partition by label hash ----
    label_hash: dict[str, str] = {}
    for sid in state_list:
        label_hash[sid] = label_hash_fn(label_fn(sid))

    block_id_seq = 0

    def _next_block_id() -> str:
        nonlocal block_id_seq
        block_id_seq += 1
        return f"b_{block_id_seq:05d}"

    label_to_block: dict[str, str] = {}
    state_to_block: dict[str, str] = {}
    block_to_members: dict[str, set[str]] = defaultdict(set)
    block_label_hash: dict[str, str] = {}
    for sid in state_list:
        lh = label_hash[sid]
        block_id = label_to_block.get(lh)
        if block_id is None:
            block_id = _next_block_id()
            label_to_block[lh] = block_id
            block_label_hash[block_id] = lh
        state_to_block[sid] = block_id
        block_to_members[block_id].add(sid)

    # ---- 2. Index high-trust outgoing transitions per source state ----
    rows = list(transitions)
    outgoing_by_state: dict[str, list[TransitionRow]] = defaultdict(list)
    for row in rows:
        if row.low_trust:
            continue
        outgoing_by_state[row.source].append(row)

    # ---- 3. Fixed-point refinement (compatibility-based) ----
    # For each state we collect its observed (quotient_action_key ->
    # block(target)) map. Within a block, two states are *compatible*
    # iff they agree on every quotient action key both have observed;
    # disjoint observations do not force a split, so an under-observed
    # leaf state coalesces with peers whose observations don't
    # contradict it. This matches the verifier-preserving semantics
    # ("could behave the same" rather than "did behave identically").
    def _action_map(sid: str) -> dict[Hashable, str]:
        own_block = state_to_block[sid]
        out: dict[Hashable, str] = {}
        for row in outgoing_by_state.get(sid, []):
            qak = action_key_fn(row.action)
            tgt_block = (
                _SELF_TARGET
                if state_to_block[row.target] == own_block
                else state_to_block[row.target]
            )
            # First observation wins; conflicts within a single state
            # are pre-existing FSM nondeterminism unrelated to the
            # partition refinement and are handled by the builder's APE
            # refinement step before the quotient runs.
            out.setdefault(qak, tgt_block)
        return out

    def _compatible(a: dict[Hashable, str], b: dict[Hashable, str]) -> bool:
        shared = a.keys() & b.keys()
        return all(a[k] == b[k] for k in shared)

    def _refine_block(members: list[str]) -> list[list[str]]:
        """Partition ``members`` into the coarsest sub-blocks such that
        every pair within a sub-block is pairwise-compatible. The
        compatibility relation is reflexive and symmetric but **not**
        transitive in general, so we greedily seed sub-blocks from
        states ordered by (number of observed actions desc, id asc) —
        the most-observed state seeds first, and any later state joins
        the first sub-block whose representative is compatible with it.
        This is deterministic and matches the intuition "richer
        observations anchor the equivalence class, sparser ones merge
        in where they fit."
        """
        maps = {sid: _action_map(sid) for sid in members}
        order = sorted(members, key=lambda sid: (-len(maps[sid]), sid))
        sub_blocks: list[list[str]] = []
        sub_maps: list[dict[Hashable, str]] = []
        for sid in order:
            placed = False
            for idx, anchor_map in enumerate(sub_maps):
                if _compatible(maps[sid], anchor_map):
                    sub_blocks[idx].append(sid)
                    # Merge sid's observations into the anchor so
                    # subsequent compatibility checks see the full
                    # observed surface of this sub-block.
                    for k, v in maps[sid].items():
                        anchor_map.setdefault(k, v)
                    placed = True
                    break
            if not placed:
                sub_blocks.append([sid])
                sub_maps.append(dict(maps[sid]))
        # Restore the original deterministic ordering inside each sub-block.
        return [sorted(b) for b in sub_blocks]

    pass_count = 0
    while pass_count < max_passes:
        pass_count += 1
        changed = False
        # Snapshot keys; new blocks created mid-pass are deferred to the
        # next iteration so the iteration order stays deterministic.
        block_ids_now = list(block_to_members.keys())
        for block_id in block_ids_now:
            members = block_to_members[block_id]
            if len(members) <= 1:
                continue
            sub_blocks = _refine_block(sorted(members))
            if len(sub_blocks) <= 1:
                continue
            changed = True
            # Keep the largest sub-block on the original block_id so
            # block_label_hash[block_id] remains valid; promote the rest
            # to fresh ids.
            sub_blocks.sort(key=lambda group: (-len(group), group[0]))
            keep_states = sub_blocks[0]
            block_to_members[block_id] = set(keep_states)
            for sub_states in sub_blocks[1:]:
                new_block_id = _next_block_id()
                block_to_members[new_block_id] = set(sub_states)
                block_label_hash[new_block_id] = block_label_hash[block_id]
                for sid in sub_states:
                    state_to_block[sid] = new_block_id
        if not changed:
            break

    # ---- 4. Choose representatives ----
    block_to_representative: dict[str, str] = {}
    for block_id, members in block_to_members.items():
        if initial_state is not None and initial_state in members:
            block_to_representative[block_id] = initial_state
        else:
            block_to_representative[block_id] = min(members)

    # ---- 5. Emit evolution log entries (one per non-trivial block) ----
    evolution: list[dict[str, Any]] = []
    for block_id, members in block_to_members.items():
        if len(members) <= 1:
            continue
        rep = block_to_representative[block_id]
        absorbed = sorted(m for m in members if m != rep)
        evolution.append(
            {
                "action": "behavioral_quotient",
                "representative": rep,
                "absorbed": absorbed,
                "label_hash": block_label_hash[block_id],
                "block_id": block_id,
            }
        )

    return QuotientResult(
        state_to_block=dict(state_to_block),
        block_to_members={bid: set(m) for bid, m in block_to_members.items()},
        block_to_representative=dict(block_to_representative),
        block_label_hash=dict(block_label_hash),
        evolution_log_entries=evolution,
        refinement_passes=pass_count,
    )


__all__ = [
    "QuotientResult",
    "TransitionRow",
    "quotient_states",
]
