"""Stable, screen-local behavioral signature for FSM coarsening.

The signature is a deterministic, hashable description of a raw screen
intended to identify behavioral *equivalence* between observations: two
screens whose signatures match are candidates for merging into a single
abstract state during the post-build coarsening pass. The signature is
**screen-local** — it is derived only from a single screen's UI elements
and per-screen metadata, never from outgoing FSM transitions.

The signature deliberately:

* Includes stable, behavior-relevant features:
    - activity / window_type / dialog flag (hard partition keys)
    - action surface: sorted ``(selector_identity, class_leaf, flags)``
      tuples for every interactable element outside repeated containers
    - form coarse status: ``empty | nonempty | error_visible`` per
      EditText group — *never* the literal contents
    - stable landmarks: toolbar / title / tab text and content-desc
      whose text is not classified volatile
    - stable non-interactable labels outside repeated containers
    - one summary per detected repeated container (list / grid)
* Excludes volatile / templated content:
    - numeric / time / monetary / percent / timestamp / "x of N" text
    - per-row text and per-row resource-id suffixes inside repeated
      containers (those rows are summarized as a skeleton)
    - element bounds and capture-local element ids
    - EditText literal contents

The module operates on the dict shape produced by exploration traces
(``screens[screen_id]`` from ``trace.json``), not on ``RawScreen``
objects. ``state.py``'s ``get_state_id`` / ``get_functional_state_key``
semantics are intentionally left untouched — this is a separate
post-build abstraction layer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Hashable
from typing import Any

_VOLATILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^-?\d+$"),  # pure integer
    re.compile(r"^-?\d+\.\d+$"),  # decimal
    re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$"),  # mm:ss / HH:MM:SS(.s)
    re.compile(r"^\d{1,2}:\d{2}\s*[AaPp][Mm]$"),  # 12h clock
    re.compile(r"^\d+%$"),  # percentage
    re.compile(r"^[$€¥£]\s*-?\d+([.,]\d+)?$"),  # money
    re.compile(r"^\d+\s*[GMK]B$", re.IGNORECASE),  # size
    re.compile(r"^\d+\s+of\s+\d+$", re.IGNORECASE),  # x of N
    re.compile(r"^\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}.*)?$"),  # ISO date(-time)
)

_VOLATILE_SUBSTRING_HINTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\s*-?\d"),  # contains "$<digit>"
    re.compile(r"\b\d{1,2}:\d{2}\b"),  # contains hh:mm
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # contains ISO date
)

_DIALOG_CLASS_TOKENS: tuple[str, ...] = ("Dialog", "AlertDialog", "PopupWindow", "BottomSheet")
_DIALOG_RID_TOKENS: tuple[str, ...] = ("dialog", "alert", "popup", "bottom_sheet")
_TOOLBAR_RID_TOKENS: tuple[str, ...] = ("app_bar", "toolbar", "action_bar")
_TITLE_RID_TOKENS: tuple[str, ...] = ("title", "tab", "header", "name")

_ERROR_RID_TOKENS: tuple[str, ...] = ("error", "invalid", "warning")
_ERROR_TEXT_HINTS: tuple[str, ...] = ("error", "invalid", "required", "must be")

_STATUS_RID_TOKENS: tuple[str, ...] = ("status", "state", "badge")
_STATUS_TEXT_WARNING_HINTS: tuple[str, ...] = (
    "warning",
    "fail",
    "failed",
    "denied",
    "rejected",
    "blocked",
)
_STATUS_TEXT_SUCCESS_HINTS: tuple[str, ...] = (
    "ok",
    "success",
    "succeeded",
    "done",
    "complete",
    "completed",
    "delivered",
    "sent",
    "read",
    "active",
    "online",
)
_STATUS_TEXT_PENDING_HINTS: tuple[str, ...] = (
    "pending",
    "loading",
    "waiting",
    "processing",
    "in progress",
    "queued",
    "uploading",
    "downloading",
)

# Closed vocabulary for the schema-only quotient label. Unknown status
# literals collapse to ``status_present`` when carried by a status/error
# slot, and are omitted entirely otherwise.
_STATUS_CLASS_ERROR = "error"
_STATUS_CLASS_WARNING = "warning"
_STATUS_CLASS_SUCCESS = "success"
_STATUS_CLASS_PENDING = "pending"
_STATUS_CLASS_PRESENT = "status_present"

_REPEATED_MIN_SIBLINGS = 3  # k+ same-shape siblings -> repeated container
_REPEATED_MIN_SIBLINGS_LIST_LIKE = 2  # relaxed minimum when parent looks list-like
_LIST_LIKE_PARENT_CLASS_TOKENS: tuple[str, ...] = (
    "RecyclerView",
    "ListView",
    "GridView",
    "ScrollView",
    "ViewPager",
)
_ANCHORED_LABEL_RID_TOKENS: tuple[str, ...] = (
    "title",
    "header",
    "tab",
    "subtitle",
    "name",
    "error",
    "status",
    "warning",
    "hint",
    "label",
)


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _class_leaf(class_name: str | None) -> str:
    if not class_name:
        return ""
    return class_name.rsplit(".", 1)[-1]


def _short_rid_tail(rid: str | None) -> str:
    if not rid:
        return ""
    if ":id/" in rid:
        return rid.split(":id/", 1)[1]
    return rid


def _is_volatile_text(text: str) -> bool:
    """Return True iff ``text`` looks like a volatile readout / counter.

    Pure numeric, time, monetary, percentage, size, "x of N", and
    ISO-date strings are flagged. Substring hints catch composites like
    ``"Preview: $1.23"`` or ``"Elapsed 00:16.9"`` whose entirety isn't
    purely numeric but whose informational payload is a volatile value.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    for pat in _VOLATILE_PATTERNS:
        if pat.match(stripped):
            return True
    return any(pat.search(stripped) for pat in _VOLATILE_SUBSTRING_HINTS)


