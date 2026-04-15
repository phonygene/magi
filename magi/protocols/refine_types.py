"""REFINE protocol data structures (P0).

Types for the primary-reviewer iterative refinement protocol.
Spec: .omc/plans/refine-mode-proposal-v4.md §2, §3.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Protocol

# Severity ordering. Higher = more severe. Used by upsert() to take historical max.
SEVERITY_ORDER = {"minor": 1, "major": 2, "critical": 3}

_VALID_SEVERITY = {"minor", "major", "critical"}
_VALID_CATEGORY = {"error", "risk", "gap", "improvement"}
_VALID_VERDICT = {"accept", "reject", "partial"}
_VALID_RESOLUTION = {"open", "resolved", "partial_resolved", "reopened"}


# ---------------------------------------------------------------------------
# Core objection / reflection types
# ---------------------------------------------------------------------------


@dataclass
class Objection:
    """Reviewer-raised issue against a primary proposal."""
    id: str                        # "R{round}-{reviewer}-{seq:02d}"
    candidate_key: str             # reviewer-proposed semantic key
    issue_key: str                 # system-canonicalized key
    reviewer: str
    category: str                  # error | risk | gap | improvement
    severity: str                  # critical | major | minor
    target: str                    # proposal SECTION_ID reference
    description: str
    suggestion: str | None = None


@dataclass
class Reflection:
    """Primary's per-issue decision against a consolidated objection."""
    consolidated_id: str
    source_issue_keys: list[str]
    verdict: str                   # accept | reject | partial
    reasoning: str
    chosen_suggestion: str | None = None
    change_summary: str | None = None
    conflict_check: str | None = None
    severity_after: str | None = None


@dataclass
class IssueState:
    """Per-issue tracking record."""
    issue_key: str
    first_raised_round: int
    last_raised_round: int
    raised_count: int
    rejected_count: int
    distinct_reviewers: list[str]
    resolution: str                # open | resolved | partial_resolved | reopened
    severity: str                  # critical | major | minor
    category: str
    latest_description: str
    resolved_at_round: int | None = None
    auto_resolved: bool = False
    latest_target: str = ""  # R02 MAJOR-1: real SECTION_ID for reconcile_cross_round


# ---------------------------------------------------------------------------
# GUIDED callback types
# ---------------------------------------------------------------------------


@dataclass
class UserOverride:
    """Structured user override for a specific issue during GUIDED review."""
    issue_key: str
    verdict: str                       # accept | reject | partial
    severity_after: str | None = None  # required when verdict == "partial"
    reasoning: str | None = None


@dataclass
class UserAction:
    """Result of one GUIDED user-review invocation."""
    action: str                        # approve | override | terminate
    overrides: list[UserOverride] | None = None
    feedback: str | None = None


class UserReviewCallback(Protocol):
    """Async callback invoked after primary reflection when guided=True."""
    async def __call__(
        self,
        round_num: int,
        proposal: str,
        decisions: list[dict],
        issue_summary: dict,
    ) -> UserAction: ...


# ---------------------------------------------------------------------------
# RefineConfig
# ---------------------------------------------------------------------------


@dataclass
class RefineConfig:
    max_rounds: int = 5
    convergence_threshold: int = 0
    primary_index: int = 0
    guided: bool = False
    on_user_review: UserReviewCallback | None = None
    collator_model: str | None = None
    max_budget_usd: float | None = None
    max_context_tokens: int = 32_000
    cancel_event: asyncio.Event | None = None
    on_round_event: Callable[[str, dict], None] | None = None
    guided_timeout_seconds: float = 300.0
    guided_timeout_policy: str = "abort"  # "abort" | "approve"

    def __post_init__(self) -> None:
        if self.guided and self.on_user_review is None:
            raise ValueError(
                "RefineConfig: guided=True requires on_user_review callback"
            )
        if self.guided_timeout_policy not in ("abort", "approve"):
            raise ValueError(
                f"RefineConfig: guided_timeout_policy must be 'abort' or 'approve', "
                f"got {self.guided_timeout_policy!r}"
            )


# ---------------------------------------------------------------------------
# RefineRound (per-round trace record)
# ---------------------------------------------------------------------------


@dataclass
class RefineRound:
    round_num: int
    proposal_text: str = ""
    proposal_hash: str = ""
    proposal_diff: str | None = None
    objections: list[dict] = field(default_factory=list)
    collated_suggestions: list[dict] = field(default_factory=list)
    reflections: list[dict] = field(default_factory=list)
    user_overrides: list[UserOverride] | None = None
    parse_errors: list[str] = field(default_factory=list)
    issue_snapshot: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    accept_rate: float = 0.0
    auto_resolved_keys: list[str] = field(default_factory=list)
    collator_cost_usd: float = 0.0
    collator_failed: bool = False
    guided_timeout: bool = False
    # R02 MAJOR-6: per-round severity snapshot so track_best_round uses the
    # active severity at the time of each round (not the final tracker state,
    # which can be downgraded by later partial_resolved transitions).
    issue_severity_snapshot: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# IssueTracker (A3)
