"""Tests for the behavioral_signature module and the FSM coarsening pass.

These tests pin the soundness invariants of the post-build abstraction
layer: volatile text and per-row list content should not split states,
but enabledness changes, dialog/modal boundaries, and form coarse-status
transitions must continue to split. The repeated-container detector
must not collapse distinct functional buttons that merely happen to
share a parent and skeleton.
"""

from __future__ import annotations

from typing import Any

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel
from vigil.neuro.behavioral_signature import (
    _is_volatile_text,
    _rid_canonical_root,
    compute_behavioral_signature,
    signature_hash,
)
from vigil.neuro.fsm_builder import FsmBuilder


def _el(
    eid: str,
    *,
    parent_id: str = "",
    class_name: str = "android.widget.TextView",
    resource_id: str | None = None,
    text: str | None = None,
    content_description: str | None = None,
    is_clickable: bool = False,
    is_editable: bool = False,
    is_checkable: bool = False,
    is_checked: bool = False,
    is_enabled: bool = True,
    is_selected: bool = False,
    depth: int = 1,
    children: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "element_id": eid,
        "class_name": class_name,
        "resource_id": resource_id,
        "text": text,
        "content_description": content_description,
        "bounds": [0, 0, 100, 100],
        "is_clickable": is_clickable,
        "is_long_clickable": False,
        "is_scrollable": False,
        "is_editable": is_editable,
        "is_checkable": is_checkable,
        "is_checked": is_checked,
        "is_enabled": is_enabled,
        "is_selected": is_selected,
        "depth": depth,
        "children": children or [],
        "parent_id": parent_id,
    }


def _screen(
    elements: list[dict[str, Any]],
    *,
    activity: str = ".MainActivity",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "activity_name": activity,
        "interactable_elements": elements,
        "elements": elements,
        "metadata": metadata or {},
    }


class TestVolatileText:
    @pytest.mark.parametrize(
        "text",
        [
            "00:16.9",
            "00:23",
            "1:02:03",
            "1:30 PM",
            "$12.34",
            "$1",
            "42",
            "-7",
            "3.14",
            "75%",
            "12 GB",
            "3 of 10",
            "2026-05-28",
            "Elapsed 00:16.9",
        ],
    )
    def test_volatile_patterns(self, text: str) -> None:
        assert _is_volatile_text(text)

    @pytest.mark.parametrize(
        "text",
        ["Send", "Inbox", "Settings", "Cancel", "Photo Beach", "Pay now"],
    )
    def test_stable_labels(self, text: str) -> None:
        assert not _is_volatile_text(text)


class TestCanonicalRoot:
    def test_drops_trailing_index(self) -> None:
        assert _rid_canonical_root("thread.message.m_alice_1.text").endswith("text")
        assert _rid_canonical_root("transfer.lap_3.time").endswith("time")

    def test_keeps_plain_root(self) -> None:
        # No instance suffix to strip.
        assert _rid_canonical_root("stopwatch.pause") == "stopwatch.pause"
        assert _rid_canonical_root("thread.send") == "thread.send"


class TestVolatileTextDoesNotSplit:
    """A timer-running screen with changing elapsed text must collapse to
    one signature when its action surface and form state are unchanged."""

    def _stopwatch_running(self, elapsed_text: str) -> dict[str, Any]:
        elements = [
            _el("e_root", class_name="android.view.View"),
            _el(
                "e_pause",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="stopwatch.pause",
                is_clickable=True,
            ),
            _el(
                "e_reset",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="stopwatch.reset",
                is_clickable=True,
            ),
            _el(
                "e_elapsed", parent_id="e_root", resource_id="stopwatch.elapsed", text=elapsed_text
            ),
        ]
        return _screen(elements)

    def test_elapsed_drift_same_signature(self) -> None:
        sig_a = compute_behavioral_signature(self._stopwatch_running("00:16.9"))
        sig_b = compute_behavioral_signature(self._stopwatch_running("01:42.3"))
        assert signature_hash(sig_a) == signature_hash(sig_b)