def _is_dialog(screen: dict[str, Any], elements: list[dict[str, Any]]) -> bool:
    metadata = screen.get("metadata") or {}
    if metadata.get("has_modal"):
        return True
    window_type = (screen.get("window_type") or metadata.get("window_type") or "").lower()
    if window_type and any(tok in window_type for tok in ("dialog", "popup", "modal")):
        return True
    for el in elements:
        cls = _class_leaf(el.get("class_name"))
        if any(tok in cls for tok in _DIALOG_CLASS_TOKENS):
            return True
        rid = (el.get("resource_id") or "").lower()
        if any(tok in rid for tok in _DIALOG_RID_TOKENS):
            return True
    return False


def _window_type(screen: dict[str, Any]) -> str:
    metadata = screen.get("metadata") or {}
    return _safe_str(screen.get("window_type") or metadata.get("window_type") or "")


def _element_skeleton(el: dict[str, Any]) -> tuple[Hashable, ...]:
    """Coarse skeleton for sibling grouping (class + interactability)."""
    return (
        _class_leaf(el.get("class_name")),
        bool(el.get("is_clickable")),
        bool(el.get("is_long_clickable")),
        bool(el.get("is_scrollable")),
        bool(el.get("is_editable")),
        bool(el.get("is_checkable")),
    )


def _rid_canonical_root(rid: str) -> str:
    """Strip a trailing numeric / per-instance index from a resource-id tail.

    Examples (per fidelity-apps observed patterns; not hardcoded to any
    one rid):
        ``thread.message.m_alice_1.text`` -> ``thread.message.text``
        ``thread.message.m_alice_1.options`` -> ``thread.message.options``
        ``transfer.lap_3.time``           -> ``transfer.lap.time``
        ``cart.item.it_42``               -> ``cart.item.it``

    The intent is to collapse per-row identity suffixes so repeated
    siblings within the same list share a canonical root for skeleton
    summaries. Conservative: if no numeric/index segment is detected,
    the rid is returned unchanged.

    Compound dot-segments matching
    ``<one-or-two-char-prefix>_<name>_<digits>`` (e.g.,
    ``m_alice_1``) are dropped wholesale on a first pass. The required
    trailing numeric suffix is important: semantic field names such as
    ``cc_number`` and ``cv_code`` must not collapse into one field.
    """
    tail = _short_rid_tail(rid)
    if not tail:
        return ""
    # First pass: drop entire dot-segments that look like a compound
    # ``<1-2 char prefix>_<identifier>(_<digits>)?`` instance carrier.
    # The very short prefix (1-2 characters) is the discriminator that
    # distinguishes pure instance segments (m_alice_1, m_bob, c_carol_3)
    # from meaningful list-row containers like ``lap_3`` or ``it_42``,
    # whose multi-character prefix is the row container's role.
    surviving: list[str] = []
    for seg in tail.split("."):
        if not seg:
            continue
        if re.fullmatch(r"[A-Za-z]{1,2}_[A-Za-z]+_\d+", seg):
            continue
        surviving.append(seg)
    rejoined = ".".join(surviving)
    # Second pass: per-token instance-index drop (existing behavior).
    parts = re.split(r"[._]", rejoined)
    cleaned: list[str] = []
    for seg in parts:
        if not seg:
            continue
        if re.fullmatch(r"\d+", seg):
            continue
        if re.fullmatch(r"[A-Za-z]+_?\d+", seg):
            continue
        if re.fullmatch(r"m_[A-Za-z]+_?\d+", seg):
            continue
        cleaned.append(seg)
    return ".".join(cleaned)


_SIBLING_RID_TOKEN_SPLIT = re.compile(r"[._\-]")


def _tokenize_rid_tail(tail: str) -> list[str]:
    return [tok for tok in _SIBLING_RID_TOKEN_SPLIT.split(tail) if tok]


def _try_wildcard_align(own: str, siblings: list[str]) -> str | None:
    """Run the positional-alignment wildcard substitution.

    Returns the wildcarded string when:

    * at least 2 distinct sibling sequences participate,
    * every sequence has the same token count,
    * varying positions form a single contiguous span, and
    * the span is flanked by at least one stable token on each side.

    Otherwise returns ``None`` so the caller can try a different input
    form or give up. The varying span collapses to a single ``"*"``.
    """
    own_tokens = _tokenize_rid_tail(own)
    sib_lists = [_tokenize_rid_tail(s) for s in siblings if s]
    if not own_tokens or not sib_lists:
        return None
    if len({tuple(t) for t in sib_lists}) < 2:
        return None
    n = len(own_tokens)
    if any(len(t) != n for t in sib_lists):
        return None
    varying: list[int] = []
    for i in range(n):
        values = {t[i] for t in sib_lists}
        if len(values) > 1:
            varying.append(i)
    if not varying:
        return None
    # Single contiguous span only.
    if varying != list(range(varying[0], varying[-1] + 1)):
        return None
    if varying[0] == 0 or varying[-1] == n - 1:
        return None  # need stable prefix AND stable suffix
    out = own_tokens[: varying[0]] + ["*"] + own_tokens[varying[-1] + 1 :]
    return ".".join(out)


