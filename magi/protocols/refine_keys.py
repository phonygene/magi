"""Issue key canonicalization and cross-round reconciliation (B1-B3).

Spec: .omc/plans/refine-mode-proposal-v4.md §5.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from magi.protocols.refine_types import (
    IssueTracker,
    Objection,
    SEVERITY_ORDER,
)


# ---------------------------------------------------------------------------
# B1 — canonicalize_key
# ---------------------------------------------------------------------------


def canonicalize_key(candidate_key: str) -> str | None:
    """Normalize a reviewer-proposed candidate_key.

    Returns a canonical key (lowercase, underscores, `::` segments truncated
    to 40 chars each), or None if the normalized result is too short (< 3
    meaningful chars). Caller assigns a fallback like `unknown_issue_{seq}`.
    """
    if candidate_key is None:
        return None
    key = candidate_key.lower().strip()
    key = re.sub(r"\s+", "_", key)
    key = re.sub(r"[^a-z0-9_:]", "", key)
    parts = key.split("::")
    parts = [p[:40] for p in parts]
    result = "::".join(parts)
    if len(result.replace(":", "").replace("_", "")) < 3:
        return None
    return result


# ---------------------------------------------------------------------------
# B2 — merge_similar_keys (intra-round dedup)
# ---------------------------------------------------------------------------


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _higher_severity(a: Objection, b: Objection) -> Objection:
    return a if SEVERITY_ORDER.get(a.severity, 0) >= SEVERITY_ORDER.get(b.severity, 0) else b


def merge_similar_keys(
    objections: list[Objection],
    threshold: float = 0.85,
) -> list[Objection]:
    """Normalize similar issue_keys within one round WITHOUT dropping objections.

    Short keys (< 10 chars) are compared with a tighter threshold (0.92).
    R02 MAJOR-2: the previous implementation dedup'd to a single objection per
    key — that dropped suggestions from non-winning reviewers before they ever
    reached the collator. We now only remap similar keys onto a canonical one
    and return ALL original objections so the collator can preserve every
    reviewer's suggestion / provenance.

    NOTE: input ``objections`` are **mutated in place** — ``issue_key`` may be
    rewritten onto the canonical form. Callers who need to preserve originals
    must ``copy.deepcopy`` before invoking.
    """
    if not objections:
        return []

    # Group by representative objection (for picking a canonical issue_key).
    groups: list[Objection] = []
    key_remap: dict[str, str] = {}

    for obj in objections:
        merged = False
        for i, rep in enumerate(groups):
            thresh = threshold
            if min(len(rep.issue_key), len(obj.issue_key)) < 10:
                thresh = max(threshold, 0.92)
            if _ratio(rep.issue_key, obj.issue_key) >= thresh:
                winner = _higher_severity(rep, obj)
                loser = obj if winner is rep else rep
                key_remap[loser.issue_key] = winner.issue_key
                groups[i] = winner
                merged = True
                break
        if not merged:
            groups.append(obj)

    # Rewrite issue_key on all objections based on remap (idempotent for non-merged).
    for obj in objections:
        if obj.issue_key in key_remap:
            obj.issue_key = key_remap[obj.issue_key]

    return list(objections)


# ---------------------------------------------------------------------------
# B3 — reconcile_cross_round
# ---------------------------------------------------------------------------


def reconcile_cross_round(
    new_objections: list[Objection],
    tracker: IssueTracker,
    current_round: int,
    threshold: float = 0.80,
) -> list[Objection]:
    """Reconcile this round's objections against IssueTracker history.

    Scan candidates (ordered: open > partial_resolved > recently_resolved):
      - resolution == "open"
      - resolution == "partial_resolved"
      - resolution == "resolved" AND resolved_at_round >= current_round - 2

    Similarity score: category hard-match gate (+0.3) + target ratio × 0.3
    + description ratio × 0.4. Max w/o category match = 0.70 < 0.80,
    so category mismatch cannot pass threshold by design.

    Match → rewrite obj.issue_key to the matched tracker key.
    Match to resolved → upsert() below will naturally flip to reopened→open.
    """
    if not new_objections:
        return new_objections

    # Build candidate buckets preserving priority.
    open_states = []
    partial_states = []
    recent_resolved_states = []
    for st in tracker.issues.values():
        if st.resolution == "open":
            open_states.append(st)
        elif st.resolution == "partial_resolved":
            partial_states.append(st)
        elif st.resolution == "resolved" and st.resolved_at_round is not None \
                and st.resolved_at_round >= current_round - 2:
            recent_resolved_states.append(st)

    ordered_candidates = open_states + partial_states + recent_resolved_states

    for obj in new_objections:
        best_key: str | None = None
        best_score = 0.0
        for st in ordered_candidates:
            if st.category != obj.category:
                continue  # hard gate
            score = 0.3  # category match
            # R02 MAJOR-1: compare real SECTION_ID via latest_target; fallback to
            # key tail only when tracker has no target recorded (legacy traces).
            target_ref = st.latest_target or (
                st.issue_key.split("::")[-1] if "::" in st.issue_key else st.issue_key
            )
            score += _ratio(target_ref, obj.target) * 0.3
            score += _ratio(st.latest_description, obj.description) * 0.4
            if score >= threshold and score > best_score:
                best_score = score
                best_key = st.issue_key
        if best_key is not None and best_key != obj.issue_key:
            obj.issue_key = best_key

    return new_objections
