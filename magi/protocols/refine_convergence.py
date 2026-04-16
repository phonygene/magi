"""REFINE convergence, best-round tracking, sycophancy, and compute_refine_* (C1-C4).

Spec: .omc/plans/refine-mode-proposal-v4.md §6 + §11.
"""
from __future__ import annotations

from magi.protocols.refine_types import (
    IssueTracker,
    Objection,
    RefineRound,
    SEVERITY_ORDER,
)


# Terminal status strings emitted in refine_summary and Decision UI.
TERMINAL_CONVERGED = "converged"
TERMINAL_THRESHOLD = "threshold"
TERMINAL_MAX_ROUNDS = "max_rounds"
TERMINAL_BUDGET = "budget"
TERMINAL_CANCELLED = "cancelled"
TERMINAL_ABORTED = "aborted"


# ---------------------------------------------------------------------------
# C1 — check_convergence
# ---------------------------------------------------------------------------


def check_convergence(
    tracker: IssueTracker,
    threshold: int,
    current_round: int,
    max_rounds: int,
    round_objections: list[Objection],
    round_parse_errors: list[str],
    successful_reviewer_names: list[str],
) -> tuple[bool, str]:
    """Decide whether refine_protocol should halt after this round.

    Returns ``(converged, terminal_status)``. Per spec §6 priority order:

    1. ALL_RESOLVED (active_issues == 0)            → converged
    2. THRESHOLD   (all minor + <= threshold)       → threshold
    3. NO_NEW_OBJECTIONS + AUTO_RESOLVE             → converged
       Gate: at least one reviewer must have successfully parsed (R8-1).
       Silence from a parse-error reviewer ≠ implicit approval.
    4. MAX_ROUNDS  (current_round >= max_rounds)    → max_rounds
    ``budget`` / ``cancelled`` / ``aborted`` are set by the main loop,
    not here — we only return their enum value when the caller signals them.
    """
    active = tracker.active_issues(min_severity="minor")

    # 1. ALL_RESOLVED
    if not active:
        return True, TERMINAL_CONVERGED

    # 2. THRESHOLD: all active are minor AND count <= threshold
    if all(a.severity == "minor" for a in active) and len(active) <= threshold:
        return True, TERMINAL_THRESHOLD

    # 3. NO_NEW_OBJECTIONS + AUTO_RESOLVE
    # Gate: need ≥1 successful reviewer who stayed silent.
    # round_objections from that reviewer must be empty.
    if successful_reviewer_names:
        silent_reviewers = [
            r for r in successful_reviewer_names
            if not any(o.reviewer == r for o in round_objections)
        ]
        # All successful reviewers must be silent (no objections raised).
        if silent_reviewers and len(silent_reviewers) == len(successful_reviewer_names):
            # Auto-resolve everything active.
            tracker.auto_resolve_silent(current_round)
            return True, TERMINAL_CONVERGED

    # 4. MAX_ROUNDS
    if current_round >= max_rounds:
        return True, TERMINAL_MAX_ROUNDS

    return False, ""


# ---------------------------------------------------------------------------
# C2 — track_best_round
# ---------------------------------------------------------------------------


def _round_score_breakdown(round_obj: RefineRound, tracker_snapshot: dict) -> dict:
    """Count active severities at the time of this round.

    R02 MAJOR-6: prefer ``round_obj.issue_severity_snapshot`` (captured at the
    end of that round) over the final tracker state — partial_resolved can
    downgrade severity later and distort history. Fallback to tracker snapshot
    when the severity snapshot is missing (legacy traces).
    """
    counts = {"critical": 0, "major": 0, "minor": 0}
    resolutions = round_obj.issue_snapshot or {}
    sev_snap = round_obj.issue_severity_snapshot or {}
    for key, resolution in resolutions.items():
        if resolution in ("open", "partial_resolved"):
            severity = sev_snap.get(key) or tracker_snapshot.get(key, {}).get("severity", "minor")
            counts[severity] = counts.get(severity, 0) + 1
    return counts