def _sibling_aware_canonical_rid(
    rid_tail: str,
    sibling_rid_tails: list[str],
    *,
    eligible: bool,
) -> str:
    """Replace per-position varying tokens with a single ``"*"`` when the
    same-parent same-skeleton sibling group is plausibly repeated-row
    content and the varying region is bracketed by a stable prefix AND a
    stable suffix.

    Returns ``rid_tail`` unchanged when any guard fails so callers can
    detect "no change" simply by comparing strings. Strict by design:

    * ``eligible`` is the primary conservatism gate — functional sibling
      controls (e.g., ``stopwatch.pause / lap / reset`` button rows)
      live in groups that fail eligibility and are never wildcarded
      even if their tokenization happens to align.
    * Alignment is attempted on ``_rid_canonical_root(tail)`` first;
      if that does not yield a wildcard (or all canonical roots are
      already identical, meaning the existing root canonicalization
      already collapsed the group), the raw ``rid_tail`` is tried.
    * The varying positions must form a single contiguous span — disjoint
      varying positions abort.
    * The span must have a stable token before AND after — this is what
      keeps functional sibling controls intact (their varying token is
      at the end, no stable suffix).
    """
    if not eligible or not rid_tail:
        return rid_tail
    if not sibling_rid_tails:
        return rid_tail

    own_root = _rid_canonical_root(rid_tail)
    sib_roots = [_rid_canonical_root(t) for t in sibling_rid_tails]
    nonempty_roots = [r for r in sib_roots if r]
    distinct_roots = {r for r in nonempty_roots if r}

    # If the existing root canonicalization already collapsed the
    # group (or could not extract any roots), there is no per-row
    # variation for this helper to summarize. Defer to the existing
    # canonical_rid logic in the caller.
    if len(distinct_roots) >= 2 and own_root:
        candidate = _try_wildcard_align(own_root, nonempty_roots)
        if candidate is not None and candidate != own_root:
            return candidate

    # Fall back to raw tails.
    candidate = _try_wildcard_align(rid_tail, list(sibling_rid_tails))
    if candidate is not None and candidate != rid_tail:
        return candidate

    return rid_tail


def _element_to_dict(element: Any) -> dict[str, Any] | None:
    """Normalize dict / pydantic-like element objects to plain dicts."""
    if isinstance(element, dict):
        return dict(element)
    model_dump = getattr(element, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="json"))
    return None


def _flatten_elements(raw: Any) -> list[dict[str, Any]]:
    """Return a flat element list from either a flat list or nested tree.

    Trace screens normally carry ``elements`` as the full flat accessibility
    tree with ``children`` holding child element ids. Some callers/tests may
    provide a nested tree instead; in that case this normalizes nested child
    dicts into the same flat shape while preserving parent/child ids.
    """
    if raw is None:
        return []

    roots = raw if isinstance(raw, list | tuple) else [raw]
    flattened: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    generated_id = 0

    def _next_id(parent_id: str, index: int) -> str:
        nonlocal generated_id
        generated_id += 1
        prefix = parent_id or "__root"
        return f"{prefix}.__child_{index}_{generated_id}"

    def _visit(node_raw: Any, parent_id: str = "", depth: int = 0) -> None:
        node = _element_to_dict(node_raw)
        if node is None:
            return

        eid = str(node.get("element_id") or _next_id(parent_id, 0))
        node["element_id"] = eid
        if parent_id and not node.get("parent_id"):
            node["parent_id"] = parent_id
        if node.get("depth") is None:
            node["depth"] = depth

        nested_children: list[dict[str, Any]] = []
        child_ids: list[str] = []
        for idx, child_raw in enumerate(node.get("children", []) or []):
            child = _element_to_dict(child_raw)
            if child is None:
                child_ids.append(str(child_raw))
                continue
            child_id = str(child.get("element_id") or _next_id(eid, idx))
            child["element_id"] = child_id
            child_ids.append(child_id)
            nested_children.append(child)

        if nested_children:
            node["children"] = child_ids

        if eid not in seen_ids:
            flattened.append(node)
            seen_ids.add(eid)

        child_depth = int(node.get("depth") or depth) + 1
        for child in nested_children:
            _visit(child, eid, child_depth)

    for root in roots:
        _visit(root)

    return flattened


def _screen_full_elements(screen: dict[str, Any]) -> list[dict[str, Any]]:
    """Full semantic element tree/list, falling back to interactables only."""
    for key in ("element_tree", "elements_tree", "ui_tree"):
        elements = _flatten_elements(screen.get(key))
        if elements:
            return elements
    elements = _flatten_elements(screen.get("elements"))
    if elements:
        return elements
    return _flatten_elements(screen.get("interactable_elements"))


def _is_interactable_element(el: dict[str, Any]) -> bool:
    return bool(
        el.get("is_clickable")
        or el.get("is_long_clickable")
        or el.get("is_editable")
        or el.get("is_checkable")
        or el.get("is_scrollable")
    )


