"""Unit tests for REFINE protocol components (P0).

Covers Phases A2-F1 plus H1 unit tests. Integration tests live in
tests/test_refine_integration.py (Phases G-I).
"""
from __future__ import annotations

import json

import pytest

from magi.protocols.refine_types import (
    IssueState,
    IssueTracker,
    Objection,
    Reflection,
    RefineConfig,
    RefineRound,
    SEVERITY_ORDER,
    UserAction,
    UserOverride,
)


# ---------------------------------------------------------------------------
# A2 — refine_types defaults + RefineConfig validation
# ---------------------------------------------------------------------------


def test_refine_types_defaults():
    """A2: All dataclasses construct with spec defaults."""
    obj = Objection(
        id="R1-casper-01",
        candidate_key="rate_limit_missing",
        issue_key="rate_limit_missing",
        reviewer="casper",
        category="gap",
        severity="major",
        target="SECTION_1",
        description="No rate limiting specified",
    )
    assert obj.suggestion is None

    refl = Reflection(
        consolidated_id="0",
        source_issue_keys=["rate_limit_missing"],
        verdict="accept",
        reasoning="Addressed in v2",
    )
    assert refl.severity_after is None

    rr = RefineRound(round_num=1)
    assert rr.proposal_text == ""
    assert rr.objections == []
    assert rr.user_overrides is None
    assert rr.collator_failed is False
    assert rr.guided_timeout is False

    ua = UserAction(action="approve")
    assert ua.overrides is None

    uo = UserOverride(issue_key="k", verdict="accept")
    assert uo.severity_after is None

    assert SEVERITY_ORDER == {"minor": 1, "major": 2, "critical": 3}


def test_refine_config_validation_requires_callback_when_guided():
    """A2: guided=True without on_user_review must raise ValueError."""
    # Default config: guided=False, OK.
    cfg = RefineConfig()
    assert cfg.guided is False
    assert cfg.max_rounds == 5
    assert cfg.guided_timeout_policy == "abort"

    # guided=True without callback → error.
    with pytest.raises(ValueError, match="requires on_user_review"):
        RefineConfig(guided=True)

    # guided=True with callback → OK.
    async def cb(round_num, proposal, decisions, issue_summary):
        return UserAction(action="approve")

    cfg2 = RefineConfig(guided=True, on_user_review=cb)
    assert cfg2.on_user_review is cb

    # Invalid timeout policy.
    with pytest.raises(ValueError, match="guided_timeout_policy"):
        RefineConfig(guided_timeout_policy="silent")


# ---------------------------------------------------------------------------
# A3 — IssueTracker state machine
# ---------------------------------------------------------------------------


def test_issue_tracker_upsert():
    """A3: upsert creates new IssueState with round/reviewer bookkeeping."""
    t = IssueTracker()
    t.upsert("k1", round_num=1, reviewer="casper", severity="major",
             category="risk", description="Missing X")
    st = t.issues["k1"]
    assert st.first_raised_round == 1
    assert st.last_raised_round == 1
    assert st.raised_count == 1
    assert st.distinct_reviewers == ["casper"]
    assert st.resolution == "open"
    assert st.severity == "major"
    assert st.latest_description == "Missing X"

    # Upsert by same reviewer does not duplicate in distinct_reviewers.
    t.upsert("k1", round_num=2, reviewer="casper", severity="major",
             category="risk", description="Still missing X")
    assert t.issues["k1"].distinct_reviewers == ["casper"]
    assert t.issues["k1"].raised_count == 2
    assert t.issues["k1"].last_raised_round == 2

    # New reviewer added.
    t.upsert("k1", round_num=2, reviewer="melchior", severity="major",
             category="risk", description="Still missing X")
    assert t.issues["k1"].distinct_reviewers == ["casper", "melchior"]


def test_issue_tracker_reopen():
    """A3: resolved/partial_resolved + upsert → reopened → open transient."""
    t = IssueTracker()
    t.upsert("k1", 1, "casper", severity="major", category="risk")
    t.resolve("k1", "accept", current_round=1)
    assert t.issues["k1"].resolution == "resolved"
    assert t.issues["k1"].resolved_at_round == 1

    # Reopen via upsert.
    t.upsert("k1", 2, "balthasar", severity="major", category="risk")
    st = t.issues["k1"]
    assert st.resolution == "open"  # reopened collapses to open per spec
    assert st.resolved_at_round is None
    assert st.auto_resolved is False

    # partial_resolved + upsert → also reopens.
    t2 = IssueTracker()
    t2.upsert("k2", 1, "casper", severity="major", category="risk")
    t2.resolve("k2", "partial", severity_after="minor")
    assert t2.issues["k2"].resolution == "partial_resolved"
    t2.upsert("k2", 2, "casper", severity="major", category="risk")
    assert t2.issues["k2"].resolution == "open"


def test_active_issues_includes_partial():
    """A3: active_issues returns open + partial_resolved, filtered by min_severity."""
    t = IssueTracker()
    t.upsert("open_major", 1, "casper", severity="major", category="risk")
    t.upsert("partial_major", 1, "casper", severity="major", category="risk")
    t.resolve("partial_major", "partial", severity_after="minor")
    t.upsert("resolved_major", 1, "casper", severity="major", category="risk")
    t.resolve("resolved_major", "accept", current_round=1)
    t.upsert("open_minor", 1, "casper", severity="minor", category="improvement")

    active_all = {s.issue_key for s in t.active_issues(min_severity="minor")}
    assert active_all == {"open_major", "partial_major", "open_minor"}

    active_major = {s.issue_key for s in t.active_issues(min_severity="major")}
    # partial_major was downgraded to minor → excluded at min_severity=major.
    assert active_major == {"open_major"}