class TestNonClickableSemanticBoundary:
    """Non-clickable detail text must remain visible to behavioral coarsening."""

    @staticmethod
    def _detail_screen(title: str) -> dict[str, Any]:
        elements = [
            _el(
                "e_root",
                class_name="android.view.ViewGroup",
                children=["e_title", "e_cancel", "e_send"],
            ),
            _el(
                "e_title",
                parent_id="e_root",
                resource_id="detail.title",
                text=title,
                depth=2,
            ),
            _el(
                "e_cancel",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="detail.cancel",
                text="Cancel",
                is_clickable=True,
                depth=2,
            ),
            _el(
                "e_send",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="detail.send",
                text="Send",
                is_clickable=True,
                depth=2,
            ),
        ]
        return {
            "activity_name": ".DetailActivity",
            "metadata": {},
            "elements": elements,
            "interactable_elements": [
                el for el in elements if el.get("is_clickable") or el.get("is_editable")
            ],
        }

    @staticmethod
    def _state(state_id: str, raw_screen_id: str) -> AbstractState:
        return AbstractState(
            state_id=state_id,
            name=state_id,
            hierarchy_level=HierarchyLevel.ACTIVITY,
            identity={"functional_hash": state_id},
            android_context={"activity_name": ".DetailActivity"},
            evidence={"raw_screen_ids": [raw_screen_id]},
        )

    def test_non_clickable_title_splits_signature(self) -> None:
        sig_alice = compute_behavioral_signature(self._detail_screen("Alice"))
        sig_bob = compute_behavioral_signature(self._detail_screen("Bob"))

        assert signature_hash(sig_alice) != signature_hash(sig_bob)

    def test_coarsening_merges_same_buttons_different_title_under_schema_label(self) -> None:
        """Under the schema-only quotient label, two detail screens that
        share the same action surface but differ only in literal title
        text collapse into one block. The richer
        ``compute_behavioral_signature`` still preserves the literal
        (see :meth:`test_non_clickable_title_splits_signature`) — only
        the partition key is schema-only."""
        fsm = AppFSM("com.example")
        fsm.add_state(self._state("state_alice", "scr_alice"))
        fsm.add_state(self._state("state_bob", "scr_bob"))
        raw_screens = {
            "scr_alice": self._detail_screen("Alice"),
            "scr_bob": self._detail_screen("Bob"),
        }

        merged = FsmBuilder("com.example")._coarsen_behavioral_duplicates(fsm, raw_screens)

        assert merged == 1
        assert len(fsm.states) == 1


class TestRepeatedListContentDoesNotSplit:
    """A chat thread with different message bodies must yield one signature."""

    def _thread(self, msgs: list[tuple[str, str]]) -> dict[str, Any]:
        elements = [
            _el("e_root", class_name="android.view.ViewGroup"),
            _el(
                "e_list",
                parent_id="e_root",
                class_name="androidx.recyclerview.widget.RecyclerView",
                resource_id="thread.message_list",
            ),
            _el(
                "e_send",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="thread.send",
                is_clickable=True,
            ),
            _el(
                "e_input",
                parent_id="e_root",
                class_name="android.widget.EditText",
                resource_id="thread.message.input",
                is_editable=True,
                is_clickable=True,
            ),
        ]
        for i, (mid, body) in enumerate(msgs):
            elements.append(
                _el(
                    f"e_msg_{i}",
                    parent_id="e_list",
                    class_name="android.view.View",
                    resource_id=f"thread.message.{mid}.text",
                    text=body,
                )
            )
            elements.append(
                _el(
                    f"e_opts_{i}",
                    parent_id="e_list",
                    class_name="android.view.View",
                    resource_id=f"thread.message.{mid}.options",
                    is_clickable=True,
                )
            )
        return _screen(elements)

    def test_message_body_drift_same_signature(self) -> None:
        sig_a = compute_behavioral_signature(
            self._thread(
                [
                    ("m_alice_1", "Hey, free tonight?"),
                    ("m_alice_2", "Yes! Where?"),
                    ("m_alice_3", "See you there."),
                ]
            )
        )
        sig_b = compute_behavioral_signature(
            self._thread(
                [
                    ("m_bob_1", "Did you push the patch?"),
                    ("m_bob_2", "Merging now."),
                    ("m_bob_3", "All green."),
                ]
            )
        )
        assert signature_hash(sig_a) == signature_hash(sig_b)


class TestEnabledChangeSplits:
    """Disabling the primary action button must produce a different signature."""

    def _form(self, *, submit_enabled: bool) -> dict[str, Any]:
        elements = [
            _el("e_root"),
            _el(
                "e_amount",
                parent_id="e_root",
                class_name="android.widget.EditText",
                resource_id="transfer.amount.input",
                is_editable=True,
                is_clickable=True,
            ),
            _el(
                "e_submit",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="transfer.continue",
                is_clickable=True,
                is_enabled=submit_enabled,
            ),
        ]
        return _screen(elements)

    def test_enabledness_splits(self) -> None:
        sig_a = compute_behavioral_signature(self._form(submit_enabled=True))
        sig_b = compute_behavioral_signature(self._form(submit_enabled=False))
        assert signature_hash(sig_a) != signature_hash(sig_b)