def _screen_action_elements(
    screen: dict[str, Any],
    full_elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Interactable-only subset for the action-alphabet contribution."""
    if "interactable_elements" in screen and screen.get("interactable_elements") is not None:
        return _flatten_elements(screen.get("interactable_elements"))
    return [el for el in full_elements if _is_interactable_element(el)]


def _parent_is_list_like(parent: dict[str, Any] | None) -> bool:
    """Generic Android list-container test (no app-specific names).

    Returns True iff the parent's class leaf contains one of the
    well-known list/grid/scroller tokens, OR the parent is itself
    scrollable. Used to relax the repeated-container minimum from 3
    siblings to 2 when the surrounding container is clearly list-like.
    """
    if parent is None:
        return False
    if parent.get("is_scrollable"):
        return True
    leaf = _class_leaf(parent.get("class_name"))
    return any(tok in leaf for tok in _LIST_LIKE_PARENT_CLASS_TOKENS)


def _find_repeated_containers(
    elements: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Identify parents with same-skeleton children that *also* share a
    canonical resource-id root.

    Minimum sibling count is normally ``_REPEATED_MIN_SIBLINGS`` (3),
    but is relaxed to ``_REPEATED_MIN_SIBLINGS_LIST_LIKE`` (2) when the
    canonical root is non-empty AND the parent's class leaf is one of
    the well-known list-container types (``_LIST_LIKE_PARENT_CLASS_TOKENS``)
    or the parent is itself scrollable. This recovers two-row seeded
    lists (e.g., a fresh chat thread with two messages) which would
    otherwise leak per-row content into the signature.

    The canonical-root requirement is critical for soundness: three
    sibling Views with rids ``stopwatch.pause`` / ``stopwatch.lap`` /
    ``stopwatch.reset`` share a skeleton (View + clickable) but are
    distinct *functional* affordances, not a repeated list. Only when
    siblings share both skeleton *and* canonical root do we treat them
    as a list/grid row-set.

    Children with empty resource-ids fall back to ``(skeleton,)`` alone,
    matching the historic behavior for anonymous repeated Views inside
    grids that lack stable rids — these always require the strict
    minimum of 3 siblings.

    Returns:
        (
            parent_id -> list of representative child dicts in that group,
            set of element_ids that live in (or below) any repeated container,
        )
    """
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for el in elements:
        pid = el.get("parent_id") or ""
        if pid:
            by_parent[pid].append(el)

    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}

    repeated_groups: dict[str, list[dict[str, Any]]] = {}
    repeated_subtree: set[str] = set()

    for parent_id, siblings in by_parent.items():
        if len(siblings) < _REPEATED_MIN_SIBLINGS_LIST_LIKE:
            continue
        parent = by_id.get(parent_id)
        list_like = _parent_is_list_like(parent)
        skel_root_groups: dict[tuple[tuple[Hashable, ...], str], list[dict[str, Any]]] = (
            defaultdict(list)
        )
        for s in siblings:
            rid = s.get("resource_id") or ""
            root = _rid_canonical_root(rid)
            skel_root_groups[(_element_skeleton(s), root)].append(s)
        for (_skel, root), group in skel_root_groups.items():
            if not root:
                # Anonymous: keep the strict minimum so distinct anonymous
                # Views aren't accidentally collapsed.
                if len(group) < _REPEATED_MIN_SIBLINGS:
                    continue
                if any((s.get("resource_id") or "") for s in group):
                    continue
            else:
                # Named: relaxed minimum only when the surrounding
                # container is platform-generic list-like; otherwise
                # require the strict minimum so a 2-View toolbar with
                # a shared rid root doesn't accidentally collapse.
                min_required = (
                    _REPEATED_MIN_SIBLINGS_LIST_LIKE if list_like else _REPEATED_MIN_SIBLINGS
                )
                if len(group) < min_required:
                    continue
            repeated_groups.setdefault(parent_id, []).extend(group)
            for child in group:
                _mark_descendants(child.get("element_id") or "", by_id, repeated_subtree)
                eid = child.get("element_id")
                if eid:
                    repeated_subtree.add(eid)

    return repeated_groups, repeated_subtree


def _sibling_canonical_root_counts(
    elements: list[dict[str, Any]],
) -> dict[tuple[str, tuple[Hashable, ...], str], int]:
    """Per-parent counts of ``(parent_id, skeleton, canonical_root)`` triples.

    Used by ``_action_surface`` to detect interactables that *would have*
    been collapsed if a third sibling existed: when at least two
    same-skeleton same-canonical-root siblings live under one parent, an
    individual surface entry uses the canonical root instead of the
    per-instance rid. This means a two-row seeded list collapses without
    waiting for the third row.
    """
    counts: dict[tuple[str, tuple[Hashable, ...], str], int] = defaultdict(int)
    for el in elements:
        pid = el.get("parent_id") or ""
        if not pid:
            continue
        rid = el.get("resource_id") or ""
        root = _rid_canonical_root(rid)
        if not root:
            continue
        counts[(pid, _element_skeleton(el), root)] += 1
    return counts


def _mark_descendants(root_id: str, by_id: dict[str, dict[str, Any]], out: set[str]) -> None:
    """DFS down ``root_id`` (via the ``children`` list), bounded depth."""
    if not root_id:
        return
    stack: list[tuple[str, int]] = [(root_id, 0)]
    while stack:
        eid, depth = stack.pop()
        if depth > 30:
            continue
        el = by_id.get(eid)
        if el is None:
            continue
        for cid in el.get("children", []) or []:
            if cid in out:
                continue
            out.add(cid)
            stack.append((cid, depth + 1))