def test_severity_upgrade_on_upsert():
    """A3: upsert takes historical max (minor → major upgrades, major stays)."""
    t = IssueTracker()
    t.upsert("k1", 1, "casper", severity="minor", category="improvement")
    assert t.issues["k1"].severity == "minor"
    # Upgrade minor → major.
    t.upsert("k1", 2, "balthasar", severity="major", category="risk")
    assert t.issues["k1"].severity == "major"
    # Do not downgrade major → minor.
    t.upsert("k1", 3, "casper", severity="minor", category="improvement")
    assert t.issues["k1"].severity == "major"
    # Upgrade to critical.
    t.upsert("k1", 4, "melchior", severity="critical", category="error")
    assert t.issues["k1"].severity == "critical"


def test_severity_downgrade_on_partial():
    """A3: resolve(partial, severity_after=...) downgrades severity."""
    t = IssueTracker()
    t.upsert("k1", 1, "casper", severity="critical", category="error")
    assert t.issues["k1"].severity == "critical"
    t.resolve("k1", "partial", severity_after="minor")
    assert t.issues["k1"].resolution == "partial_resolved"
    assert t.issues["k1"].severity == "minor"

    # partial without severity_after → ValueError.
    t.upsert("k2", 1, "casper", severity="major", category="risk")
    with pytest.raises(ValueError, match="severity_after"):
        t.resolve("k2", "partial")


def test_auto_resolve_silent_covers_open_and_partial():
    """A3: auto_resolve_silent flips open AND partial_resolved → resolved with auto_resolved=True."""
    t = IssueTracker()
    t.upsert("open1", 1, "casper", severity="major", category="risk")
    t.upsert("partial1", 1, "casper", severity="major", category="risk")
    t.resolve("partial1", "partial", severity_after="minor")
    t.upsert("resolved1", 1, "casper", severity="major", category="risk")
    t.resolve("resolved1", "accept", current_round=1)

    keys = t.auto_resolve_silent(current_round=3)
    assert set(keys) == {"open1", "partial1"}  # already-resolved not touched
    assert t.issues["open1"].resolution == "resolved"
    assert t.issues["open1"].auto_resolved is True
    assert t.issues["open1"].resolved_at_round == 3
    assert t.issues["partial1"].resolution == "resolved"
    assert t.issues["partial1"].auto_resolved is True
    # Already-resolved untouched (was human-resolved at round 1, not 3).
    assert t.issues["resolved1"].resolved_at_round == 1
    assert t.issues["resolved1"].auto_resolved is False


def test_issue_tracker_to_dict():
    """A3: to_dict produces JSON-safe representation."""
    t = IssueTracker()
    t.upsert("k1", 1, "casper", severity="major", category="risk", description="desc")
    t.resolve("k1", "accept", current_round=1)
    d = t.to_dict()
    assert "k1" in d
    assert d["k1"]["resolution"] == "resolved"
    assert d["k1"]["severity"] == "major"
    assert d["k1"]["resolved_at_round"] == 1


# ---------------------------------------------------------------------------
# B1 — canonicalize_key
# ---------------------------------------------------------------------------


def test_canonicalize_key():
    """B1: normalization lowercases, underscores, strips punctuation, truncates segments."""
    from magi.protocols.refine_keys import canonicalize_key

    assert canonicalize_key("2_Auth_Design::Risk::Token Expiry Missing") == (
        "2_auth_design::risk::token_expiry_missing"
    )
    # Spaces converted.
    assert canonicalize_key("S2 Auth::risk::no_timeout") == "s2_auth::risk::no_timeout"
    # Strips punctuation.
    assert canonicalize_key("S2::risk::token-expiry!") == "s2::risk::tokenexpiry"
    # 40-char truncation per segment.
    long = "a" * 60 + "::risk::" + "b" * 60
    out = canonicalize_key(long)
    segs = out.split("::")
    assert all(len(s) <= 40 for s in segs)


def test_canonicalize_key_empty_fallback():
    """B1: len<3 after normalization returns None (caller uses fallback)."""
    from magi.protocols.refine_keys import canonicalize_key

    assert canonicalize_key("") is None
    assert canonicalize_key("::") is None
    assert canonicalize_key("a!@#") is None
    assert canonicalize_key(None) is None


# ---------------------------------------------------------------------------
# B2 — merge_similar_keys
# ---------------------------------------------------------------------------


def _obj(issue_key: str, severity: str = "minor", category: str = "risk",
         reviewer: str = "casper", target: str = "S1", description: str = "x",
         suggestion: str | None = None) -> Objection:
    return Objection(
        id=f"R1-{reviewer}-01",
        candidate_key=issue_key,
        issue_key=issue_key,
        reviewer=reviewer,
        category=category,
        severity=severity,
        target=target,
        description=description,
        suggestion=suggestion,
    )