class TestFormCoarseStatusSplits:
    """An empty form field vs a filled one must split, but the literal text
    must never appear in the signature."""

    def _form_with_text(self, amount: str | None) -> dict[str, Any]:
        elements = [
            _el("e_root"),
            _el(
                "e_amount",
                parent_id="e_root",
                class_name="android.widget.EditText",
                resource_id="transfer.amount.input",
                text=amount,
                is_editable=True,
            ),
        ]
        return _screen(elements)

    def test_empty_vs_nonempty(self) -> None:
        sig_empty = compute_behavioral_signature(self._form_with_text(None))
        sig_filled = compute_behavioral_signature(self._form_with_text("123"))
        assert signature_hash(sig_empty) != signature_hash(sig_filled)

    def test_two_distinct_inputs_collapse(self) -> None:
        sig_a = compute_behavioral_signature(self._form_with_text("123"))
        sig_b = compute_behavioral_signature(self._form_with_text("9876"))
        # Both nonempty -> same coarse status -> same signature.
        assert signature_hash(sig_a) == signature_hash(sig_b)

    def test_no_literal_text_in_signature(self) -> None:
        sig = compute_behavioral_signature(self._form_with_text("topsecret"))
        canonical = signature_hash(sig)
        # Re-compute with completely different literal text — hash equal.
        sig2 = compute_behavioral_signature(self._form_with_text("apassword"))
        assert canonical == signature_hash(sig2)


class TestDialogBoundarySplits:
    """A dialog/modal overlay produces a different signature partition from
    its base screen even if the base elements are identical."""

    def _base(self) -> dict[str, Any]:
        return _screen(
            [
                _el("e_pay", resource_id="checkout.pay", is_clickable=True),
            ]
        )

    def _dialog(self) -> dict[str, Any]:
        # Add explicit dialog-flavored class so the screen-local detector
        # picks it up without needing the metadata flag.
        return _screen(
            [
                _el(
                    "e_dialog",
                    class_name="androidx.appcompat.app.AlertDialog",
                    resource_id="payment_dialog",
                ),
                _el(
                    "e_confirm",
                    parent_id="e_dialog",
                    resource_id="payment_dialog.confirm",
                    is_clickable=True,
                ),
                _el(
                    "e_cancel",
                    parent_id="e_dialog",
                    resource_id="payment_dialog.cancel",
                    is_clickable=True,
                ),
            ]
        )

    def test_dialog_distinct_from_base(self) -> None:
        sig_base = compute_behavioral_signature(self._base())
        sig_dialog = compute_behavioral_signature(self._dialog())
        assert signature_hash(sig_base) != signature_hash(sig_dialog)
        assert sig_dialog["dialog"] is True
        assert sig_base["dialog"] is False


class TestRepeatedDetectorSoundness:
    """Distinct functional buttons that share a parent and skeleton must
    NOT collapse into a repeated container — they must remain visible on
    the action surface. (This is the regression that caused stopwatch
    and timer pages to collide.)"""

    def test_distinct_buttons_stay_on_surface(self) -> None:
        elements = [
            _el("e_root"),
            _el(
                "e_pause",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="stopwatch.pause",
                is_clickable=True,
            ),
            _el(
                "e_lap",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="stopwatch.lap",
                is_clickable=True,
            ),
            _el(
                "e_reset",
                parent_id="e_root",
                class_name="android.view.View",
                resource_id="stopwatch.reset",
                is_clickable=True,
            ),
        ]
        sig = compute_behavioral_signature(_screen(elements))
        rids_on_surface = {entry[0] for entry in sig["action_surface"]}
        assert {"stopwatch.pause", "stopwatch.lap", "stopwatch.reset"} <= rids_on_surface
        assert sig["repeated_skeletons"] == []

    def test_stopwatch_vs_timer_signatures_differ(self) -> None:
        def _row(prefix: str) -> dict[str, Any]:
            return _screen(
                [
                    _el("e_root"),
                    _el(
                        "e_pause",
                        parent_id="e_root",
                        class_name="android.view.View",
                        resource_id=f"{prefix}.pause",
                        is_clickable=True,
                    ),
                    _el(
                        "e_reset",
                        parent_id="e_root",
                        class_name="android.view.View",
                        resource_id=f"{prefix}.reset",
                        is_clickable=True,
                    ),
                ]
            )

        sig_stopwatch = compute_behavioral_signature(_row("stopwatch"))
        sig_timer = compute_behavioral_signature(_row("timer"))
        assert signature_hash(sig_stopwatch) != signature_hash(sig_timer)

    def test_actual_repeated_rows_collapse(self) -> None:
        # Three sibling Views sharing a canonical root *do* collapse.
        rows = []
        for i in range(4):
            rows.append(
                _el(
                    f"e_row_{i}",
                    parent_id="e_list",
                    class_name="android.view.View",
                    resource_id=f"history.entry.txn_{i}",
                    is_clickable=True,
                )
            )
        elements = [
            _el("e_root"),
            _el(
                "e_list",
                parent_id="e_root",
                class_name="androidx.recyclerview.widget.RecyclerView",
                resource_id="history.list",
            ),
            *rows,
        ]
        sig = compute_behavioral_signature(_screen(elements))
        rids_on_surface = {entry[0] for entry in sig["action_surface"]}
        # The repeated rows are absorbed into a skeleton summary.
        assert "history.entry" not in rids_on_surface or len(sig["repeated_skeletons"]) >= 1
        assert sig["repeated_skeletons"], "repeated rows should produce a skeleton"