def _action_surface(
    elements: list[dict[str, Any]],
    repeated_subtree: set[str],
    *,
    sibling_root_counts: dict[tuple[str, tuple[Hashable, ...], str], int] | None = None,
    detected_repeated_parents: set[str] | None = None,
    full_elements: list[dict[str, Any]] | None = None,
) -> list[tuple[Hashable, ...]]:
    """Sorted tuples describing each interactable element outside repeated
    containers. The tuple captures stable identity (selector key), class
    leaf, and interactability/state flags; bounds and element_ids are
    excluded.

    When ``sibling_root_counts`` is supplied, an interactable whose
    ``(parent_id, skeleton, canonical_root)`` triple appears for at
    least two siblings uses the canonical root as its surface rid. This
    lets two-row seeded lists (e.g., a fresh chat thread with two
    message-options buttons) collapse on the action surface even when
    ``_find_repeated_containers`` did not absorb them — for example,
    when their parent is not list-like.

    When ``detected_repeated_parents`` is supplied (the ids of parents
    that ``_find_repeated_containers`` already classified as repeated),
    or when a same-parent same-skeleton group is plausibly repeated-row
    content under the eligibility rule (see below), the sibling-aware
    helper :func:`_sibling_aware_canonical_rid` is invoked to wildcard
    a contiguous instance span (e.g., ``thread.message.m_alice_1.options``
    and ``thread.message.m_bob_2.options`` collapse to
    ``thread.message.*.options``). The wildcard substitution affects
    only the surface tuple; ``canonical_action_key`` is untouched.
    """
    surface: set[tuple[Hashable, ...]] = set()
    counts = sibling_root_counts or {}
    detected_parents = detected_repeated_parents or set()
    # ``by_id`` covers parent lookup. Prefer ``full_elements`` (which
    # includes container parents); fall back to the interactable
    # ``elements`` list when callers do not supply it.
    pool = full_elements if full_elements is not None else elements
    by_id = {e["element_id"]: e for e in pool if e.get("element_id")}

    # Precompute (parent_id, skeleton) -> [(element_id, rid_tail)] for
    # interactable elements outside repeated_subtree with non-empty rid.
    groups: dict[tuple[str, tuple[Hashable, ...]], list[tuple[str, str]]] = defaultdict(list)
    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if not _is_interactable_element(el):
            continue
        rid_tail = _short_rid_tail(el.get("resource_id") or "")
        if not rid_tail:
            continue
        pid = el.get("parent_id") or ""
        skel = _element_skeleton(el)
        groups[(pid, skel)].append((eid, rid_tail))

    # Eligibility per (parent, skeleton) group.
    eligibility: dict[tuple[str, tuple[Hashable, ...]], bool] = {}
    for (pid, skel), members in groups.items():
        if len(members) < 2:
            eligibility[(pid, skel)] = False
            continue
        parent_dict = by_id.get(pid)
        list_like = _parent_is_list_like(parent_dict)
        repeated_here = pid in detected_parents
        size_ok = len(members) >= 3
        state_div = False
        if size_ok and not (list_like or repeated_here):
            first_eid = members[0][0]
            first_el = by_id.get(first_eid, {})
            ref_selected = bool(first_el.get("is_selected"))
            ref_checked = bool(first_el.get("is_checked"))
            ref_editable = bool(first_el.get("is_editable"))
            for eid, _tail in members[1:]:
                el2 = by_id.get(eid, {})
                if (
                    bool(el2.get("is_selected")) != ref_selected
                    or bool(el2.get("is_checked")) != ref_checked
                    or bool(el2.get("is_editable")) != ref_editable
                ):
                    state_div = True
                    break
        eligibility[(pid, skel)] = bool(list_like or repeated_here or (size_ok and not state_div))

    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if not _is_interactable_element(el):
            continue
        rid = el.get("resource_id") or ""
        rid_tail = _short_rid_tail(rid)
        # Strip a trailing per-instance index. The base case uses the
        # per-element canonical-root; if the same (parent, skeleton,
        # root) triple has >= 2 siblings, prefer the canonical root
        # alone so per-instance variants collapse.
        per_element_root = _rid_canonical_root(rid) or rid_tail
        parent_id = el.get("parent_id") or ""
        skel = _element_skeleton(el)
        triple = (parent_id, skel, _rid_canonical_root(rid))
        canonical_rid = (
            triple[2] if (triple[2] and counts.get(triple, 0) >= 2) else per_element_root
        )
        # Sibling-aware wildcard substitution. Compare the returned
        # value against the original ``rid_tail`` (not ``canonical_rid``)
        # because the helper's "no change" contract is to return the
        # tail unchanged. Comparing against the already-normalized
        # ``canonical_rid`` would silently overwrite the existing root
        # canonicalization with the raw tail or spuriously trigger the
        # text/cd drop.
        sib_wildcard_fired = False
        if rid_tail:
            group_members = groups.get((parent_id, skel), [])
            # Include the current element's own tail — the helper
            # counts distinct *sequences* across the full group and
            # needs at least 2 to align.
            sibling_tails = [t for (_eid, t) in group_members]
            if len(sibling_tails) >= 2:
                sib_canon = _sibling_aware_canonical_rid(
                    rid_tail,
                    sibling_tails,
                    eligible=eligibility.get((parent_id, skel), False),
                )
                if sib_canon != rid_tail:
                    canonical_rid = sib_canon
                    sib_wildcard_fired = True
        text = el.get("text") or ""
        cd = el.get("content_description") or ""
        # Text/content-desc are kept only if non-volatile and short
        # enough to plausibly be a stable label, not user content.
        if _is_volatile_text(text):
            text = ""
        if _is_volatile_text(cd):
            cd = ""
        # When the canonical-root replacement fired OR the sibling-aware
        # wildcard fired, the original text was instance-correlated with
        # the rid; drop it.
        if canonical_rid != per_element_root or sib_wildcard_fired:
            text = ""
            cd = ""
        # Editable fields contribute their selector but not their value;
        # _form_coarse_status carries the empty/nonempty class instead.
        if el.get("is_editable"):
            text = ""
        surface.add(
            (
                canonical_rid,
                text.strip(),
                cd.strip(),
                _class_leaf(el.get("class_name")),
                bool(el.get("is_clickable")),
                bool(el.get("is_long_clickable")),
                bool(el.get("is_scrollable")),
                bool(el.get("is_editable")),
                bool(el.get("is_checkable")),
                bool(el.get("is_checked")),
                bool(el.get("is_selected")),
                bool(el.get("is_enabled", True)),
            )
        )
    return sorted(surface)


def _form_coarse_status(
    elements: list[dict[str, Any]],
    repeated_subtree: set[str],
) -> list[tuple[str, str]]:
    """Per-field coarse status for EditText / input elements.

    Each entry is ``(canonical_field_key, status)`` where status is one
    of ``empty | nonempty | error_visible``. Literal contents (OTP
    digits, amount strings, memo text, password chars) never appear.
    """
    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}
    statuses: dict[str, str] = {}
    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if not el.get("is_editable"):
            continue
        rid = el.get("resource_id") or ""
        key = _rid_canonical_root(rid) or _short_rid_tail(rid) or _class_leaf(el.get("class_name"))
        text = (el.get("text") or "").strip()
        status = "nonempty" if text else "empty"
        # Look at siblings / nearby descendants for visible error labels.
        if _has_error_sibling(el, by_id):
            status = "error_visible"
        statuses[key] = status
    return sorted(statuses.items())