def test_merge_similar_keys():
    """B2 (R02 MAJOR-2): similar long keys remap to one canonical key; all objections preserved."""
    from magi.protocols.refine_keys import merge_similar_keys

    objs = [
        _obj("s2_auth::risk::token_expiry_missing", severity="minor"),
        _obj("s2_auth::risk::token_expiry_is_missing", severity="major"),
        _obj("s3_api::gap::rate_limiting_missing", severity="critical"),
    ]
    out = merge_similar_keys(objs, threshold=0.85)
    # All 3 objections preserved (no dedup); first two share the winner's canonical key.
    assert len(out) == 3
    distinct_keys = {o.issue_key for o in out}
    # s2_auth pair collapses to 1 canonical key; s3_api stays distinct.
    assert len(distinct_keys) == 2


def test_merge_similar_keys_short_keys():
    """B2: short keys need stricter threshold — 'abc' and 'abd' should NOT remap."""
    from magi.protocols.refine_keys import merge_similar_keys

    objs = [_obj("abc", severity="minor"), _obj("abd", severity="major")]
    out = merge_similar_keys(objs, threshold=0.85)
    # Short-key tighter threshold keeps these distinct.
    assert len(out) == 2
    assert {o.issue_key for o in out} == {"abc", "abd"}


# ---------------------------------------------------------------------------
# B3 — reconcile_cross_round
# ---------------------------------------------------------------------------


def test_cross_round_reconciliation():
    """B3: weighted similarity match reassigns issue_key to tracker entry."""
    from magi.protocols.refine_keys import reconcile_cross_round

    tracker = IssueTracker()
    tracker.upsert(
        "s2_auth::risk::token_expiry",
        round_num=1,
        reviewer="casper",
        severity="major",
        category="risk",
        description="No token expiry enforcement in session middleware",
    )
    # New objection same category + near-identical description + matching target.
    new_objs = [_obj(
        "s2_auth::risk::tokens_never_expire",
        severity="major",
        category="risk",
        target="token_expiry",  # target string close to tracker key tail
        description="No token expiry enforcement in session middleware layer",
    )]
    out = reconcile_cross_round(new_objs, tracker, current_round=2, threshold=0.80)
    assert out[0].issue_key == "s2_auth::risk::token_expiry"


def test_cross_round_category_hard_match():
    """B3: category mismatch cannot pass threshold even with identical target+description."""
    from magi.protocols.refine_keys import reconcile_cross_round

    tracker = IssueTracker()
    tracker.upsert(
        "s2_auth::risk::token_expiry",
        round_num=1,
        reviewer="casper",
        severity="major",
        category="risk",
        description="exactly matching description",
    )
    new_objs = [_obj(
        "new_key",
        severity="major",
        category="error",  # category differs
        target="token_expiry",
        description="exactly matching description",
    )]
    out = reconcile_cross_round(new_objs, tracker, current_round=2)
    # Hard-gated: issue_key unchanged.
    assert out[0].issue_key == "new_key"


def test_cross_round_reopen_resolved():
    """B3: match against recently-resolved key rewrites issue_key (upsert flips to open)."""
    from magi.protocols.refine_keys import reconcile_cross_round

    tracker = IssueTracker()
    tracker.upsert(
        "s2_auth::risk::token_expiry",
        round_num=1,
        reviewer="casper",
        severity="major",
        category="risk",
        description="No token expiry",
    )
    tracker.resolve("s2_auth::risk::token_expiry", "accept", current_round=1)
    assert tracker.issues["s2_auth::risk::token_expiry"].resolution == "resolved"

    # At round 2 (within current_round-2 window), reconcile should still find it.
    new_objs = [_obj(
        "other_key",
        severity="major",
        category="risk",
        target="token_expiry",
        description="No token expiry",
    )]
    out = reconcile_cross_round(new_objs, tracker, current_round=2, threshold=0.80)
    assert out[0].issue_key == "s2_auth::risk::token_expiry"


def test_cross_round_skip_old_resolved():
    """B3: resolved > 2 rounds ago is skipped (out of reconcile window)."""
    from magi.protocols.refine_keys import reconcile_cross_round

    tracker = IssueTracker()
    tracker.upsert(
        "s2_auth::risk::token_expiry",
        round_num=1,
        reviewer="casper",
        severity="major",
        category="risk",
        description="No token expiry",
    )
    tracker.resolve("s2_auth::risk::token_expiry", "accept", current_round=1)

    # current_round=5 means reconcile window is [3, 5]. Resolved at 1 is too old.
    new_objs = [_obj(
        "other_key",
        severity="major",
        category="risk",
        target="token_expiry",
        description="No token expiry",
    )]
    out = reconcile_cross_round(new_objs, tracker, current_round=5, threshold=0.80)
    assert out[0].issue_key == "other_key"


# ---------------------------------------------------------------------------
# E1 — TraceLogger.log_round
# ---------------------------------------------------------------------------


def test_log_round_writes_to_refine_subdir(tmp_path):
    """E1: log_round appends JSONL under {trace_dir}/refine/{trace_id}.jsonl."""
    import json as _json
    from magi.trace.logger import TraceLogger

    logger = TraceLogger(str(tmp_path))
    round_data = {"round_num": 1, "active_issues": 3, "cost_usd": 0.012}
    logger.log_round("abcd1234", round_data)

    path = tmp_path / "refine" / "abcd1234.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert _json.loads(lines[0]) == round_data

    # Subsequent calls append.
    logger.log_round("abcd1234", {"round_num": 2, "active_issues": 1})
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