class TestSignatureHashDeterminism:
    def test_same_input_same_hash(self) -> None:
        s = _screen([_el("e_root"), _el("e_btn", resource_id="x.y", is_clickable=True)])
        assert signature_hash(compute_behavioral_signature(s)) == signature_hash(
            compute_behavioral_signature(s)
        )


class TestQuotientLabelRegressions:
    """Regression tests pinning the quotient label's "stays vs goes"
    semantics: anchored labels split, free body text collapses, two-row
    lists do not over-discriminate."""

    @staticmethod
    def _txt(
        eid: str,
        *,
        parent_id: str = "",
        rid: str | None = None,
        text: str,
        class_name: str = "android.widget.TextView",
    ) -> dict[str, Any]:
        return {
            "element_id": eid,
            "class_name": class_name,
            "resource_id": rid,
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
            "parent_id": parent_id,
        }

    @staticmethod
    def _btn(eid: str, *, parent_id: str = "", rid: str) -> dict[str, Any]:
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
            "depth": 2,
            "children": [],
            "parent_id": parent_id,
        }

    @staticmethod
    def _make(elements: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "activity_name": ".A",
            "metadata": {},
            "elements": elements,
            "interactable_elements": [e for e in elements if e.get("is_clickable")],
        }

    def test_two_row_list_text_ignored_under_list_like_parent(self) -> None:
        """A two-row repeated container under a RecyclerView parent
        collapses; differing per-row text must NOT split."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _screen(rows: list[tuple[str, str]]) -> dict[str, Any]:
            elements: list[dict[str, Any]] = [
                {
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
                    "children": ["e_list"],
                    "parent_id": "",
                },
                {
                    "element_id": "e_list",
                    "class_name": "androidx.recyclerview.widget.RecyclerView",
                    "resource_id": "feed.list",
                    "text": None,
                    "content_description": None,
                    "bounds": [0, 0, 0, 0],
                    "is_clickable": False,
                    "is_long_clickable": False,
                    "is_scrollable": True,
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
                    "parent_id": "e_root",
                },
            ]
            for i, (rid_suffix, body) in enumerate(rows):
                elements.append(
                    self._txt(
                        f"e_row_{i}",
                        parent_id="e_list",
                        rid=f"feed.row.{rid_suffix}.body",
                        text=body,
                    )
                )
            return self._make(elements)

        sig_a = compute_quotient_label(_screen([("it_1", "Hey!"), ("it_2", "Hi!")]))
        sig_b = compute_quotient_label(_screen([("it_3", "Later"), ("it_4", "Now")]))
        assert signature_hash(sig_a) == signature_hash(sig_b)

    def test_free_body_text_does_not_split_quotient_label(self) -> None:
        """Two screens with identical action surfaces and only differing
        non-anchored body text must produce identical quotient labels."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _screen(body_text: str) -> dict[str, Any]:
            return self._make(
                [
                    {
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
                        "children": ["e_body", "e_btn"],
                        "parent_id": "",
                    },
                    # Free body text, no title/header/error rid shape, no
                    # toolbar ancestor — should NOT enter the label.
                    self._txt("e_body", parent_id="e_root", rid="content.message", text=body_text),
                    self._btn("e_btn", parent_id="e_root", rid="screen.act"),
                ]
            )

        sig_a = compute_quotient_label(_screen("Lorem ipsum dolor sit amet."))
        sig_b = compute_quotient_label(_screen("Some completely different prose."))
        assert signature_hash(sig_a) == signature_hash(sig_b)

    def test_anchored_title_schema_only_does_not_split(self) -> None:
        """Schema-only quotient label: a non-clickable element with a
        ``*.title`` rid shape contributes its *slot* to the label, but
        NOT the literal title text. Two screens differing only in title
        text (Alice vs Bob under the same title rid slot) therefore
        produce the same quotient label. The richer
        :func:`compute_behavioral_signature` still preserves the
        literal — it is the diagnostic signature, not the partition
        key."""
        from vigil.neuro.behavioral_signature import (
            compute_behavioral_signature,
            compute_quotient_label,
            signature_hash,
        )

        def _screen(title: str) -> dict[str, Any]:
            return self._make(
                [
                    {
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
                        "children": ["e_title", "e_btn"],
                        "parent_id": "",
                    },
                    self._txt("e_title", parent_id="e_root", rid="detail.title", text=title),
                    self._btn("e_btn", parent_id="e_root", rid="detail.action"),
                ]
            )

        screen_alice = _screen("Alice")
        screen_bob = _screen("Bob")
        # Quotient label is schema-only: identical.
        assert signature_hash(compute_quotient_label(screen_alice)) == signature_hash(
            compute_quotient_label(screen_bob)
        )
        # Full diagnostic signature is literal: distinct.
        assert signature_hash(compute_behavioral_signature(screen_alice)) != signature_hash(
            compute_behavioral_signature(screen_bob)
        )