def _has_error_sibling(el: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    parent_id = el.get("parent_id") or ""
    if not parent_id:
        return False
    parent = by_id.get(parent_id)
    if parent is None:
        return False
    for sid in parent.get("children", []) or []:
        sib = by_id.get(sid)
        if sib is None or sib is el:
            continue
        rid = (sib.get("resource_id") or "").lower()
        if any(tok in rid for tok in _ERROR_RID_TOKENS):
            return True
        text = (sib.get("text") or "").lower()
        if text and any(hint in text for hint in _ERROR_TEXT_HINTS):
            return True
    return False


def _stable_landmarks(
    elements: list[dict[str, Any]],
    repeated_subtree: set[str],
) -> list[tuple[str, str]]:
    """Stable text/content-desc anchors from toolbar / title / tab regions.

    Excludes elements inside repeated containers and any text classified
    as volatile.
    """
    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}

    def _is_inside_toolbar(elem: dict[str, Any]) -> bool:
        cur_pid = elem.get("parent_id") or ""
        depth = 0
        while cur_pid and depth < 20:
            parent = by_id.get(cur_pid)
            if parent is None:
                return False
            rid = (parent.get("resource_id") or "").lower()
            if any(tok in rid for tok in _TOOLBAR_RID_TOKENS):
                return True
            cur_pid = parent.get("parent_id") or ""
            depth += 1
        return False

    anchors: set[tuple[str, str]] = set()
    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if el.get("is_editable"):
            continue
        rid_tail = _short_rid_tail(el.get("resource_id") or "")
        rid_lower = rid_tail.lower()
        is_title_like = any(tok in rid_lower for tok in _TITLE_RID_TOKENS)
        is_toolbar_text = "TextView" in _class_leaf(el.get("class_name")) and _is_inside_toolbar(el)
        # The label must be *anchored*: title-shaped rid, toolbar
        # ancestry, or an explicit ``is_selected`` flag. A depth-only
        # fallback (previously accepted any element with depth <= 2 and
        # any text) admits arbitrary body text and is unsound for the
        # quotient label.
        if not (is_title_like or is_toolbar_text or el.get("is_selected")):
            continue
        text = (el.get("text") or "").strip()
        cd = (el.get("content_description") or "").strip()
        if text and not _is_volatile_text(text):
            anchors.add((_rid_canonical_root(rid_tail) or rid_tail, text))
        if cd and not _is_volatile_text(cd):
            anchors.add((_rid_canonical_root(rid_tail) or rid_tail or "cd", f"@cd:{cd}"))
    return sorted(anchors)


def _stable_labels(
    elements: list[dict[str, Any]],
    repeated_subtree: set[str],
) -> list[tuple[Hashable, ...]]:
    """Stable non-action labels anchored by recognizable identity hints.

    Only labels whose evidence is *anchored* — by a title/header/tab/
    error/status-shaped resource-id tail, by toolbar ancestry, or by an
    explicit ``is_selected`` flag — are emitted. Free body text (chat
    message bodies, generated paragraphs, arbitrary content text) is
    **never** emitted, because it is instance content, not state
    identity. Elements inside repeated containers, interactable
    elements (covered by the action surface), volatile text, and
    EditText values are all excluded.
    """
    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}

    def _is_inside_toolbar(elem: dict[str, Any]) -> bool:
        cur_pid = elem.get("parent_id") or ""
        depth = 0
        while cur_pid and depth < 20:
            parent = by_id.get(cur_pid)
            if parent is None:
                return False
            rid = (parent.get("resource_id") or "").lower()
            if any(tok in rid for tok in _TOOLBAR_RID_TOKENS):
                return True
            cur_pid = parent.get("parent_id") or ""
            depth += 1
        return False

    labels: set[tuple[Hashable, ...]] = set()
    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if el.get("is_editable") or _is_interactable_element(el):
            continue

        text = (el.get("text") or "").strip()
        cd = (el.get("content_description") or "").strip()
        if not text and not cd:
            continue

        rid_tail = _short_rid_tail(el.get("resource_id") or "")
        rid_lower = rid_tail.lower()

        anchored_by_rid = any(tok in rid_lower for tok in _ANCHORED_LABEL_RID_TOKENS)
        anchored_by_toolbar = _is_inside_toolbar(el)
        anchored_by_state = bool(el.get("is_selected"))

        if not (anchored_by_rid or anchored_by_toolbar or anchored_by_state):
            continue

        key = _rid_canonical_root(rid_tail) or rid_tail or f"{_class_leaf(el.get('class_name'))}"
        class_leaf = _class_leaf(el.get("class_name"))
        if text and not _is_volatile_text(text):
            labels.add((key, class_leaf, "text", text))
        if cd and not _is_volatile_text(cd):
            labels.add((key, class_leaf, "content_description", cd))
    return sorted(labels)


def _anchor_kind(
    el: dict[str, Any],
    rid_lower: str,
    is_inside_toolbar: bool,
) -> str:
    """Classify the anchor *kind* (not its literal value).

    Returns one of ``{title, toolbar, tab, header, error, status,
    selected, label}``. Used by the schema-only quotient label so the
    anchor slot identity is preserved without leaking literal text.
    """
    if any(tok in rid_lower for tok in _ERROR_RID_TOKENS):
        return "error"
    if any(tok in rid_lower for tok in _STATUS_RID_TOKENS):
        return "status"
    if "tab" in rid_lower:
        return "tab"
    if "header" in rid_lower:
        return "header"
    if any(tok in rid_lower for tok in _TITLE_RID_TOKENS):
        return "title"
    if is_inside_toolbar:
        return "toolbar"
    if el.get("is_selected"):
        return "selected"
    return "label"


def _coarse_status_class(text: str, anchor_kind: str) -> str | None:
    """Return a closed-vocabulary status class for an anchored label, or None.

    The vocabulary is intentionally small and conservative:
    ``{error, warning, success, pending, status_present}``. The literal
    text is never preserved.

    * Anchors whose kind is ``error`` always map to ``error`` regardless
      of literal (the rid itself names the slot).
    * For ``status`` anchors, the literal is matched against the
      ``warning|success|pending`` hint tables. Unknown literals collapse
      to ``status_present`` (carrier exists, semantics unknown) so
      arbitrary status messages do not leak into the quotient identity.
    * For non-status anchors, an explicit error-text hint promotes to
      ``error``. Otherwise the label has no coarse class and the caller
      should drop the literal entirely (return ``None``).
    """
    if anchor_kind == "error":
        return _STATUS_CLASS_ERROR
    lower = text.strip().lower()
    if anchor_kind == "status":
        if any(h in lower for h in _STATUS_TEXT_WARNING_HINTS):
            return _STATUS_CLASS_WARNING
        if any(h in lower for h in _STATUS_TEXT_SUCCESS_HINTS):
            return _STATUS_CLASS_SUCCESS
        if any(h in lower for h in _STATUS_TEXT_PENDING_HINTS):
            return _STATUS_CLASS_PENDING
        return _STATUS_CLASS_PRESENT
    # Non-status anchor: only an explicit error-text hint promotes to a
    # closed-vocab class; everything else is *value* identity and must
    # not enter the quotient label.
    if any(h in lower for h in _ERROR_TEXT_HINTS):
        return _STATUS_CLASS_ERROR
    return None