def test_log_round_preserves_existing_log_method(tmp_path):
    """E1: log_round is additive — existing TraceLogger.log() still functions."""
    from magi.core.decision import Decision
    from magi.trace.logger import TraceLogger

    logger = TraceLogger(str(tmp_path))
    d = Decision(query="q", ruling="r", confidence=0.9, minority_report="", votes={})
    logger.log(d)
    # Existing behavior: writes to {date}.jsonl at trace_dir root.
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert len(jsonl_files) == 1

    # log_round writes elsewhere.
    logger.log_round("xyz12345", {"round_num": 1})
    refine_file = tmp_path / "refine" / "xyz12345.jsonl"
    assert refine_file.exists()


# ---------------------------------------------------------------------------
# F1 — Prompt templates
# ---------------------------------------------------------------------------


def test_reviewer_prompt_contains_decisions_summary_round2():
    """F1: round > 1 reviewer prompt includes decisions_summary + resolved/unresolved sections."""
    from magi.protocols.refine_prompts import build_reviewer

    p1 = build_reviewer(
        query="Design a rate limiter",
        primary_node="melchior",
        round_num=1,
        proposal_or_diff="S1: proposal...",
    )
    assert "Primary's decisions from last round" not in p1

    p2 = build_reviewer(
        query="Design a rate limiter",
        primary_node="melchior",
        round_num=2,
        proposal_or_diff="S1: revised proposal",
        decisions_summary="- [accept] s2::risk::foo: \"fixed\"",
        resolved_issues_summary="s2::risk::foo",
        unresolved_issues_summary="s3::gap::bar",
    )
    assert "Primary's decisions from last round" in p2
    assert "s2::risk::foo" in p2
    assert "Previously resolved" in p2
    assert "Still unresolved" in p2
    # SYSTEM_INSTRUCTION / UNTRUSTED_CONTENT isolation tags present.
    assert "<SYSTEM_INSTRUCTION" in p2
    assert "<UNTRUSTED_CONTENT" in p2


def test_reflection_prompt_uses_consolidated_schema():
    """F1: reflection prompt asks for consolidated_id + source_issue_keys + chosen_suggestion (R8-2)."""
    from magi.protocols.refine_prompts import build_primary_reflection

    p = build_primary_reflection(
        round_num=2,
        collated_suggestions=[{"issue_key": "k1", "description": "d"}],
        current_proposal="S1: ...",
    )
    assert "consolidated_id" in p
    assert "source_issue_keys" in p
    assert "chosen_suggestion" in p
    assert "conflicting_suggestions" in p
    assert "REVISED_PROPOSAL:" in p
    assert "<SYSTEM_INSTRUCTION" in p


# ---------------------------------------------------------------------------
# C1 — check_convergence (5 tests; T3)
# ---------------------------------------------------------------------------


def _pop_tracker(*entries) -> IssueTracker:
    """Helper: make an IssueTracker with entries (key, severity, resolution)."""
    t = IssueTracker()
    for key, severity, resolution in entries:
        t.upsert(key, round_num=1, reviewer="casper", severity=severity, category="risk")
        if resolution == "resolved":
            t.resolve(key, "accept", current_round=1)
        elif resolution == "partial_resolved":
            t.resolve(key, "partial", severity_after=severity)


    return t


def test_convergence_all_resolved():
    """C1: active==0 → converged regardless of other signals."""
    from magi.protocols.refine_convergence import check_convergence

    t = _pop_tracker(("k1", "major", "resolved"), ("k2", "minor", "resolved"))
    converged, status = check_convergence(
        tracker=t, threshold=0, current_round=3, max_rounds=5,
        round_objections=[], round_parse_errors=[], successful_reviewer_names=["casper"],
    )
    assert converged is True
    assert status == "converged"


def test_convergence_threshold():
    """C1: all active minor + count <= threshold → threshold.

    Silence rule has higher priority than threshold; we therefore include a
    reviewer-raised objection on an unrelated key so the silence path does
    not auto-resolve everything before THRESHOLD evaluates.
    """
    from magi.protocols.refine_convergence import check_convergence

    t = _pop_tracker(("k1", "minor", "open"), ("k2", "minor", "open"))
    # Active reviewer objection this round keeps silence rule off.
    live_obj = _obj("other_live_key", severity="minor", reviewer="casper")
    converged, status = check_convergence(
        tracker=t, threshold=2, current_round=3, max_rounds=5,
        round_objections=[live_obj],
        round_parse_errors=[],
        successful_reviewer_names=["casper"],
    )
    assert converged is True
    assert status == "threshold"

    # Same but threshold=1 → not converged (count 2 > 1).
    t2 = _pop_tracker(("k1", "minor", "open"), ("k2", "minor", "open"))
    live_obj2 = _obj("other_live_key", severity="minor", reviewer="casper")
    converged2, status2 = check_convergence(
        tracker=t2, threshold=1, current_round=3, max_rounds=5,
        round_objections=[live_obj2],
        round_parse_errors=[],
        successful_reviewer_names=["casper"],
    )
    assert converged2 is False


def test_convergence_no_new_objections():
    """C1: successful reviewers silent → auto-resolve → converged."""
    from magi.protocols.refine_convergence import check_convergence

    t = _pop_tracker(("k1", "major", "open"), ("k2", "minor", "open"))
    converged, status = check_convergence(
        tracker=t, threshold=0, current_round=3, max_rounds=5,
        round_objections=[],
        round_parse_errors=[],
        successful_reviewer_names=["casper", "balthasar"],
    )
    assert converged is True
    assert status == "converged"
    # All active issues flipped to resolved with auto_resolved=True.
    for st in t.issues.values():
        assert st.resolution == "resolved"
        assert st.auto_resolved is True