# ---------------------------------------------------------------------------


def _max_severity(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.get(a, 0) >= SEVERITY_ORDER.get(b, 0) else b


@dataclass
class IssueTracker:
    """4-state issue state machine.

    States: open | resolved | partial_resolved | reopened (transient).

    Transitions:
        open             + accept  -> resolved (resolved_at_round = current)
        open             + reject  -> open (rejected_count++)
        open             + partial -> partial_resolved (severity = severity_after)
        resolved         + upsert  -> reopened -> open
        partial_resolved + upsert  -> reopened -> open
    """

    issues: dict[str, IssueState] = field(default_factory=dict)

    def upsert(
        self,
        issue_key: str,
        round_num: int,
        reviewer: str,
        severity: str = "minor",
        category: str = "improvement",
        description: str = "",
        target: str = "",
    ) -> None:
        if severity not in _VALID_SEVERITY:
            raise ValueError(f"invalid severity: {severity!r}")
        if category not in _VALID_CATEGORY:
            raise ValueError(f"invalid category: {category!r}")

        existing = self.issues.get(issue_key)
        if existing is None:
            self.issues[issue_key] = IssueState(
                issue_key=issue_key,
                first_raised_round=round_num,
                last_raised_round=round_num,
                raised_count=1,
                rejected_count=0,
                distinct_reviewers=[reviewer],
                resolution="open",
                severity=severity,
                category=category,
                latest_description=description,
                latest_target=target,
            )
            return

        existing.last_raised_round = round_num
        existing.raised_count += 1
        if reviewer not in existing.distinct_reviewers:
            existing.distinct_reviewers.append(reviewer)

        # Reopen-on-upsert: resolved or partial_resolved -> reopened -> open
        if existing.resolution in ("resolved", "partial_resolved"):
            existing.resolution = "reopened"
            # Transient per spec — flip to open immediately so downstream sees open.
            existing.resolution = "open"
            existing.resolved_at_round = None
            existing.auto_resolved = False

        # Severity: historical max on upsert (conservative).
        existing.severity = _max_severity(existing.severity, severity)
        # Category: take latest.
        existing.category = category
        # Description: always refresh.
        existing.latest_description = description
        # Target: keep latest non-empty value (R02 MAJOR-1).
        if target:
            existing.latest_target = target

    def resolve(
        self,
        issue_key: str,
        verdict: str,
        severity_after: str | None = None,
        current_round: int | None = None,
    ) -> None:
        if verdict not in _VALID_VERDICT:
            raise ValueError(f"invalid verdict: {verdict!r}")
        st = self.issues.get(issue_key)
        if st is None:
            return

        if verdict == "accept":
            st.resolution = "resolved"
            st.resolved_at_round = current_round if current_round is not None else st.last_raised_round
            st.auto_resolved = False
        elif verdict == "reject":
            # open stays open, counter++.
            st.rejected_count += 1
            st.resolution = "open"
        elif verdict == "partial":
            if severity_after is None or severity_after not in _VALID_SEVERITY:
                raise ValueError(
                    "resolve(partial) requires severity_after in {minor,major,critical}"
                )
            st.resolution = "partial_resolved"
            st.severity = severity_after

    def active_issues(self, min_severity: str = "minor") -> list[IssueState]:
        min_rank = SEVERITY_ORDER.get(min_severity, 1)
        out: list[IssueState] = []
        for st in self.issues.values():
            if st.resolution in ("open", "partial_resolved") and SEVERITY_ORDER.get(st.severity, 0) >= min_rank:
                out.append(st)
        return out

    def auto_resolve_silent(self, current_round: int) -> list[str]:
        resolved_keys: list[str] = []
        for st in self.issues.values():
            if st.resolution in ("open", "partial_resolved"):
                st.resolution = "resolved"
                st.resolved_at_round = current_round
                st.auto_resolved = True
                resolved_keys.append(st.issue_key)
        return resolved_keys

    def to_dict(self) -> dict:
        return {
            k: {
                "issue_key": v.issue_key,
                "first_raised_round": v.first_raised_round,
                "last_raised_round": v.last_raised_round,
                "raised_count": v.raised_count,
                "rejected_count": v.rejected_count,
                "distinct_reviewers": list(v.distinct_reviewers),
                "resolution": v.resolution,
                "severity": v.severity,
                "category": v.category,
                "latest_description": v.latest_description,
                "resolved_at_round": v.resolved_at_round,
                "auto_resolved": v.auto_resolved,
                "latest_target": v.latest_target,
            }
            for k, v in self.issues.items()
        }