def _stable_landmarks_schema(
    elements: list[dict[str, Any]],
    repeated_subtree: set[str],
) -> list[tuple[Hashable, ...]]:
    """Schema-only variant of :func:`_stable_landmarks` for the quotient label.

    Walks the same anchored-element filter (toolbar ancestry,
    title-shaped rid, ``is_selected``) but emits
    ``(rid_canonical_root or "*", class_leaf, anchor_kind, value_kind,
    status_class_or_*)`` tuples — never the literal text or
    content-description value. ``status_class_or_*`` is a closed-vocab
    token only when the anchor is error/status (or the literal carries
    an explicit error hint); otherwise it is ``"*"`` so the slot's
    presence is recorded without any literal payload.
    """
    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}

    def _is_inside_toolbar(elem: dict[str, Any]) -> bool:
        cur_pid = elem.get("parent_id") or ""
        depth = 0
        while cur_pid and depth < 20:
            parent = by_id.get(cur_pid)
            if parent is None:
                return False
            rid = (parent.get("resource_id") or "").lower()
            if any(tok in rid for tok in _TOOLBAR_RID_TOKENS):
                return True
            cur_pid = parent.get("parent_id") or ""
            depth += 1
        return False

    schema: set[tuple[Hashable, ...]] = set()
    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if el.get("is_editable"):
            continue
        rid_tail = _short_rid_tail(el.get("resource_id") or "")
        rid_lower = rid_tail.lower()
        is_title_like = any(tok in rid_lower for tok in _TITLE_RID_TOKENS)
        is_toolbar_text = "TextView" in _class_leaf(el.get("class_name")) and _is_inside_toolbar(el)
        if not (is_title_like or is_toolbar_text or el.get("is_selected")):
            continue
        text = (el.get("text") or "").strip()
        cd = (el.get("content_description") or "").strip()
        anchor = _anchor_kind(el, rid_lower, is_toolbar_text)
        key = _rid_canonical_root(rid_tail) or rid_tail or "*"
        class_leaf = _class_leaf(el.get("class_name"))
        if text and not _is_volatile_text(text):
            status_cls = _coarse_status_class(text, anchor) or "*"
            schema.add((key, class_leaf, anchor, "text", status_cls))
        if cd and not _is_volatile_text(cd):
            status_cls = _coarse_status_class(cd, anchor) or "*"
            schema.add((key, class_leaf, anchor, "cd", status_cls))
    return sorted(schema)


def _stable_labels_schema(
    elements: list[dict[str, Any]],
    repeated_subtree: set[str],
) -> list[tuple[Hashable, ...]]:
    """Schema-only variant of :func:`_stable_labels` for the quotient label.

    Emits ``(rid_canonical_root or "*", class_leaf, anchor_kind,
    value_kind, status_class_or_*)`` tuples. Behaves like
    :func:`_stable_labels` for filtering but never writes the literal
    text/content-description. Non-status anchors with unknown literals
    contribute only the slot identity (status_class = ``"*"``); status
    anchors with unknown literals contribute ``status_present``.
    """
    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}

    def _is_inside_toolbar(elem: dict[str, Any]) -> bool:
        cur_pid = elem.get("parent_id") or ""
        depth = 0
        while cur_pid and depth < 20:
            parent = by_id.get(cur_pid)
            if parent is None:
                return False
            rid = (parent.get("resource_id") or "").lower()
            if any(tok in rid for tok in _TOOLBAR_RID_TOKENS):
                return True
            cur_pid = parent.get("parent_id") or ""
            depth += 1
        return False

    schema: set[tuple[Hashable, ...]] = set()
    for el in elements:
        eid = el.get("element_id") or ""
        if eid in repeated_subtree:
            continue
        if el.get("is_editable") or _is_interactable_element(el):
            continue

        text = (el.get("text") or "").strip()
        cd = (el.get("content_description") or "").strip()
        if not text and not cd:
            continue

        rid_tail = _short_rid_tail(el.get("resource_id") or "")
        rid_lower = rid_tail.lower()

        anchored_by_rid = any(tok in rid_lower for tok in _ANCHORED_LABEL_RID_TOKENS)
        anchored_by_toolbar = _is_inside_toolbar(el)
        anchored_by_state = bool(el.get("is_selected"))

        if not (anchored_by_rid or anchored_by_toolbar or anchored_by_state):
            continue

        anchor = _anchor_kind(el, rid_lower, anchored_by_toolbar)
        key = _rid_canonical_root(rid_tail) or rid_tail or f"{_class_leaf(el.get('class_name'))}"
        class_leaf = _class_leaf(el.get("class_name"))
        if text and not _is_volatile_text(text):
            status_cls = _coarse_status_class(text, anchor) or "*"
            schema.add((key, class_leaf, anchor, "text", status_cls))
        if cd and not _is_volatile_text(cd):
            status_cls = _coarse_status_class(cd, anchor) or "*"
            schema.add((key, class_leaf, anchor, "cd", status_cls))
    return sorted(schema)