def test_convergence_partial_not_resolved():
    """C1: partial_resolved issues still count as active (don't auto-converge at ALL_RESOLVED)."""
    from magi.protocols.refine_convergence import check_convergence

    t = _pop_tracker(("k1", "major", "partial_resolved"))
    # No reviewers silent this round, so auto-resolve silence rule doesn't fire.
    obj = _obj("other", severity="major", reviewer="casper")
    converged, status = check_convergence(
        tracker=t, threshold=0, current_round=2, max_rounds=5,
        round_objections=[obj],  # reviewer raised something
        round_parse_errors=[],
        successful_reviewer_names=["casper"],
    )
    # Should NOT converge at ALL_RESOLVED (still active), nor THRESHOLD (not minor),
    # nor silence (reviewer raised). Current_round < max_rounds.
    assert converged is False


def test_silence_with_parse_error_no_converge():
    """C1 / R8-1: len(successful_reviewer_names) == 0 disables silence rule."""
    from magi.protocols.refine_convergence import check_convergence

    t = _pop_tracker(("k1", "major", "open"))
    # All reviewers had parse errors — no successful reviewer, no silence.
    converged, status = check_convergence(
        tracker=t, threshold=0, current_round=2, max_rounds=5,
        round_objections=[],
        round_parse_errors=["casper", "balthasar"],
        successful_reviewer_names=[],
    )
    assert converged is False
    # Not auto-resolved.
    assert t.issues["k1"].resolution == "open"


# ---------------------------------------------------------------------------
# C2 — track_best_round
# ---------------------------------------------------------------------------


def test_best_round_tracking():
    """C2: picks the round with highest score (fewer active issues)."""
    from magi.protocols.refine_convergence import track_best_round

    t = _pop_tracker(("k1", "major", "open"))
    # Round 1: k1 open (no resolves).
    r1 = RefineRound(round_num=1, issue_snapshot={"k1": "open"})
    # Round 2: k1 resolved.
    r2 = RefineRound(round_num=2, issue_snapshot={"k1": "resolved"})
    # Round 3: k1 reopened via upsert + k2 open.
    r3 = RefineRound(round_num=3, issue_snapshot={"k1": "open", "k2": "open"})

    result = track_best_round([r1, r2, r3], t)
    assert result["best_round"] == 2
    assert result["best_round_note"] is not None
    assert "Round 2" in result["best_round_note"]


# ---------------------------------------------------------------------------
# C3 — check_sycophancy
# ---------------------------------------------------------------------------


def test_sycophancy_detection():
    """C3: 2 consecutive rounds with accept_rate == 1.0 → True."""
    from magi.protocols.refine_convergence import check_sycophancy

    rounds = [
        RefineRound(round_num=1, accept_rate=0.8),
        RefineRound(round_num=2, accept_rate=1.0),
        RefineRound(round_num=3, accept_rate=1.0),
    ]
    assert check_sycophancy(rounds) is True

    rounds2 = [
        RefineRound(round_num=1, accept_rate=1.0),
        RefineRound(round_num=2, accept_rate=0.7),
    ]
    assert check_sycophancy(rounds2) is False

    # Only 1 round: cannot detect.
    assert check_sycophancy([RefineRound(round_num=1, accept_rate=1.0)]) is False


# ---------------------------------------------------------------------------
# C4 — compute_refine_confidence / votes / minority_report (T12)
# ---------------------------------------------------------------------------


def test_compute_confidence_with_partial():
    """C4: confidence formula uses active_issues (includes partial_resolved)."""
    from magi.protocols.refine_convergence import compute_refine_confidence

    t = _pop_tracker(("c1", "critical", "open"), ("m1", "major", "partial_resolved"))
    # active: 1 critical + 1 major-partial → confidence 1.0 - 0.10 - 0.05 = 0.85
    c = compute_refine_confidence(t, max_rounds_hit=False, degraded=False)
    assert abs(c - 0.85) < 1e-6

    # With max_rounds and degraded: -0.15 -0.10 more → 0.60
    c2 = compute_refine_confidence(t, max_rounds_hit=True, degraded=True)
    assert abs(c2 - 0.60) < 1e-6

    # Clamp floor at 0.1.
    t2 = _pop_tracker(*[(f"c{i}", "critical", "open") for i in range(20)])
    c3 = compute_refine_confidence(t2, max_rounds_hit=True, degraded=True)
    assert c3 == 0.1


def test_compute_votes_by_last_round():
    """C4: votes use last-round objections; silent reviewer gets ruling (approve)."""
    from magi.protocols.refine_convergence import compute_refine_votes

    class _N:
        def __init__(self, name): self.name = name

    primary, r1, r2 = _N("melchior"), _N("casper"), _N("balthasar")
    objs = [_obj("k1", severity="major", reviewer="casper")]
    votes = compute_refine_votes(
        primary_node_name="melchior",
        reviewer_nodes=[primary, r1, r2],
        last_round_objections=objs,
        last_round_parse_errors=[],
        ruling="FINAL RULING",
    )
    assert votes["melchior"] == "FINAL RULING"
    assert votes["balthasar"] == "FINAL RULING"  # silent → approve
    assert "DISSENT" in votes["casper"]
    assert "k1" in votes["casper"]


