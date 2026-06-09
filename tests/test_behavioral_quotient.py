"""Unit tests for the fixed-point behavioral quotient.

These exercise :func:`vigil.neuro.behavioral_quotient.quotient_states`
in isolation, with hand-built ``label_fn`` / ``action_key_fn`` callables
so the algorithm's properties can be pinned without an :class:`AppFSM`.
"""

from __future__ import annotations

from typing import Any

from vigil.neuro.behavioral_quotient import (
    TransitionRow,
    quotient_states,
)


def _hash(label: dict[str, Any]) -> str:
    return repr(sorted(label.items()))


def _action_key(action: dict[str, Any]) -> str:
    return str(action.get("type", "")) + ":" + str(action.get("rid", ""))


class TestQuotientLabelGrouping:
    def test_same_label_no_edges_collapses(self) -> None:
        labels = {"s1": {"k": "A"}, "s2": {"k": "A"}, "s3": {"k": "B"}}
        result = quotient_states(
            states=["s1", "s2", "s3"],
            transitions=[],
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        # Two blocks: {s1, s2} and {s3}.
        assert len(result.block_to_members) == 2
        sizes = sorted(len(m) for m in result.block_to_members.values())
        assert sizes == [1, 2]


class TestQuotientCompatibility:
    def test_under_observed_state_merges_with_active_peer(self) -> None:
        """A leaf with no outgoing high-trust edges should not split off
        from a peer that observes additional behavior consistent with
        the leaf's empty observation."""
        labels = {"s1": {"k": "A"}, "s2": {"k": "A"}}
        rows = [TransitionRow(source="s1", target="s2", action={"type": "click"})]
        result = quotient_states(
            states=["s1", "s2"],
            transitions=rows,
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        # Even though s1 has an outgoing edge and s2 does not, the
        # quotient must NOT split them: s2's empty observation is
        # consistent with s1's (no contradicting action key).
        assert len(result.block_to_members) == 1


class TestQuotientConflict:
    def test_same_action_to_different_blocks_splits(self) -> None:
        labels = {sid: {"k": "A"} for sid in ("s1", "s2", "t1", "t2")}
        labels["t1"]["k"] = "B"
        labels["t2"]["k"] = "C"  # different label = different block
        rows = [
            TransitionRow(source="s1", target="t1", action={"type": "click", "rid": "x"}),
            TransitionRow(source="s2", target="t2", action={"type": "click", "rid": "x"}),
        ]
        result = quotient_states(
            states=["s1", "s2", "t1", "t2"],
            transitions=rows,
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        # s1 and s2 had label A but their click(x) lands in different
        # target blocks (B vs C) -> they must split.
        s1_block = result.state_to_block["s1"]
        s2_block = result.state_to_block["s2"]
        assert s1_block != s2_block


class TestQuotientSelfGrowthChain:
    """States in a "growth chain" where each member transitions to the
    next under the same action key must coalesce, because the targets
    themselves coalesce into the same block."""

    def test_chain_collapses(self) -> None:
        states = [f"s{i}" for i in range(1, 5)]
        labels = {sid: {"k": "A"} for sid in states}
        rows = [
            TransitionRow(source=src, target=tgt, action={"type": "click", "rid": "next"})
            for src, tgt in zip(states[:-1], states[1:], strict=False)
        ]
        result = quotient_states(
            states=states,
            transitions=rows,
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        # All states land in one block; the click(next) becomes a SELF
        # transition at the block level.
        assert len(result.block_to_members) == 1


class TestRepresentativeChoice:
    def test_initial_state_is_representative_when_present(self) -> None:
        labels = {"s1": {"k": "A"}, "s2": {"k": "A"}}
        result = quotient_states(
            states=["s1", "s2"],
            transitions=[],
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
            initial_state="s2",
        )
        # Block containing the initial state picks it as representative
        # even though "s1" < "s2" lexically.
        for block_id, members in result.block_to_members.items():
            if "s2" in members:
                assert result.block_to_representative[block_id] == "s2"


class TestEvolutionLog:
    def test_emits_entry_per_non_singleton_block(self) -> None:
        labels = {sid: {"k": "A"} for sid in ("s1", "s2", "s3")}
        labels["s3"]["k"] = "B"
        result = quotient_states(
            states=["s1", "s2", "s3"],
            transitions=[],
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        # Only the {s1, s2} block contributes; the singleton {s3} does not.
        entries = result.evolution_log_entries
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "behavioral_quotient"
        assert sorted(entry["absorbed"]) == ["s2"]
        assert entry["representative"] == "s1"


class TestRedirectMap:
    def test_redirect_is_identity_for_singletons(self) -> None:
        labels = {"s1": {"k": "A"}, "s2": {"k": "B"}}
        result = quotient_states(
            states=["s1", "s2"],
            transitions=[],
            label_fn=lambda sid: labels[sid],
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        redirect = result.redirect_map()
        assert redirect == {"s1": "s1", "s2": "s2"}


class TestEmptyInput:
    def test_empty_states_returns_empty_result(self) -> None:
        result = quotient_states(
            states=[],
            transitions=[],
            label_fn=lambda sid: {},
            label_hash_fn=_hash,
            action_key_fn=_action_key,
        )
        assert result.state_to_block == {}
        assert result.block_to_members == {}
        assert result.evolution_log_entries == []


class TestSchemaOnlyLabelsMergeThread:
    """End-to-end check that schema-only labels + canonical action
    refinement collapse a chat-list-then-thread mini-FSM to two blocks,
    even when per-contact thread screens differ in literal title text."""

    def test_per_contact_thread_screens_collapse(self) -> None:
        from vigil.models.fsm import canonical_action_key
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        # Mini-FSM: one list screen plus three thread screens (Alice,
        # Bob, Carol). The row-click actions remain canonical/concrete;
        # the target thread states still merge because their state labels
        # match and they have no contradicting outgoing behavior.
        def _row(eid: str, *, parent: str, rid: str) -> dict[str, Any]:
            return {
                "element_id": eid,
                "class_name": "android.view.View",
                "resource_id": rid,
                "text": None,
                "content_description": None,
                "bounds": [0, 0, 0, 0],
                "is_clickable": True,
                "is_long_clickable": False,
                "is_scrollable": False,
                "is_editable": False,
                "is_checkable": False,
                "is_checked": False,
                "is_enabled": True,
                "is_focusable": True,
                "is_focused": False,
                "is_selected": False,
                "is_password": False,
                "depth": 3,
                "children": [],
                "parent_id": parent,
            }

        def _title(eid: str, *, parent: str, text: str) -> dict[str, Any]:
            return {
                "element_id": eid,
                "class_name": "android.widget.TextView",
                "resource_id": "thread.title",
                "text": text,
                "content_description": None,
                "bounds": [0, 0, 0, 0],
                "is_clickable": False,
                "is_long_clickable": False,
                "is_scrollable": False,
                "is_editable": False,
                "is_checkable": False,
                "is_checked": False,
                "is_enabled": True,
                "is_focusable": False,
                "is_focused": False,
                "is_selected": False,
                "is_password": False,
                "depth": 2,
                "children": [],
                "parent_id": parent,
            }

        def _send_btn(parent: str) -> dict[str, Any]:
            return {
                "element_id": "e_send",
                "class_name": "android.widget.Button",
                "resource_id": "thread.send",
                "text": None,
                "content_description": None,
                "bounds": [0, 0, 0, 0],
                "is_clickable": True,
                "is_long_clickable": False,
                "is_scrollable": False,
                "is_editable": False,
                "is_checkable": False,
                "is_checked": False,
                "is_enabled": True,
                "is_focusable": True,
                "is_focused": False,
                "is_selected": False,
                "is_password": False,
                "depth": 2,
                "children": [],
                "parent_id": parent,
            }

        root = {
            "element_id": "e_root",
            "class_name": "android.view.ViewGroup",
            "resource_id": "",
            "text": None,
            "content_description": None,
            "bounds": [0, 0, 0, 0],
            "is_clickable": False,
            "is_long_clickable": False,
            "is_scrollable": False,
            "is_editable": False,
            "is_checkable": False,
            "is_checked": False,
            "is_enabled": True,
            "is_focusable": False,
            "is_focused": False,
            "is_selected": False,
            "is_password": False,
            "depth": 1,
            "children": ["e_r1", "e_r2", "e_r3"],
            "parent_id": "",
        }
        list_screen = {
            "activity_name": ".Inbox",
            "metadata": {},
            "elements": [
                root,
                _row("e_r1", parent="e_root", rid="inbox.row.it_alice"),
                _row("e_r2", parent="e_root", rid="inbox.row.it_bob"),
                _row("e_r3", parent="e_root", rid="inbox.row.it_carol"),
            ],
            "interactable_elements": [
                _row("e_r1", parent="e_root", rid="inbox.row.it_alice"),
                _row("e_r2", parent="e_root", rid="inbox.row.it_bob"),
                _row("e_r3", parent="e_root", rid="inbox.row.it_carol"),
            ],
        }

        def _thread_screen(title: str) -> dict[str, Any]:
            root_t = {**root, "children": ["e_title", "e_send"]}
            elems = [
                root_t,
                _title("e_title", parent="e_root", text=title),
                _send_btn("e_root"),
            ]
            return {
                "activity_name": ".Thread",
                "metadata": {},
                "elements": elems,
                "interactable_elements": [e for e in elems if e.get("is_clickable")],
            }

        screens = {
            "list": list_screen,
            "thread_alice": _thread_screen("Alice"),
            "thread_bob": _thread_screen("Bob"),
            "thread_carol": _thread_screen("Carol"),
        }
        labels = {sid: compute_quotient_label(s) for sid, s in screens.items()}

        rows = [
            TransitionRow(
                source="list",
                target="thread_alice",
                action={
                    "type": "click",
                    "resource_id": "inbox.row.it_alice",
                    "target_resource_id": "inbox.row.it_alice",
                    "target_text": "Alice",
                },
            ),
            TransitionRow(
                source="list",
                target="thread_bob",
                action={
                    "type": "click",
                    "resource_id": "inbox.row.it_bob",
                    "target_resource_id": "inbox.row.it_bob",
                    "target_text": "Bob",
                },
            ),
            TransitionRow(
                source="list",
                target="thread_carol",
                action={
                    "type": "click",
                    "resource_id": "inbox.row.it_carol",
                    "target_resource_id": "inbox.row.it_carol",
                    "target_text": "Carol",
                },
            ),
        ]

        result = quotient_states(
            states=list(screens),
            transitions=rows,
            label_fn=lambda sid: labels[sid],
            label_hash_fn=signature_hash,
            action_key_fn=canonical_action_key,
            initial_state="list",
        )

        # Three thread states collapse to one block. Two blocks total
        # (list, thread).
        block_sizes = sorted(len(m) for m in result.block_to_members.values())
        assert block_sizes == [1, 3]
        # The thread block contains exactly the three thread states.
        thread_block = next(
            bid for bid, members in result.block_to_members.items() if len(members) == 3
        )
        assert result.block_to_members[thread_block] == {
            "thread_alice",
            "thread_bob",
            "thread_carol",
        }
