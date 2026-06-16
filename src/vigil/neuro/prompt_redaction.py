"""Generic, configuration/evidence-driven prompt-time identifier redaction.

Offline LLM prompts must not leak benchmark/identifier signals that let a model infer the
specific app or hidden task: raw package names, app slugs, bundle names, raw screen ids, local
file paths, and evaluator/gold labels. This module masks those *in the assembled prompt text*
while preserving everything the model legitimately needs to synthesize guards/invariants —
registry aliases, normalized permissions, sanitized resource hints (``<app>:id/...``), and
action properties.

Redaction is **not** a fixed fidelity-app blacklist. A :class:`PromptRedactor` is built from
identifiers discovered in the current configuration and runtime evidence (the FSM package, the
per-observation package names, raw screen ids, screenshot/XML paths) plus any caller-supplied
``extra_identifiers`` (trace package, output slug, app data / report directories, gold labels).
The fidelity fixtures (``com.vigil``, ``vigilmarket`` …) are only *examples* used in tests, not
the rule.

True identifiers are never altered in the evidence itself — only in the prompt string. Admission
keeps using the unredacted evidence registry, so masking displayed ids/paths cannot change any
admission outcome; resource-id *suffixes* are preserved (``com.app:id/foo`` -> ``<app>:id/foo``)
so the model can still reason about widget roles.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.models.fsm import AppFSM

# Generic backstops applied in addition to the configured/evidence identifiers.
_SCR_RE = re.compile(r"\bscr_\d+\b", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"/[^\s'\"()\]]+\.(?:png|jpe?g|webp|xml|json)", re.IGNORECASE)

_APP_MASK = "<app>"
_SCR_MASK = "<scr>"
_PATH_MASK = "<path>"


class PromptRedactor:
    """Masks configured/evidence identifiers in a prompt string.

    ``app_literals`` (packages, slugs, bundle names, gold labels) -> ``<app>``;
    ``screen_ids`` and the generic ``scr_####`` pattern -> ``<scr>``;
    ``paths`` and generic absolute media/data paths -> ``<path>``.
    Longer literals are masked before shorter ones so a fidelity bundle id is masked before its
    embedded package, and paths are masked before package literals can fragment them.
    """

    def __init__(
        self,
        *,
        packages: Any = (),
        screen_ids: Any = (),
        paths: Any = (),
        labels: Any = (),
        extra_identifiers: Any = (),
    ) -> None:
        literals: set[str] = set()
        for group in (packages, labels, extra_identifiers):
            for item in group or ():
                value = str(item).strip()
                if value:
                    literals.add(value)
        self._app_literals = sorted(literals, key=len, reverse=True)
        self._screen_ids = sorted(
            {str(s).strip() for s in (screen_ids or ()) if str(s).strip()},
            key=len,
            reverse=True,
        )
        self._paths = sorted(
            {str(p).strip() for p in (paths or ()) if str(p).strip()},
            key=len,
            reverse=True,
        )

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        # Paths first (exact, longest-first), then the generic absolute-path backstop, so a
        # package literal cannot fragment a path before it is masked whole.
        for path in self._paths:
            out = out.replace(path, _PATH_MASK)
        out = _ABS_PATH_RE.sub(_PATH_MASK, out)
        # App identifiers (packages/slugs/labels). Resource-id suffixes survive because only the
        # identifier substring is replaced: ``com.app:id/foo`` -> ``<app>:id/foo``.
        for literal in self._app_literals:
            out = out.replace(literal, _APP_MASK)
        # Screen ids (explicit list, then the generic scr_#### backstop).
        for screen_id in self._screen_ids:
            out = out.replace(screen_id, _SCR_MASK)
        out = _SCR_RE.sub(_SCR_MASK, out)
        return out


def _collect_from_screen(
    screen: Any,
    packages: set[str],
    screen_ids: set[str],
    paths: set[str],
) -> None:
    if screen is None:
        return
    package = getattr(screen, "package_name", "")
    if package:
        packages.add(str(package))
    screen_id = getattr(screen, "screen_id", "")
    if screen_id:
        screen_ids.add(str(screen_id))
    for attr in ("screenshot_path", "xml_tree_path"):
        value = getattr(screen, attr, "")
        if value:
            paths.add(str(value))


def build_prompt_redactor(
    fsm: AppFSM,
    evidence_items: Any = None,
    *,
    extra_identifiers: Any = None,
) -> PromptRedactor:
    """Build a redactor from the FSM package and per-evidence identifiers (duck-typed).

    Works for both :class:`~vigil.neuro.guard_evidence.GuardEvidence` (``source_screen`` /
    ``target_screen`` / ``*_screen_ids``) and
    :class:`~vigil.neuro.invariant_evidence.InvariantEvidence` (``raw_screen_ids`` /
    ``observations``). ``extra_identifiers`` carries caller/config identifiers (trace package,
    output slug, directories, evaluator/gold labels).
    """
    packages: set[str] = set()
    screen_ids: set[str] = set()
    paths: set[str] = set()

    app_package = getattr(fsm, "app_package", "") or ""
    if app_package:
        packages.add(str(app_package))

    for evidence in evidence_items or ():
        for attr in ("source_screen", "target_screen"):
            _collect_from_screen(getattr(evidence, attr, None), packages, screen_ids, paths)
        for attr in ("source_screen_ids", "target_screen_ids", "raw_screen_ids"):
            ids = getattr(evidence, attr, None)
            if ids:
                screen_ids.update(str(s) for s in ids)
        for observation in getattr(evidence, "observations", None) or ():
            if not isinstance(observation, dict):
                continue
            sid = observation.get("screen_id")
            if sid:
                screen_ids.add(str(sid))
            for key in ("screenshot_path", "xml_tree_path"):
                value = observation.get(key)
                if value:
                    paths.add(str(value))

    return PromptRedactor(
        packages=packages,
        screen_ids=screen_ids,
        paths=paths,
        extra_identifiers=extra_identifiers or (),
    )