def test_parse_error_reviewer_not_counted_as_approve():
    """C4 / R8-4 / R9 #1: parse-error reviewer emits 'PARSE_ERROR' string."""
    from magi.protocols.refine_convergence import compute_refine_votes

    class _N:
        def __init__(self, name): self.name = name

    primary, r1 = _N("melchior"), _N("casper")
    votes = compute_refine_votes(
        primary_node_name="melchior",
        reviewer_nodes=[primary, r1],
        last_round_objections=[],  # no objections because reviewer couldn't parse
        last_round_parse_errors=["casper"],
        ruling="FINAL",
    )
    assert votes["casper"] == "PARSE_ERROR"
    assert votes["melchior"] == "FINAL"


def test_minority_report_by_last_round():
    """C4: minority_report built from last-round objection list only."""
    from magi.protocols.refine_convergence import compute_refine_minority_report

    objs = [
        _obj("k1", severity="major", reviewer="casper", description="desc A"),
        _obj("k2", severity="minor", reviewer="casper", description="desc B"),
    ]
    report = compute_refine_minority_report(objs, reviewer_nodes=[])
    assert "**casper**" in report
    assert "k1 (major): desc A" in report
    assert "k2 (minor): desc B" in report

    # Empty objections → empty string (UI skips rendering).
    assert compute_refine_minority_report([], reviewer_nodes=[]) == ""


# ---------------------------------------------------------------------------
# D1 — Collator LLM call (T6, T14)
# ---------------------------------------------------------------------------


def _collator_response(payload: list[dict], *, cost: float = 0.01):
    """Build a stubbed litellm response object."""
    class _Msg:
        def __init__(self, content):
            self.content = content
    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)
    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]
    return _Resp("```json\n" + json.dumps(payload) + "\n```"), cost