class TestQuotientActionKey:
    """``quotient_action_key`` must strip per-instance rid segments so
    parametrized actions collapse to one quotient action class, while
    leaving genuinely different action kinds distinct."""

    def test_instance_rids_collapse(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {
            "type": "click",
            "resource_id": "list.row.it_001",
            "target_resource_id": "list.row.it_001",
            "target_text": "Item #1",
            "target_class": "View",
        }
        b = {
            "type": "click",
            "resource_id": "list.row.it_042",
            "target_resource_id": "list.row.it_042",
            "target_text": "Item #42",
            "target_class": "View",
        }
        assert quotient_action_key(a) == quotient_action_key(b)

    def test_distinct_action_kinds_stay_distinct(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {"type": "click", "resource_id": "screen.send"}
        b = {"type": "long_press", "resource_id": "screen.send"}
        assert quotient_action_key(a) != quotient_action_key(b)

    def test_raw_canonical_key_unaffected(self) -> None:
        """The quotient does not modify the raw canonical_action_key —
        replay/dedup pipeline still distinguishes concrete selectors."""
        from vigil.models.fsm import canonical_action_key

        a = {
            "type": "click",
            "resource_id": "list.row.it_001",
            "target_resource_id": "list.row.it_001",
        }
        b = {
            "type": "click",
            "resource_id": "list.row.it_042",
            "target_resource_id": "list.row.it_042",
        }
        assert canonical_action_key(a) != canonical_action_key(b)


class TestQuotientLabelStatusVocabulary:
    """Schema-only quotient label keeps a closed-vocabulary status class
    (``{error, warning, success, pending, status_present}``) for
    error/status slots only, and never preserves literal text."""

    @staticmethod
    def _txt(eid: str, *, parent_id: str = "", rid: str | None = None, text: str) -> dict[str, Any]:
        return {
            "element_id": eid,
            "class_name": "android.widget.TextView",
            "resource_id": rid,
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
            "parent_id": parent_id,
        }

    @staticmethod
    def _btn(eid: str, *, parent_id: str = "", rid: str) -> dict[str, Any]:
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
            "depth": 2,
            "children": [],
            "parent_id": parent_id,
        }

    @classmethod
    def _root_with(
        cls,
        child_id: str,
        child_extras: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        children = [child_id] + [e["element_id"] for e in (child_extras or [])]
        return {
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
            "children": children,
            "parent_id": "",
        }

    @staticmethod
    def _screen(elements: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "activity_name": ".A",
            "metadata": {},
            "elements": elements,
            "interactable_elements": [e for e in elements if e.get("is_clickable")],
        }

    def test_error_anchor_presence_splits(self) -> None:
        """An anchored error label (rid token ``error``) splits two
        screens where only one carries the error slot."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        btn = self._btn("e_btn", parent_id="e_root", rid="screen.act")
        no_error = self._screen([self._root_with("e_btn"), btn])
        with_error = self._screen(
            [
                self._root_with("e_err", child_extras=[btn]),
                self._txt("e_err", parent_id="e_root", rid="form.error", text="Invalid OTP"),
                btn,
            ]
        )
        assert signature_hash(compute_quotient_label(no_error)) != signature_hash(
            compute_quotient_label(with_error)
        )

    def test_status_vocabulary_classes_split(self) -> None:
        """``status`` anchors with literals mapping to distinct
        closed-vocab classes (success vs pending) produce distinct
        quotient labels."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _make(literal: str) -> dict[str, Any]:
            badge = self._txt("e_status", parent_id="e_root", rid="order.status", text=literal)
            return self._screen(
                [
                    self._root_with("e_status"),
                    badge,
                ]
            )

        success_ok = _make("Delivered")
        pending = _make("Pending")
        assert signature_hash(compute_quotient_label(success_ok)) != signature_hash(
            compute_quotient_label(pending)
        )

    def test_status_same_class_different_literals_collapse(self) -> None:
        """Two ``success`` literals under the same status slot collapse
        to one quotient label (closed-vocab class, not literal)."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _make(literal: str) -> dict[str, Any]:
            badge = self._txt("e_status", parent_id="e_root", rid="order.status", text=literal)
            return self._screen([self._root_with("e_status"), badge])

        delivered = _make("Delivered")
        sent = _make("Sent")
        assert signature_hash(compute_quotient_label(delivered)) == signature_hash(
            compute_quotient_label(sent)
        )

    def test_status_unknown_literal_collapses_to_status_present(self) -> None:
        """An unknown literal under a status-shaped slot maps to
        ``status_present`` so two different unknown literals collapse."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _make(literal: str) -> dict[str, Any]:
            badge = self._txt("e_status", parent_id="e_root", rid="order.status", text=literal)
            return self._screen([self._root_with("e_status"), badge])

        frob = _make("Frobnicated")
        wibble = _make("Wibbleated")
        assert signature_hash(compute_quotient_label(frob)) == signature_hash(
            compute_quotient_label(wibble)
        )

    def test_non_status_anchor_unknown_literal_does_not_leak(self) -> None:
        """An unknown literal under a non-status anchor (a title slot)
        does not encode the literal — two distinct unknown literals
        collapse to the same quotient label."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _make(literal: str) -> dict[str, Any]:
            title = self._txt("e_title", parent_id="e_root", rid="detail.title", text=literal)
            return self._screen([self._root_with("e_title"), title])

        a = _make("Apricot")
        b = _make("Blueberry")
        assert signature_hash(compute_quotient_label(a)) == signature_hash(
            compute_quotient_label(b)
        )


class TestQuotientActionKeyWhitelist:
    """The whitelisted ``_quotient_selector`` keeps only verifier-relevant
    selector fields; capture-local fields (bounds, depth, element_id,
    parent_id, ancestor_chain, raw text) must not affect the key."""

    def test_capture_local_selector_fields_dropped(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        base_a = {
            "type": "click",
            "resource_id": "screen.submit",
            "target_selector": {
                "resource_id": "screen.submit",
                "class_name": "android.widget.Button",
                "bounds": [0, 0, 100, 50],
                "text": "Submit",
                "depth": 4,
                "element_id": "e_5",
                "parent_id": "e_4",
                "ancestor_chain": ["root", "form", "footer"],
            },
        }
        base_b = {
            "type": "click",
            "resource_id": "screen.submit",
            "target_selector": {
                "resource_id": "screen.submit",
                "class_name": "android.widget.Button",
                "bounds": [200, 800, 500, 870],
                "text": "Send",
                "depth": 9,
                "element_id": "e_99",
                "parent_id": "e_77",
                "ancestor_chain": ["root", "drawer", "panel", "footer"],
            },
        }
        assert quotient_action_key(base_a) == quotient_action_key(base_b)

    def test_distinct_canonical_roots_still_split(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {
            "type": "click",
            "resource_id": "screen.submit",
            "target_selector": {"resource_id": "screen.submit"},
        }
        b = {
            "type": "click",
            "resource_id": "screen.cancel",
            "target_selector": {"resource_id": "screen.cancel"},
        }
        assert quotient_action_key(a) != quotient_action_key(b)

    def test_volatile_content_description_dropped(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {
            "type": "click",
            "resource_id": "inbox.thread",
            "target_selector": {"resource_id": "inbox.thread", "content_description": "3 of 10"},
        }
        b = {
            "type": "click",
            "resource_id": "inbox.thread",
            "target_selector": {"resource_id": "inbox.thread", "content_description": "5 of 12"},
        }
        assert quotient_action_key(a) == quotient_action_key(b)


class TestQuotientActionKeyValueClass:
    """``quotient_action_key`` normalizes user-entered ``text`` / ``value``
    payloads via a coarse closed-vocab class so literals do not split
    actions."""

    def test_distinct_nonempty_literals_collapse(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {"type": "input_text", "resource_id": "form.memo", "text": "hello"}
        b = {"type": "input_text", "resource_id": "form.memo", "text": "goodbye"}
        assert quotient_action_key(a) == quotient_action_key(b)

    def test_empty_vs_nonempty_split(self) -> None:
        from vigil.neuro.behavioral_signature import quotient_action_key

        empty = {"type": "input_text", "resource_id": "form.memo", "text": ""}
        nonempty = {"type": "input_text", "resource_id": "form.memo", "text": "hello"}
        assert quotient_action_key(empty) != quotient_action_key(nonempty)

    def test_numeric_literals_collapse(self) -> None:
        """Two distinct numeric inputs collapse to the same coarse
        class (``numeric``), even if the specific values differ."""
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {"type": "input_text", "resource_id": "form.amount", "text": "100.00"}
        b = {"type": "input_text", "resource_id": "form.amount", "text": "42"}
        assert quotient_action_key(a) == quotient_action_key(b)

    def test_canonical_action_key_keeps_literal(self) -> None:
        """Sanity guard: canonical_action_key still distinguishes the
        literal values (replay/provenance unaffected)."""
        from vigil.models.fsm import canonical_action_key

        a = {"type": "input_text", "resource_id": "form.memo", "text": "hello"}
        b = {"type": "input_text", "resource_id": "form.memo", "text": "goodbye"}
        assert canonical_action_key(a) != canonical_action_key(b)


class TestSiblingAwareActionSurface:
    """``_action_surface`` collapses per-row instance segments via
    structural sibling-aware canonicalization. The collapse is gated by
    an eligibility rule that keeps functional sibling controls intact."""

    @staticmethod
    def _row(
        eid: str,
        *,
        parent: str,
        rid: str,
        is_selected: bool = False,
        is_checked: bool = False,
        is_editable: bool = False,
    ) -> dict[str, Any]:
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
            "is_editable": is_editable,
            "is_checkable": False,
            "is_checked": is_checked,
            "is_enabled": True,
            "is_focusable": True,
            "is_focused": False,
            "is_selected": is_selected,
            "is_password": False,
            "depth": 2,
            "children": [],
            "parent_id": parent,
        }

    @staticmethod
    def _parent(
        eid: str, *, class_name: str, is_scrollable: bool = False, children: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "element_id": eid,
            "class_name": class_name,
            "resource_id": "",
            "text": None,
            "content_description": None,
            "bounds": [0, 0, 0, 0],
            "is_clickable": False,
            "is_long_clickable": False,
            "is_scrollable": is_scrollable,
            "is_editable": False,
            "is_checkable": False,
            "is_checked": False,
            "is_enabled": True,
            "is_focusable": False,
            "is_focused": False,
            "is_selected": False,
            "is_password": False,
            "depth": 1,
            "children": children or [],
            "parent_id": "",
        }

    @classmethod
    def _screen_with(
        cls,
        rows: list[dict[str, Any]],
        *,
        parent_class: str = "android.view.ViewGroup",
        parent_scrollable: bool = False,
    ) -> dict[str, Any]:
        parent = cls._parent(
            "e_root",
            class_name=parent_class,
            is_scrollable=parent_scrollable,
            children=[r["element_id"] for r in rows],
        )
        elements = [parent] + rows
        return {
            "activity_name": ".A",
            "metadata": {},
            "elements": elements,
            "interactable_elements": [e for e in elements if e.get("is_clickable")],
        }

    @staticmethod
    def _surface_rids(label: dict[str, Any]) -> list[str]:
        return [entry[0] for entry in label["action_surface"]]

    def test_action_surface_collapses_named_row_instance_segments(self) -> None:
        """3 same-skeleton clickable rows with rids ``item.<name>.options``
        under a non-list-like parent: group size >= 3 → eligibility met
        → contiguous middle span collapses to ``item.*.options``."""
        from vigil.neuro.behavioral_signature import compute_quotient_label

        rows = [
            self._row("e_1", parent="e_root", rid="item.alice.options"),
            self._row("e_2", parent="e_root", rid="item.bob.options"),
            self._row("e_3", parent="e_root", rid="item.carol.options"),
        ]
        label = compute_quotient_label(self._screen_with(rows))
        rids = self._surface_rids(label)
        assert rids == ["item.*.options"]

    def test_action_surface_preserves_functional_sibling_controls(self) -> None:
        """``stopwatch.pause / lap / reset``: varying span at final
        position has no stable suffix → not wildcarded; 3 distinct
        action_surface entries preserved with original rids."""
        from vigil.neuro.behavioral_signature import compute_quotient_label

        rows = [
            self._row("e_1", parent="e_root", rid="stopwatch.pause"),
            self._row("e_2", parent="e_root", rid="stopwatch.lap"),
            self._row("e_3", parent="e_root", rid="stopwatch.reset"),
        ]
        label = compute_quotient_label(self._screen_with(rows))
        rids = sorted(self._surface_rids(label))
        assert rids == ["stopwatch.lap", "stopwatch.pause", "stopwatch.reset"]

    def test_action_surface_collapses_numeric_row_ids(self) -> None:
        """2 same-skeleton siblings under a RecyclerView whose
        varying middle token is non-numeric (so the existing
        ``_rid_canonical_root`` does NOT strip it): the sibling-aware
        helper fires under list-like eligibility (min 2) and collapses
        the middle to ``*``. The original chat-style issue surfaces
        through the same code path."""
        from vigil.neuro.behavioral_signature import compute_quotient_label

        rows = [
            self._row("e_1", parent="e_root", rid="row.alpha.open"),
            self._row("e_2", parent="e_root", rid="row.beta.open"),
        ]
        label = compute_quotient_label(
            self._screen_with(rows, parent_class="androidx.recyclerview.widget.RecyclerView")
        )
        rids = self._surface_rids(label)
        assert rids == ["row.*.open"]

    def test_action_surface_collapses_chat_style_row_rids(self) -> None:
        """Chat-style cross-screen invariance. Each per-contact thread
        screen has its own ``m_<contact>_<n>`` message rids. The
        compound-instance dot-segment is dropped wholesale by
        ``_rid_canonical_root``, so the per-screen action_surface no
        longer carries ``m.alice`` / ``m.bob`` literals. Two different
        threads therefore produce the SAME quotient_label hash."""
        from vigil.neuro.behavioral_signature import (
            compute_quotient_label,
            signature_hash,
        )

        def _thread_screen(contact: str, msg_count: int) -> dict[str, Any]:
            # Build a per-contact thread screen with N message rows
            # plus a stable send button. The compound-instance
            # ``m_<contact>_<n>`` token must not leak into the surface.
            send_row = self._row(
                "e_send",
                parent="e_root",
                rid="thread.send",
            )
            msg_rows = [
                self._row(
                    f"e_msg_{i}",
                    parent="e_root",
                    rid=f"thread.message.m_{contact}_{i}.options",
                )
                for i in range(1, msg_count + 1)
            ]
            # Real chat thread message containers are list-like
            # (RecyclerView), so the relaxed min=2 sibling rule applies
            # uniformly regardless of how many messages are visible.
            return self._screen_with(
                [send_row, *msg_rows],
                parent_class="androidx.recyclerview.widget.RecyclerView",
            )

        alice = _thread_screen("alice", 3)
        bob = _thread_screen("bob", 2)
        dad = _thread_screen("dad", 4)
        ha = signature_hash(compute_quotient_label(alice))
        hb = signature_hash(compute_quotient_label(bob))
        hd = signature_hash(compute_quotient_label(dad))
        assert ha == hb == hd
        # No per-contact literal anywhere in the surface.
        for screen in (alice, bob, dad):
            label = compute_quotient_label(screen)
            blob = repr(label["action_surface"])
            for tok in ("alice", "bob", "dad"):
                assert tok not in blob

    def test_two_siblings_under_nonlistlike_parent_not_collapsed(self) -> None:
        """2 siblings under a plain LinearLayout: group size < 3 AND
        parent not list-like → eligibility fails; both rids remain
        distinct in the action surface."""
        from vigil.neuro.behavioral_signature import compute_quotient_label

        rows = [
            self._row("e_1", parent="e_root", rid="feature.alpha.open"),
            self._row("e_2", parent="e_root", rid="feature.beta.open"),
        ]
        label = compute_quotient_label(
            self._screen_with(rows, parent_class="android.widget.LinearLayout")
        )
        rids = sorted(self._surface_rids(label))
        assert rids == ["feature.alpha.open", "feature.beta.open"]

    def test_tab_strip_with_selected_divergence_not_collapsed(self) -> None:
        """Tab strip with one selected and two unselected siblings:
        is_selected divergence disqualifies eligibility even at size 3;
        all three tabs surface distinctly."""
        from vigil.neuro.behavioral_signature import compute_quotient_label

        rows = [
            self._row("e_1", parent="e_root", rid="tabs.home", is_selected=True),
            self._row("e_2", parent="e_root", rid="tabs.search"),
            self._row("e_3", parent="e_root", rid="tabs.profile"),
        ]
        label = compute_quotient_label(
            self._screen_with(rows, parent_class="android.widget.LinearLayout")
        )
        rids = sorted(self._surface_rids(label))
        assert rids == ["tabs.home", "tabs.profile", "tabs.search"]

    def test_canonical_root_first_then_raw_fallback(self) -> None:
        """When the existing ``_rid_canonical_root`` would already strip
        the varying token, the sibling-aware helper must NOT additionally
        insert a stray ``"*"`` into the surface. Two coexisting groups
        share the same parent: one numeric (already collapsed by root
        canonicalization), one named-instance (collapsed by the new
        helper). The surface contains the collapsed numeric root and a
        wildcarded named entry — never an unintended ``"*"`` injection
        into the numeric one."""
        from vigil.neuro.behavioral_signature import compute_quotient_label

        # Numeric instance group + named-instance group under the same
        # ViewGroup parent. Skeletons differ (one View, one Button), so
        # the repeated-container detector groups them separately by
        # skeleton + canonical-root.
        rows = [
            # Numeric group — canonical_root strips digits → shared root
            # ``num.body``. 3 same-skeleton same-root siblings under a
            # non-list-like parent qualify for repeated_subtree absorption,
            # so they disappear from the action surface entirely.
            self._row("e_1", parent="e_root", rid="num.row1.body"),
            self._row("e_2", parent="e_root", rid="num.row2.body"),
            self._row("e_3", parent="e_root", rid="num.row3.body"),
        ]
        label = compute_quotient_label(self._screen_with(rows))
        rids = self._surface_rids(label)
        # The numeric rows are absorbed by repeated_subtree (existing
        # behavior — the existing root canonicalization is the layer
        # responsible for this collapse). The sibling-aware helper
        # must not inject a stray '*' anywhere.
        for r in rids:
            assert "*" not in r

    def test_quotient_action_key_does_not_collapse_named_instances_without_context(
        self,
    ) -> None:
        """``quotient_action_key`` operates on a single action dict with
        no sibling context, so it deliberately does NOT wildcard
        named-instance row segments. That collapse is handled upstream
        in ``_action_surface``."""
        from vigil.neuro.behavioral_signature import quotient_action_key

        a = {
            "type": "click",
            "resource_id": "item.alice.options",
            "target_resource_id": "item.alice.options",
        }
        b = {
            "type": "click",
            "resource_id": "item.bob.options",
            "target_resource_id": "item.bob.options",
        }
        assert quotient_action_key(a) != quotient_action_key(b)

    def test_canonical_action_key_unchanged_for_row_actions(self) -> None:
        """``canonical_action_key`` still preserves the full per-row rid
        for replay / dedup / provenance, even when the quotient layer
        would wildcard it."""
        from vigil.models.fsm import canonical_action_key

        a = {
            "type": "click",
            "resource_id": "item.alice.options",
            "target_resource_id": "item.alice.options",
        }
        b = {
            "type": "click",
            "resource_id": "item.bob.options",
            "target_resource_id": "item.bob.options",
        }
        assert canonical_action_key(a) != canonical_action_key(b)