def track_best_round(rounds: list[RefineRound], tracker: IssueTracker) -> dict:
    """Find the round with the best (highest) score.

    score = -5*critical_active - 2*major_active - 1*minor_active + 3*resolved_count

    Ties: later round wins (prefer most-recent).
    """
    if not rounds:
        return {"best_round": 0, "best_round_score": {"critical": 0, "major": 0, "minor": 0},
                "best_round_note": None}

    tracker_snap = tracker.to_dict()
    best_round = 0
    best_score = -(10**9)
    best_breakdown = {"critical": 0, "major": 0, "minor": 0}
    for r in rounds:
        counts = _round_score_breakdown(r, tracker_snap)
        snapshot = r.issue_snapshot or {}
        resolved_count = sum(1 for v in snapshot.values() if v == "resolved")
        score = (
            -5 * counts["critical"]
            - 2 * counts["major"]
            - 1 * counts["minor"]
            + 3 * resolved_count
        )
        if score >= best_score:
            best_score = score
            best_round = r.round_num
            best_breakdown = counts

    final_round = rounds[-1].round_num
    note = None
    if best_round != final_round:
        note = f"Round {best_round} had a better score than final round {final_round}"
    return {
        "best_round": best_round,
        "best_round_score": best_breakdown,
        "best_round_note": note,
    }


# ---------------------------------------------------------------------------
# C3 — check_sycophancy
# ---------------------------------------------------------------------------


def check_sycophancy(rounds: list[RefineRound]) -> bool:
    """Return True if the most recent 2 rounds both had accept_rate == 1.0."""
    if len(rounds) < 2:
        return False
    return rounds[-1].accept_rate == 1.0 and rounds[-2].accept_rate == 1.0


# ---------------------------------------------------------------------------
# C4 — compute_refine_confidence / votes / minority_report
# ---------------------------------------------------------------------------


def compute_refine_confidence(
    tracker: IssueTracker,
    max_rounds_hit: bool,
    degraded: bool,
) -> float:
    active = tracker.active_issues(min_severity="minor")
    crit = sum(1 for a in active if a.severity == "critical")
    maj = sum(1 for a in active if a.severity == "major")
    base = 1.0 - 0.10 * crit - 0.05 * maj
    if max_rounds_hit:
        base -= 0.15
    if degraded:
        base -= 0.10
    return max(0.1, min(1.0, base))


def compute_refine_votes(
    primary_node_name: str,
    reviewer_nodes: list,
    last_round_objections: list[Objection],
    last_round_parse_errors: list[str],
    ruling: str,
) -> dict[str, str]:
    """Compute per-node votes for Decision.votes.

    - primary: always the ruling (approve).
    - reviewer in parse_errors: ``"PARSE_ERROR"`` (Codex R8-4 / R9 #1).
    - reviewer silent (no objections, no parse error): ruling (approve).
    - reviewer with objections: short dissent summary (!= ruling → UI shows reject).
    """
    votes: dict[str, str] = {primary_node_name: ruling}
    by_reviewer: dict[str, list[Objection]] = {}
    for obj in last_round_objections:
        by_reviewer.setdefault(obj.reviewer, []).append(obj)

    for node in reviewer_nodes:
        name = getattr(node, "name", str(node))
        if name == primary_node_name:
            continue
        if name in last_round_parse_errors:
            votes[name] = "PARSE_ERROR"
        elif name in by_reviewer and by_reviewer[name]:
            dissent = "; ".join(
                f"[{o.severity}] {o.issue_key}" for o in by_reviewer[name][:3]
            )
            votes[name] = f"DISSENT: {dissent}"
        else:
            votes[name] = ruling
    return votes


def compute_refine_minority_report(
    last_round_objections: list[Objection],
    reviewer_nodes: list,
) -> str:
    if not last_round_objections:
        return ""
    by_reviewer: dict[str, list[Objection]] = {}
    for obj in last_round_objections:
        by_reviewer.setdefault(obj.reviewer, []).append(obj)

    lines: list[str] = []
    for reviewer, objs in by_reviewer.items():
        lines.append(f"**{reviewer}**:")
        for o in objs:
            lines.append(f"- {o.issue_key} ({o.severity}): {o.description}")
    return "\n".join(lines)