@pytest.mark.asyncio
async def test_collator_dedup(monkeypatch):
    """D1 / T14: collator output is passed through untouched; cost recorded."""
    import json as _json
    from magi.protocols.refine_types import RefineConfig
    from magi.protocols import refine_collator

    calls = []

    class _Msg:
        def __init__(self, content): self.content = content
    class _Choice:
        def __init__(self, msg): self.message = msg
    class _Resp:
        def __init__(self, msg): self.choices = [_Choice(msg)]

    async def _fake_acomp(**kwargs):
        calls.append(kwargs)
        payload = [{"issue_key": "merged_k", "category": "risk", "severity": "major",
                    "target": "S1", "description": "merged",
                    "suggestions": [{"reviewer": "casper", "text": "fix"},
                                    {"reviewer": "balthasar", "text": "fix"}],
                    "conflicting_suggestions": False,
                    "source_reviewers": ["casper", "balthasar"],
                    "source_issue_keys": ["k1", "k2"]}]
        return _Resp(_Msg("```json\n" + _json.dumps(payload) + "\n```"))

    monkeypatch.setattr(refine_collator.litellm, "acompletion", _fake_acomp)
    monkeypatch.setattr(refine_collator.litellm, "completion_cost",
                        lambda completion_response: 0.02)

    class _Node:
        def __init__(self, name, model):
            self.name = name
            self.model = model

    reviewers = [_Node("casper", "gpt-4"), _Node("balthasar", "gpt-4")]
    objs = [_obj("k1", reviewer="casper"), _obj("k2", reviewer="balthasar")]

    cfg = RefineConfig()
    consolidated, cost, failed = await refine_collator.collate_objections(
        objs, round_num=1, config=cfg, reviewer_nodes=reviewers,
    )
    assert failed is False
    assert len(consolidated) == 1
    assert consolidated[0]["source_issue_keys"] == ["k1", "k2"]
    assert cost == 0.02
    # Should use reviewer[0].model since collator_model unset.
    assert calls[0]["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_collator_no_drop(monkeypatch):
    """D1: collator MUST NOT drop unique objections."""
    from magi.protocols.refine_types import RefineConfig
    from magi.protocols import refine_collator

    class _Msg:
        def __init__(self, content): self.content = content
    class _Choice:
        def __init__(self, msg): self.message = msg
    class _Resp:
        def __init__(self, msg): self.choices = [_Choice(msg)]

    # Collator returns 3 entries preserving all inputs.
    async def _fake_acomp(**kwargs):
        entries = [
            {"issue_key": f"k{i}", "category": "risk", "severity": "major",
             "target": "S1", "description": f"d{i}",
             "suggestions": [{"reviewer": f"r{i}", "text": "x"}],
             "conflicting_suggestions": False,
             "source_reviewers": [f"r{i}"],
             "source_issue_keys": [f"k{i}"]}
            for i in range(3)
        ]
        return _Resp(_Msg("```json\n" + json.dumps(entries) + "\n```"))

    monkeypatch.setattr(refine_collator.litellm, "acompletion", _fake_acomp)
    monkeypatch.setattr(refine_collator.litellm, "completion_cost", lambda **k: 0.01)

    class _N:
        def __init__(self, n, m): self.name, self.model = n, m

    objs = [_obj(f"k{i}", reviewer=f"r{i}") for i in range(3)]
    out, _, failed = await refine_collator.collate_objections(
        objs, round_num=1, config=RefineConfig(), reviewer_nodes=[_N("r0", "m")],
    )
    assert failed is False
    assert {e["issue_key"] for e in out} == {"k0", "k1", "k2"}


@pytest.mark.asyncio
async def test_collator_preserves_conflicting_suggestions(monkeypatch):
    """D1: conflicting_suggestions=true + multi-item suggestions survive the call."""
    from magi.protocols.refine_types import RefineConfig
    from magi.protocols import refine_collator

    class _Msg:
        def __init__(self, content): self.content = content
    class _Choice:
        def __init__(self, msg): self.message = msg
    class _Resp:
        def __init__(self, msg): self.choices = [_Choice(msg)]

    async def _fake_acomp(**kwargs):
        entries = [{
            "issue_key": "k1", "category": "risk", "severity": "major",
            "target": "S1", "description": "d",
            "suggestions": [
                {"reviewer": "casper", "text": "fix via A"},
                {"reviewer": "balthasar", "text": "fix via B"},
            ],
            "conflicting_suggestions": True,
            "source_reviewers": ["casper", "balthasar"],
            "source_issue_keys": ["k1"],
        }]
        return _Resp(_Msg("```json\n" + json.dumps(entries) + "\n```"))

    monkeypatch.setattr(refine_collator.litellm, "acompletion", _fake_acomp)
    monkeypatch.setattr(refine_collator.litellm, "completion_cost", lambda **k: 0.0)

    class _N:
        def __init__(self, n, m): self.name, self.model = n, m

    out, _, _ = await refine_collator.collate_objections(
        [_obj("k1", reviewer="casper")], round_num=1,
        config=RefineConfig(), reviewer_nodes=[_N("r0", "m")],
    )
    assert out[0]["conflicting_suggestions"] is True
    assert len(out[0]["suggestions"]) == 2


@pytest.mark.asyncio
async def test_collator_cli_node_skip(monkeypatch):
    """D1 / T6: CLI node reviewer skips Collator and walks fallback path."""
    from magi.protocols.refine_types import RefineConfig
    from magi.protocols import refine_collator
    from magi.core.cli_node import CliNode

    acomp_called = {"n": 0}

    async def _should_not_be_called(**k):
        acomp_called["n"] += 1
        raise AssertionError("litellm.acompletion should NOT run for CLI node reviewers")

    monkeypatch.setattr(refine_collator.litellm, "acompletion", _should_not_be_called)

    # Create a minimal CliNode-looking instance bypassing __init__.
    cli = CliNode.__new__(CliNode)
    cli.name = "casper"
    cli.model = "Claude CLI (sonnet-4.6)"

    objs = [_obj("k1", reviewer="casper"), _obj("k2", reviewer="casper")]
    out, cost, failed = await refine_collator.collate_objections(
        objs, round_num=1, config=RefineConfig(), reviewer_nodes=[cli],
    )
    assert failed is True  # fallback used
    assert cost == 0.0
    assert acomp_called["n"] == 0
    assert len(out) == 2  # each raw objection wrapped
    # Fallback shape matches ConsolidatedObjection schema.
    assert all("source_issue_keys" in e for e in out)
    assert all(e["conflicting_suggestions"] is False for e in out)


# ---------------------------------------------------------------------------
# D2 — fallback_consolidate (T4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collator_failure_fallback_normalized_schema(monkeypatch):
    """D2 / T4: two collator failures → fallback emits normalized schema."""
    from magi.protocols.refine_types import RefineConfig
    from magi.protocols import refine_collator

    call_count = {"n": 0}

    async def _always_fail(**k):
        call_count["n"] += 1
        raise RuntimeError("simulated collator failure")

    monkeypatch.setattr(refine_collator.litellm, "acompletion", _always_fail)
    monkeypatch.setattr(refine_collator.litellm, "completion_cost", lambda **k: 0.0)

    class _N:
        def __init__(self, n, m): self.name, self.model = n, m

    objs = [_obj("k1", reviewer="casper", suggestion="do X")]
    out, cost, failed = await refine_collator.collate_objections(
        objs, round_num=1, config=RefineConfig(), reviewer_nodes=[_N("casper", "gpt-4")],
    )
    assert call_count["n"] == 2  # first + retry with schema hint
    assert failed is True
    assert len(out) == 1
    entry = out[0]
    assert entry["issue_key"] == "k1"
    assert entry["conflicting_suggestions"] is False
    assert entry["source_reviewers"] == ["casper"]
    assert entry["source_issue_keys"] == ["k1"]
    assert entry["suggestions"] == [{"reviewer": "casper", "text": "do X"}]


# ---------------------------------------------------------------------------
# R02 regression tests (Round 02 MAJOR + MINOR fixes)
# ---------------------------------------------------------------------------


def test_reconcile_cross_round_uses_real_section_id():
    """R02 MAJOR-1: reconcile compares against IssueState.latest_target (real SECTION_ID),
    not the issue_key tail — so different canonical keys still collapse when they share
    a SECTION_ID target + near-identical description + category.
    """
    from magi.protocols.refine_keys import reconcile_cross_round

    tracker = IssueTracker()
    tracker.upsert(
        "authoring_section::error::api_contract_drift",
        round_num=1,
        reviewer="casper",
        severity="major",
        category="error",
        description="API response shape does not match documented contract",
        target="S2.1",  # real SECTION_ID
    )
    # New objection has a completely different canonical key but points at the
    # same SECTION_ID and describes the same issue.
    new_objs = [_obj(
        "auth_layer::error::response_mismatch_in_endpoint",
        severity="major",
        category="error",
        target="S2.1",  # matches tracker.latest_target
        description="API response shape does not match documented contract text",
    )]
    out = reconcile_cross_round(new_objs, tracker, current_round=2, threshold=0.80)
    # Reconcile rewrote the new objection's issue_key to the existing tracker entry.
    assert out[0].issue_key == "authoring_section::error::api_contract_drift"


def test_merge_similar_keys_preserves_multi_reviewer_suggestions():
    """R02 MAJOR-2: merge_similar_keys must NOT dedup by key — it only remaps
    similar keys onto a canonical one. All original objections stay in the list
    so the collator can preserve every reviewer's suggestion.
    """
    from magi.protocols.refine_keys import merge_similar_keys

    # Three reviewers flag the same underlying issue with near-identical keys.
    objs = [
        _obj("authoring_section::error::api_contract_drift",
             severity="major", category="error", reviewer="casper",
             suggestion="fix via casper's approach"),
        _obj("authoring_section::error::api_contract_drift",
             severity="major", category="error", reviewer="balthasar",
             suggestion="fix via balthasar's approach"),
        _obj("authoring_section::error::api_contract_drift",
             severity="major", category="error", reviewer="melchior",
             suggestion="fix via melchior's approach"),
    ]
    out = merge_similar_keys(objs, threshold=0.85)
    # All 3 preserved (the pre-R02 implementation would have returned 1).
    assert len(out) == 3
    reviewers = {o.reviewer for o in out}
    assert reviewers == {"casper", "balthasar", "melchior"}


def test_best_round_after_partial_downgrade():
    """R02 MAJOR-6: track_best_round must use per-round issue_severity_snapshot
    rather than the final tracker state, because partial_resolved can downgrade
    severity in later rounds and distort the scoring of earlier rounds.

    Round 2 resolved the issue partially at minor severity; round 3 reopened it
    at critical and added a new minor issue. Round 2 should win because its
    snapshot shows one minor active issue vs round 3's one critical + one minor.
    """
    from magi.protocols.refine_convergence import track_best_round

    r1 = RefineRound(
        round_num=1,
        issue_snapshot={"authoring_section::error::api_drift": "open"},
        issue_severity_snapshot={"authoring_section::error::api_drift": "critical"},
    )
    r2 = RefineRound(
        round_num=2,
        issue_snapshot={"authoring_section::error::api_drift": "partial_resolved"},
        issue_severity_snapshot={"authoring_section::error::api_drift": "minor"},
    )
    r3 = RefineRound(
        round_num=3,
        issue_snapshot={
            "authoring_section::error::api_drift": "open",
            "database_layer::gap::fk_missing": "open",
        },
        issue_severity_snapshot={
            "authoring_section::error::api_drift": "critical",
            "database_layer::gap::fk_missing": "minor",
        },
    )

    # Final tracker state mimics the end-of-run: api_drift critical + fk minor,
    # both active. Without per-round snapshots, round 2 would be scored using
    # the critical severity and lose to round 1.
    tracker = IssueTracker()
    tracker.upsert("authoring_section::error::api_drift", 1, "casper",
                   severity="critical", category="error")
    tracker.upsert("database_layer::gap::fk_missing", 3, "casper",
                   severity="minor", category="gap")

    result = track_best_round([r1, r2, r3], tracker)
    assert result["best_round"] == 2


def test_reviewer_prompt_wraps_prior_summaries_in_untrusted():
    """R02 MINOR-1: round > 1 reviewer prompt wraps prior-round summaries inside
    <UNTRUSTED_CONTENT type="prior_round_summary"> so primary reasoning cannot
    inject instructions. Round 1 has no prior summary — tag must not appear.
    """
    from magi.protocols.refine_prompts import build_reviewer

    p2 = build_reviewer(
        query="Design X",
        primary_node="melchior",
        round_num=2,
        proposal_or_diff="S1: revised",
        decisions_summary="- [accept] k: \"fixed\"",
        resolved_issues_summary="k",
        unresolved_issues_summary="(none)",
    )
    assert '<UNTRUSTED_CONTENT type="prior_round_summary"' in p2

    p1 = build_reviewer(
        query="Design X",
        primary_node="melchior",
        round_num=1,
        proposal_or_diff="S1: initial",
    )
    assert "prior_round_summary" not in p1


def test_dashboard_has_refine_option():
    """R02 MAJOR-7 (dashboard): mode selector must include value="refine"."""
    from pathlib import Path

    html = Path("magi/web/static/index.html").read_text(encoding="utf-8")
    assert 'value="refine"' in html
    # Appears after value="critique" inside the mode <select>.
    critique_idx = html.find('value="critique"')
    refine_idx = html.find('value="refine"')
    assert critique_idx != -1 and refine_idx != -1
    assert refine_idx > critique_idx


def test_dashboard_final_ruling_warning_class_css_present():
    """R02 MAJOR-7 / R03 MAJOR-A: .final-warning CSS class exists and finalRuling
    logic uses mapVoteToLamp → counts 'warning' lamp to pick the warning style.
    """
    from pathlib import Path

    html = Path("magi/web/static/index.html").read_text(encoding="utf-8")
    assert "final-warning" in html
    assert "mapVoteToLamp" in html
    assert "warningCount" in html
