"""Generic risk policy for native UI exploration.

Replaces app-specific dangerous-text patterns previously hardcoded in
``vigil.neuro.explorer``. Risk categories are loaded from
``configs/android_platform.yaml`` so deployments can tune them without
touching Python code; defaults are platform-level (Android), not tied to
any specific app.

Risk severity tiers:
  - ``hard_block`` (default: destructive / payment / irreversible / credential):
    actions matching these are NEVER executed regardless of configuration.
    They are unconditionally recorded as skipped attempts so analysis can
    see what was blocked.
  - ``low_trust`` (default: permission / commit / privacy): low-trust
    categories whose blocking is configurable. ``allow_risky=True`` lifts
    the block but they remain tagged so the validator can flag them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from vigil.core.platform_priors import get_exploration_policy, get_risk_categories


class RiskSeverity(StrEnum):
    """Severity tier governing whether a matched category is hard-blocked."""

    HARD_BLOCK = "hard_block"
    LOW_TRUST = "low_trust"


DEFAULT_SEVERITY: dict[str, RiskSeverity] = {
    "destructive": RiskSeverity.HARD_BLOCK,
    "payment": RiskSeverity.HARD_BLOCK,
    "irreversible": RiskSeverity.HARD_BLOCK,
    "credential": RiskSeverity.HARD_BLOCK,
    "permission": RiskSeverity.LOW_TRUST,
    "commit": RiskSeverity.LOW_TRUST,
    "privacy": RiskSeverity.LOW_TRUST,
}


@dataclass(frozen=True)
class RiskPolicy:
    """Maps element text/content-desc to generic risk categories with severity.

    Attributes:
        categories: Category name -> tuple of keyword substrings (lowercased).
        severity: Category name -> :class:`RiskSeverity`.
        allow_risky: If True, ``low_trust`` categories are allowed to
            execute (still tagged in trace metadata). Hard-block categories
            are NEVER allowed to execute, regardless of this flag.
    """

    categories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    severity: dict[str, RiskSeverity] = field(default_factory=dict)
    allow_risky: bool = False

    @classmethod
    def from_config(cls) -> RiskPolicy:
        """Build a policy from ``configs/android_platform.yaml``."""
        raw = get_risk_categories()
        normalized = {
            cat: tuple(kw.lower() for kw in keywords if kw)
            for cat, keywords in raw.items()
            if keywords
        }
        exploration = get_exploration_policy()
        severity_overrides = exploration.get("risk_severity", {}) or {}
        severity = {
            cat: RiskSeverity(severity_overrides.get(cat, DEFAULT_SEVERITY.get(cat, "low_trust")))
            for cat in normalized
        }
        return cls(
            categories=normalized,
            severity=severity,
            allow_risky=bool(exploration.get("allow_risky", False)),
        )

    def tag(self, *texts: str | None) -> list[str]:
        """Return all risk categories matched by any of ``texts``.

        Matching is case-insensitive substring. Empty / None inputs are skipped.
        """
        hits: set[str] = set()
        normalized = [t.lower() for t in texts if t]
        if not normalized:
            return []
        for category, keywords in self.categories.items():
            for kw in keywords:
                if any(kw in t for t in normalized):
                    hits.add(category)
                    break
        return sorted(hits)

    def max_severity(self, risk_tags: list[str]) -> RiskSeverity | None:
        """Return the strongest severity among ``risk_tags``, or None."""
        if not risk_tags:
            return None
        if any(self.severity.get(t) == RiskSeverity.HARD_BLOCK for t in risk_tags):
            return RiskSeverity.HARD_BLOCK
        return RiskSeverity.LOW_TRUST

    def should_skip(self, risk_tags: list[str]) -> bool:
        """True iff matching tags should block execution.

        Hard-block categories always skip. Low-trust categories skip only
        when ``allow_risky`` is False.
        """
        sev = self.max_severity(risk_tags)
        if sev is None:
            return False
        if sev == RiskSeverity.HARD_BLOCK:
            return True
        return not self.allow_risky