def _repeated_skeletons(
    repeated_groups: dict[str, list[dict[str, Any]]],
    elements: list[dict[str, Any]],
) -> list[tuple[Hashable, ...]]:
    """One canonical entry per detected repeated container.

    Captures (parent_canonical_rid, child_class_leaf, child_interactability,
    presence-flag) without per-row text or per-row resource-id suffix.
    The count is *not* included — it is the most volatile feature of a
    growing list (clock laps, chat threads). A presence flag (empty vs
    non-empty) is included as that *is* behavior-relevant.
    """
    by_id = {e["element_id"]: e for e in elements if e.get("element_id")}
    summaries: set[tuple[Hashable, ...]] = set()
    for parent_id, group in repeated_groups.items():
        parent = by_id.get(parent_id, {})
        parent_rid = _rid_canonical_root(parent.get("resource_id") or "") or _short_rid_tail(
            parent.get("resource_id") or ""
        )
        parent_class = _class_leaf(parent.get("class_name"))
        # Use the dominant child skeleton (the group fed in already
        # shares one).
        sample = group[0]
        summaries.add(
            (
                parent_rid,
                parent_class,
                _class_leaf(sample.get("class_name")),
                bool(sample.get("is_clickable")),
                bool(sample.get("is_long_clickable")),
                bool(sample.get("is_scrollable")),
                bool(sample.get("is_checkable")),
                "non_empty",  # presence flag — empty containers produce no group
            )
        )
    return sorted(summaries)


def compute_behavioral_signature(
    screen: dict[str, Any],
    *,
    dialog_flag: bool | None = None,
    app_package: str = "",
) -> dict[str, Any]:
    """Screen-local behavioral signature derived from a trace screen dict.

    Args:
        screen: A single screen entry from an exploration trace (the
            ``screens[screen_id]`` value), or any dict carrying
            ``activity_name`` / ``elements`` / ``interactable_elements``
            / ``metadata`` fields.
        dialog_flag: Optional override — when ``None``, dialog presence
            is detected from elements + metadata. Callers that already
            ran ``FsmBuilder._is_dialog_state`` should pass the result
            here so the partition key stays consistent with FSM-side
            dialog detection.
        app_package: Reserved for future filtering; currently unused.

    Returns:
        A deterministic, JSON-serializable signature dict. Pass to
        :func:`signature_hash` for a stable group key.
    """
    full_elements = _screen_full_elements(screen)
    action_elements = _screen_action_elements(screen, full_elements)
    if dialog_flag is None:
        dialog_flag = _is_dialog(screen, full_elements)

    repeated_groups, repeated_subtree = _find_repeated_containers(full_elements)
    sibling_root_counts = _sibling_canonical_root_counts(full_elements)
    detected_repeated_parents = set(repeated_groups.keys())

    return {
        "activity": _safe_str(screen.get("activity_name")) or "",
        "window_type": _window_type(screen),
        "dialog": bool(dialog_flag),
        "action_surface": _action_surface(
            action_elements,
            repeated_subtree,
            sibling_root_counts=sibling_root_counts,
            detected_repeated_parents=detected_repeated_parents,
            full_elements=full_elements,
        ),
        "form_status": _form_coarse_status(full_elements, repeated_subtree),
        "landmarks": _stable_landmarks(full_elements, repeated_subtree),
        "stable_labels": _stable_labels(full_elements, repeated_subtree),
        "repeated_skeletons": _repeated_skeletons(repeated_groups, full_elements),
    }


def compute_quotient_label(
    screen: dict[str, Any],
    *,
    dialog_flag: bool | None = None,
) -> dict[str, Any]:
    """Screen-local label used as the initial partition for behavioral
    quotienting. Strictly instance-free and **schema-only** for anchored
    UI text.

    The label keeps verifier-relevant predicates (activity, dialog flag,
    window type, action surface with per-instance rid segments stripped,
    form coarse status, anchored landmark/label *schema*) and drops
    everything else (free body text, per-row content, EditText literals,
    volatile readouts, bounds, element ids, repeated-container presence,
    and **all literal anchored title/label text**).

    Schema-only anchored fields:

    * ``landmarks`` / ``stable_labels`` carry
      ``(rid_canonical_root, class_leaf, anchor_kind, value_kind,
      status_class_or_*)`` tuples — never the literal carrier value.
    * ``anchor_kind`` records the slot kind
      (``title|toolbar|tab|header|error|status|selected|label``) so
      different anchor slots still split.
    * ``status_class_or_*`` is one of the closed-vocab tokens
      ``{error, warning, success, pending, status_present}`` when the
      anchor is itself a status/error slot or the literal carries an
      explicit error hint; otherwise ``"*"`` (slot presence only).

    Differences vs the full :func:`compute_behavioral_signature`:

    * ``repeated_skeletons`` is **excluded** (presence flag
      over-discriminates; the action surface already records row
      skeletons when sibling counts qualify).
    * ``landmarks`` / ``stable_labels`` use the schema-only variants
      instead of literal-bearing variants.

    The output is a deterministic JSON-serializable dict. Pass to
    :func:`signature_hash` for a stable block key.
    """
    full_elements = _screen_full_elements(screen)
    action_elements = _screen_action_elements(screen, full_elements)
    if dialog_flag is None:
        dialog_flag = _is_dialog(screen, full_elements)

    _repeated_groups, repeated_subtree = _find_repeated_containers(full_elements)
    sibling_root_counts = _sibling_canonical_root_counts(full_elements)
    detected_repeated_parents = set(_repeated_groups.keys())

    return {
        "activity": _safe_str(screen.get("activity_name")) or "",
        "window_type": _window_type(screen),
        "dialog": bool(dialog_flag),
        "action_surface": _action_surface(
            action_elements,
            repeated_subtree,
            sibling_root_counts=sibling_root_counts,
            detected_repeated_parents=detected_repeated_parents,
            full_elements=full_elements,
        ),
        "form_status": _form_coarse_status(full_elements, repeated_subtree),
        "landmarks": _stable_landmarks_schema(full_elements, repeated_subtree),
        "stable_labels": _stable_labels_schema(full_elements, repeated_subtree),
    }


def signature_hash(signature: dict[str, Any]) -> str:
    """Stable 16-char hash of a behavioral signature for grouping.

    Uses canonical-form JSON so dict key order does not affect the hash.
    Tuples are normalized to lists at the boundary.
    """
    canonical = json.dumps(_to_jsonable(signature), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


__all__ = [
    "compute_behavioral_signature",
    "compute_quotient_label",
    "signature_hash",
]
