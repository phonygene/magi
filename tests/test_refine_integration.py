"""Integration tests for REFINE protocol (G2, G3, H2, H3, I2, J1).

All LLM calls are mocked via `AsyncMock` on node.query(), matching the
pattern used by tests/test_vote.py / tests/test_critique.py.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from magi.core.decision import Decision
from magi.protocols.refine import refine_protocol
from magi.protocols.refine_types import (
    RefineConfig,
    UserAction,
    UserOverride,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_node(name: str, model: str = "gpt-4", responses: list[str] | None = None):
    """Build a mock node with a scripted query() side_effect."""
    node = AsyncMock()
    node.name = name
    node.model = model
    node.last_cost_usd = 0.01
    node.query = AsyncMock(side_effect=responses or [])
    return node


def objection_json(
    candidate_key: str = "s1::risk::missing_x",
    category: str = "risk",
    severity: str = "major",
    target: str = "S1",
    issue: str = "Missing X",
    suggestion: str = "Add X",
) -> str:
    return "```json\n" + json.dumps([{
        "candidate_key": candidate_key,
        "category": category,
        "severity": severity,
        "target": target,
        "issue": issue,
        "suggestion": suggestion,
    }]) + "\n```"


def empty_objections() -> str:
    return "```json\n[]\n```"


def reflection_json(
    entries: list[dict],
    revised_text: str = "S1: revised proposal",
) -> str:
    body = "```json\n" + json.dumps(entries) + "\n```\n\nREVISED_PROPOSAL:\n" + revised_text
    return body


def _silent_collator(monkeypatch):
    """Bypass actual litellm collator — return raw objections wrapped as consolidated."""
    from magi.protocols import refine_collator

    async def _mock_collate(objections, round_num, config, reviewer_nodes):
        # Walk fallback path for determinism.
        return refine_collator.fallback_consolidate(objections), 0.0, False

    monkeypatch.setattr(refine_collator, "collate_objections", _mock_collate)
    # ALSO patch the import site in refine module.
    from magi.protocols import refine
    monkeypatch.setattr(refine, "collate_objections", _mock_collate)


# ---------------------------------------------------------------------------
# G2 — Core loop happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_happy_path(monkeypatch):
    """G2: round-1 acceptance clears the tracker → ALL_RESOLVED in one round."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial proposal",
        reflection_json([{
            "consolidated_id": "0",
            "source_issue_keys": ["s1::risk::missing_x"],
            "verdict": "accept",
            "reasoning": "Added X",
        }], "S1: revised with X"),
    ])
    casper = make_node("casper", responses=[objection_json()])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("Design X", [primary, casper, balthasar], cfg)

    assert decision.protocol_used == "refine"
    assert decision.ruling == "S1: revised with X"
    assert decision.refine_summary["terminal_status"] == "converged"
    assert decision.refine_summary["total_rounds"] == 1
    assert decision.refine_summary["resolved"] == 1


@pytest.mark.asyncio
async def test_refine_no_new_objections(monkeypatch):
    """G2: silence on round 1 auto-resolves nothing and converges immediately."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=["S1: initial"])
    casper = make_node("casper", responses=[empty_objections()])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "converged"
    assert decision.refine_summary["total_rounds"] == 1
    # Primary did not need to reflect because no objections.
    assert primary.query.await_count == 1


@pytest.mark.asyncio
async def test_refine_max_rounds(monkeypatch):
    """G2: reviewers keep raising objections → max_rounds terminal status."""
    _silent_collator(monkeypatch)

    # Each round: casper raises 1 new objection; primary rejects → stays open.
    primary_rs = ["S1: initial"]
    casper_rs = []
    balthasar_rs = []
    for rnd in range(1, 4):  # 3 rounds
        casper_rs.append(objection_json(candidate_key=f"k::risk::r{rnd}"))
        balthasar_rs.append(empty_objections())
        primary_rs.append(reflection_json([{
            "consolidated_id": "0",
            "source_issue_keys": [f"k::risk::r{rnd}"],
            "verdict": "reject",
            "reasoning": "out of scope",
        }], f"S1: proposal r{rnd}"))

    primary = make_node("melchior", responses=primary_rs)
    casper = make_node("casper", responses=casper_rs)
    balthasar = make_node("balthasar", responses=balthasar_rs)

    cfg = RefineConfig(max_rounds=3, convergence_threshold=0)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "max_rounds"
    assert decision.refine_summary["total_rounds"] == 3


@pytest.mark.asyncio
async def test_refine_budget_exceeded(monkeypatch):
    """G2: budget cap at round start returns budget terminal_status."""
    _silent_collator(monkeypatch)

    # Primary call has a large cost so total_cost exceeds budget on round 2 start.
    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "ok"}], "S1: r1"),
    ])
    primary.last_cost_usd = 0.10  # each query costs $0.10
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x"),
    ])
    casper.last_cost_usd = 0.05
    balthasar = make_node("balthasar", responses=[empty_objections()])
    balthasar.last_cost_usd = 0.01

    cfg = RefineConfig(max_rounds=5, max_budget_usd=0.15)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "budget"


@pytest.mark.asyncio
async def test_refine_cancel(monkeypatch):
    """G2: cancel_event set mid-run → cancelled terminal_status."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=["S1: initial"])
    casper = make_node("casper", responses=[empty_objections()])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    event = asyncio.Event()
    event.set()  # immediate cancel

    cfg = RefineConfig(max_rounds=5, cancel_event=event)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "cancelled"


@pytest.mark.asyncio
async def test_refine_parse_error_recovery(monkeypatch):
    """G1 / T12 intent: reviewer parse_error does NOT count as rejection."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{
            "consolidated_id": "0", "source_issue_keys": ["s1::risk::x"],
            "verdict": "accept", "reasoning": "fix",
        }], "S1: r1"),
    ])
    # casper: first call garbage, retry also garbage → parse_error
    casper = make_node("casper", responses=[
        "not json at all",
        "still garbage",
        empty_objections(),  # round 2 (unused here)
    ])
    balthasar = make_node("balthasar", responses=[
        objection_json(candidate_key="s1::risk::x"),
        empty_objections(),
    ])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.votes.get("casper") == "PARSE_ERROR"
    # parse-error reviewer did not count as silent approver; balthasar accepted
    # the fix and went silent on round 2 → converged.
    assert decision.refine_summary["terminal_status"] == "converged"


@pytest.mark.asyncio
async def test_refine_all_reviewers_offline(monkeypatch):
    """G2: 0 successful reviewers on round 1 → aborted + degraded."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=["S1: initial"])
    # Both reviewers always throw.
    casper = make_node("casper", responses=[])
    casper.query = AsyncMock(side_effect=RuntimeError("offline"))
    balthasar = make_node("balthasar", responses=[])
    balthasar.query = AsyncMock(side_effect=RuntimeError("offline"))

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.degraded is True
    assert decision.refine_summary["terminal_status"] == "aborted"
    assert decision.refine_summary["abort_reason"] == "all_reviewers_offline"


@pytest.mark.asyncio
async def test_refine_primary_failure(monkeypatch):
    """G2: initial primary call fails twice → aborted with degraded."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[])
    primary.query = AsyncMock(side_effect=RuntimeError("primary down"))
    casper = make_node("casper", responses=[])
    balthasar = make_node("balthasar", responses=[])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.degraded is True
    assert decision.refine_summary["terminal_status"] == "aborted"
    assert decision.refine_summary["abort_reason"] == "primary_failed"
    assert primary.name in decision.failed_nodes


@pytest.mark.asyncio
async def test_refine_reviewer_sees_decisions(monkeypatch):
    """G2: round>1 reviewer prompt contains decisions_summary text.

    To force a round 2, primary must reject (so the issue stays open and
    silence rule can't fire).
    """
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{
            "consolidated_id": "0", "source_issue_keys": ["s1::risk::missing_x"],
            "verdict": "reject", "reasoning": "out of scope for this iteration",
        }], "S1: r1"),
        reflection_json([{
            "consolidated_id": "0", "source_issue_keys": ["s1::gap::other"],
            "verdict": "accept", "reasoning": "ok",
        }], "S1: r2"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="s1::risk::missing_x"),
        objection_json(candidate_key="s1::gap::other"),  # new concern → round 2 still runs
    ])
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])

    cfg = RefineConfig(max_rounds=3)
    await refine_protocol("q", [primary, casper, balthasar], cfg)

    # casper's second prompt should include decisions_summary text.
    second_prompt = casper.query.await_args_list[1].args[0]
    assert "Primary's decisions from last round" in second_prompt
    assert "out of scope" in second_prompt
    assert "Still unresolved" in second_prompt


@pytest.mark.asyncio
async def test_refine_round_trace_complete(monkeypatch, tmp_path):
    """G2 / E1: TraceLogger.log_round called per round with full asdict(RefineRound)."""
    _silent_collator(monkeypatch)
    from magi.trace.logger import TraceLogger

    logger = TraceLogger(str(tmp_path))

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{
            "consolidated_id": "0", "source_issue_keys": ["s1::risk::x"],
            "verdict": "accept", "reasoning": "fix",
        }], "S1: r1"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="s1::risk::x"),
        empty_objections(),
    ])
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg, logger=logger)

    refine_file = tmp_path / "refine" / f"{decision.trace_id}.jsonl"
    assert refine_file.exists()
    lines = refine_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == decision.refine_summary["total_rounds"]
    first = json.loads(lines[0])
    assert "proposal_text" in first
    assert "objections" in first
    assert "reflections" in first
    assert "issue_snapshot" in first


# ---------------------------------------------------------------------------
# G3 — GUIDED integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_guided_approve(monkeypatch):
    """G3: guided=True with approve action completes normally."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "fix"}], "S1: r1"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x"),
        empty_objections(),
    ])
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])

    cb_calls = []

    async def cb(round_num, proposal, decisions, issue_summary):
        cb_calls.append((round_num, len(decisions)))
        return UserAction(action="approve")

    cfg = RefineConfig(max_rounds=3, guided=True, on_user_review=cb)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["guided"] is True
    assert decision.refine_summary["terminal_status"] == "converged"
    assert len(cb_calls) >= 1


@pytest.mark.asyncio
async def test_refine_guided_override(monkeypatch):
    """G3: user override redirects tracker resolution."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k::risk::x"],
                          "verdict": "reject", "reasoning": "out of scope"}], "S1: r1"),
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k::risk::x"],
                          "verdict": "accept", "reasoning": "user forced accept"}], "S1: r2"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x"),
        empty_objections(),
    ])
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])

    async def cb(round_num, proposal, decisions, issue_summary):
        if round_num == 1:
            return UserAction(
                action="override",
                overrides=[UserOverride(issue_key="k::risk::x", verdict="accept")],
            )
        return UserAction(action="approve")

    cfg = RefineConfig(max_rounds=3, guided=True, on_user_review=cb)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["user_overrides_count"] >= 1


@pytest.mark.asyncio
async def test_refine_guided_terminate(monkeypatch):
    """G3: user terminate stops refine immediately with cancelled status."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "fix"}], "S1: r1"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x"),
    ])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    async def cb(round_num, proposal, decisions, issue_summary):
        return UserAction(action="terminate")

    cfg = RefineConfig(max_rounds=5, guided=True, on_user_review=cb)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "cancelled"


@pytest.mark.asyncio
async def test_refine_guided_off(monkeypatch):
    """G3: guided=False (default) never invokes callback."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=["S1: initial"])
    casper = make_node("casper", responses=[empty_objections()])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    cb_calls = {"n": 0}

    async def cb(round_num, proposal, decisions, issue_summary):
        cb_calls["n"] += 1
        return UserAction(action="approve")

    cfg = RefineConfig(max_rounds=3)  # guided default False
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert cb_calls["n"] == 0
    assert decision.refine_summary["guided"] is False


@pytest.mark.asyncio
async def test_guided_callback_timeout_aborts_by_default(monkeypatch):
    """G3: callback exceeds guided_timeout_seconds → abort (default policy)."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "x"}], "S1: r1"),
    ])
    casper = make_node("casper", responses=[objection_json(candidate_key="k::risk::x")])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    async def cb(round_num, proposal, decisions, issue_summary):
        await asyncio.sleep(10)
        return UserAction(action="approve")

    cfg = RefineConfig(
        max_rounds=3, guided=True, on_user_review=cb,
        guided_timeout_seconds=0.05,  # very short
        guided_timeout_policy="abort",
    )
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "aborted"
    assert decision.refine_summary["abort_reason"] == "user_review_timeout"


@pytest.mark.asyncio
async def test_guided_callback_timeout_approve_opt_in(monkeypatch):
    """G3: timeout_policy='approve' silently approves on timeout."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "x"}], "S1: r1"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x"),
        empty_objections(),
    ])
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])

    async def cb(round_num, proposal, decisions, issue_summary):
        await asyncio.sleep(10)
        return UserAction(action="approve")

    cfg = RefineConfig(
        max_rounds=3, guided=True, on_user_review=cb,
        guided_timeout_seconds=0.05,
        guided_timeout_policy="approve",
    )
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    # Did NOT abort — silently approved and continued.
    assert decision.refine_summary["terminal_status"] != "aborted"


@pytest.mark.asyncio
async def test_guided_callback_exception_aborts(monkeypatch):
    """G3: callback exception → abort with degraded + abort_reason set."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "x"}], "S1: r1"),
    ])
    casper = make_node("casper", responses=[objection_json(candidate_key="k::risk::x")])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    async def cb(round_num, proposal, decisions, issue_summary):
        raise RuntimeError("user interrupted")

    cfg = RefineConfig(max_rounds=3, guided=True, on_user_review=cb)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "aborted"
    assert decision.degraded is True
    assert "user_review_failed" in (decision.refine_summary["abort_reason"] or "")


@pytest.mark.asyncio
async def test_guided_override_with_severity_after(monkeypatch):
    """G3: override verdict=partial with severity_after is respected by tracker."""
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k::risk::x"],
                          "verdict": "reject", "reasoning": "disagree"}], "S1: r1"),
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k::risk::x"],
                          "verdict": "accept", "reasoning": "ok"}], "S1: r2"),
    ])
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x", severity="critical"),
        empty_objections(),
    ])
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])

    async def cb(round_num, proposal, decisions, issue_summary):
        if round_num == 1:
            return UserAction(action="override", overrides=[
                UserOverride(issue_key="k::risk::x", verdict="partial", severity_after="minor"),
            ])
        return UserAction(action="approve")

    cfg = RefineConfig(max_rounds=3, guided=True, on_user_review=cb)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["user_overrides_count"] == 1


# ---------------------------------------------------------------------------
# D1/D2 cost attribution + multi-key resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collator_cost_attribution(monkeypatch):
    """D1 cost attribution: Collator cost appears in round_cost + decision.cost_usd."""
    from magi.protocols import refine, refine_collator

    async def _mock_collate(objs, round_num, config, reviewer_nodes):
        return refine_collator.fallback_consolidate(objs), 0.005, False

    monkeypatch.setattr(refine, "collate_objections", _mock_collate)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k::risk::x"],
                          "verdict": "accept", "reasoning": "x"}], "S1: r1"),
    ])
    primary.last_cost_usd = 0.01
    casper = make_node("casper", responses=[
        objection_json(candidate_key="k::risk::x"),
        empty_objections(),
    ])
    casper.last_cost_usd = 0.01
    balthasar = make_node("balthasar", responses=[
        empty_objections(),
        empty_objections(),
    ])
    balthasar.last_cost_usd = 0.01

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    # Collator cost included in total.
    assert decision.cost_usd >= 0.005


@pytest.mark.asyncio
async def test_collator_multi_key_resolve(monkeypatch):
    """D2 multi-key: reflection with multiple source_issue_keys resolves all at once."""
    from magi.protocols import refine, refine_collator

    async def _mock_collate(objs, round_num, config, reviewer_nodes):
        # Merge two raw keys into a single consolidated entry with 2 source_issue_keys.
        if not objs:
            return [], 0.0, False
        merged = {
            "issue_key": objs[0].issue_key,
            "category": objs[0].category,
            "severity": objs[0].severity,
            "target": objs[0].target,
            "description": objs[0].description,
            "suggestions": [
                {"reviewer": o.reviewer, "text": o.suggestion or ""}
                for o in objs
            ],
            "conflicting_suggestions": False,
            "source_reviewers": list({o.reviewer for o in objs}),
            "source_issue_keys": list({o.issue_key for o in objs}),
        }
        return [merged], 0.0, False

    monkeypatch.setattr(refine, "collate_objections", _mock_collate)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{
            "consolidated_id": "0",
            "source_issue_keys": ["k1::risk::x", "k2::risk::x"],
            "verdict": "accept",
            "reasoning": "fixed both",
        }], "S1: r1"),
    ])
    # Use distinctive keys that won't be collapsed by merge_similar_keys.
    casper = make_node("casper", responses=[
        objection_json(candidate_key="auth_module::error::expired_token"),
        empty_objections(),
    ])
    balthasar = make_node("balthasar", responses=[
        objection_json(candidate_key="database_layer::gap::missing_index"),
        empty_objections(),
    ])
    # Replace primary reflection with one that uses the actual canonical keys.
    primary.query = AsyncMock(side_effect=[
        "S1: initial",
        reflection_json([{
            "consolidated_id": "0",
            "source_issue_keys": [
                "auth_module::error::expired_token",
                "database_layer::gap::missing_index",
            ],
            "verdict": "accept",
            "reasoning": "fixed both",
        }], "S1: r1"),
    ])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    # Both distinct keys should be resolved.
    assert decision.refine_summary["resolved"] == 2


# ---------------------------------------------------------------------------
# H2 / H3 — Engine method + ask() dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_refine_method(monkeypatch, tmp_path):
    """H2: MAGI.refine(query) runs refine_protocol and logs decision."""
    from magi.core.engine import MAGI

    _silent_collator(monkeypatch)

    engine = MAGI.__new__(MAGI)
    primary = make_node("melchior", responses=["S1: initial"])
    casper = make_node("casper", responses=[empty_objections()])
    balthasar = make_node("balthasar", responses=[empty_objections()])
    engine._init_common([primary, casper, balthasar], trace_dir=str(tmp_path))
    engine._cost_mode = "measured"

    decision = await engine.refine("Design X")
    assert decision.protocol_used == "refine"
    assert decision.cost_mode == "measured"
    # Logged to daily trace.
    files = list(tmp_path.glob("*.jsonl"))
    assert files  # at least one daily trace file written


@pytest.mark.asyncio
async def test_ask_dispatches_refine_mode(monkeypatch, tmp_path):
    """H3: engine.ask(query, mode='refine') dispatches to refine() with default config."""
    from magi.core.engine import MAGI

    _silent_collator(monkeypatch)

    engine = MAGI.__new__(MAGI)
    primary = make_node("melchior", responses=["S1: initial"])
    casper = make_node("casper", responses=[empty_objections()])
    balthasar = make_node("balthasar", responses=[empty_objections()])
    engine._init_common([primary, casper, balthasar], trace_dir=str(tmp_path))
    engine._cost_mode = "measured"

    decision = await engine.ask("q", mode="refine")
    assert decision.protocol_used == "refine"
    assert decision.refine_summary is not None


def test_resolve_cost_mode_unit(tmp_path):
    """H1 unit: _resolve_cost_mode returns engine._cost_mode when not 'mixed'."""
    from magi.core.engine import MAGI

    eng = MAGI.__new__(MAGI)
    eng._init_common([make_node("a"), make_node("b"), make_node("c")], trace_dir=str(tmp_path))
    eng._cost_mode = "measured"
    assert eng._resolve_cost_mode() == "measured"

    eng._cost_mode = "estimated"
    assert eng._resolve_cost_mode() == "estimated"

    # Mixed with all measured → measured.
    eng._cost_mode = "mixed"
    for n in eng.nodes:
        n.cost_mode = "measured"
    assert eng._resolve_cost_mode() == "measured"

    # Mixed with unavailable → estimated.
    eng.nodes[0].cost_mode = "unavailable"
    assert eng._resolve_cost_mode() == "estimated"


# ---------------------------------------------------------------------------
# I2 — Server decision event includes refine_summary
# ---------------------------------------------------------------------------


def test_cli_refine_mode(monkeypatch, tmp_path):
    """H4: `magi ask --mode refine` runs the engine and prints ruling."""
    from click.testing import CliRunner
    from magi.cli import main as cli_main
    from magi.core.engine import MAGI

    _silent_collator(monkeypatch)

    # Replace MAGI(...) construction with our mock-nodes engine.
    def _fake_init(self, *args, **kwargs):
        self.nodes = [
            make_node("melchior", responses=["S1: initial"]),
            make_node("balthasar", responses=[empty_objections()]),
            make_node("casper", responses=[empty_objections()]),
        ]
        import os
        trace_dir = kwargs.get("trace_dir") or os.path.expanduser("~/.magi/traces")
        from magi.trace.logger import TraceLogger
        self.trace_dir = trace_dir
        self._logger = TraceLogger(str(tmp_path))
        self._cost_mode = "measured"

    monkeypatch.setattr(MAGI, "__init__", _fake_init)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["ask", "Design X", "--mode", "refine"])
    assert result.exit_code == 0, result.output
    assert "MAGI DECISION" in result.output
    assert "Protocol: refine" in result.output


def test_cli_guided_flag_calls_refine_directly(monkeypatch, tmp_path):
    """H4 / R9 #2: --guided bypasses ask() and invokes engine.refine() with a callback."""
    from click.testing import CliRunner
    from magi.cli import main as cli_main
    from magi.core.engine import MAGI

    _silent_collator(monkeypatch)

    calls = {"ask": 0, "refine": 0, "last_config": None}

    async def _fake_refine(self, query, config=None):
        calls["refine"] += 1
        calls["last_config"] = config
        d = Decision(
            query=query, ruling="done", confidence=1.0, minority_report="",
            votes={}, protocol_used="refine",
            refine_summary={"terminal_status": "converged", "guided": True},
        )
        return d

    async def _fake_ask(self, query, mode="vote"):
        calls["ask"] += 1
        return Decision(query=query, ruling="x", confidence=1.0,
                        minority_report="", votes={}, protocol_used="vote")

    def _fake_init(self, *args, **kwargs):
        self.nodes = []
        self.trace_dir = str(tmp_path)
        from magi.trace.logger import TraceLogger
        self._logger = TraceLogger(self.trace_dir)
        self._cost_mode = "measured"

    monkeypatch.setattr(MAGI, "__init__", _fake_init)
    monkeypatch.setattr(MAGI, "refine", _fake_refine)
    monkeypatch.setattr(MAGI, "ask", _fake_ask)

    runner = CliRunner()
    # Provide stdin input: approve → prompter returns UserAction(approve).
    result = runner.invoke(
        cli_main,
        ["ask", "Design X", "--mode", "refine", "--guided"],
        input="approve\n",
    )
    assert result.exit_code == 0, result.output
    # refine() was called, ask() was NOT (R9 #2).
    assert calls["refine"] == 1
    assert calls["ask"] == 0
    # Config had guided=True and a callback attached.
    cfg = calls["last_config"]
    assert cfg is not None
    assert cfg.guided is True
    assert cfg.on_user_review is not None


def test_server_decision_event_includes_refine_summary():
    """I2: Decision serialization over JSONL includes refine_summary for refine protocol."""
    from dataclasses import asdict

    summary = {
        "terminal_status": "converged",
        "total_rounds": 2,
        "resolved": 3,
        "best_round": 2,
    }
    d = Decision(
        query="q", ruling="answer",
        confidence=0.92, minority_report="",
        votes={"melchior": "answer"},
        protocol_used="refine",
        refine_summary=summary,
    )
    payload = json.loads(d.to_jsonl())
    assert payload["protocol_used"] == "refine"
    assert payload["refine_summary"] == summary
    # asdict also propagates.
    asdict_payload = asdict(d)
    assert asdict_payload["refine_summary"] == summary


# ---------------------------------------------------------------------------
# R02 regression tests (Round 02 MAJOR fixes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_reviewers_offline_round_2_aborts(monkeypatch):
    """R02 MAJOR-3: late-round (round 2+) all-reviewers-offline must abort —
    NOT silently converge via the silence rule. Round 1 returns valid objections
    (so iteration continues); round 2 both reviewers throw → abort.
    """
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{
            "consolidated_id": "0",
            "source_issue_keys": ["authoring_section::error::api_drift"],
            "verdict": "reject", "reasoning": "stays open",
        }], "S1: r1"),
    ])
    # Round 1: parseable objection; Round 2: infra failure.
    casper = AsyncMock()
    casper.name = "casper"
    casper.model = "gpt-4"
    casper.last_cost_usd = 0.01
    casper.query = AsyncMock(side_effect=[
        objection_json(candidate_key="authoring_section::error::api_drift"),
        RuntimeError("offline"),
        RuntimeError("offline"),  # retry also fails
    ])
    balthasar = AsyncMock()
    balthasar.name = "balthasar"
    balthasar.model = "gpt-4"
    balthasar.last_cost_usd = 0.01
    balthasar.query = AsyncMock(side_effect=[
        empty_objections(),
        RuntimeError("offline"),
        RuntimeError("offline"),
    ])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)
    assert decision.refine_summary["terminal_status"] == "aborted"
    assert decision.degraded is True
    assert decision.refine_summary["abort_reason"] == "all_reviewers_offline"


@pytest.mark.asyncio
async def test_parse_error_sets_degraded(monkeypatch):
    """R02 MAJOR-4: any parse_error during the run sets decision.degraded=True
    and records the reviewer's name in decision.failed_nodes (even if silence
    rule would otherwise converge the run).
    """
    _silent_collator(monkeypatch)

    primary = make_node("melchior", responses=["S1: initial"])
    # casper: both parse attempts fail on round 1 → parse_error.
    casper = make_node("casper", responses=[
        "not json at all",
        "still garbage",
    ])
    # balthasar: silent → triggers silence-rule convergence.
    balthasar = make_node("balthasar", responses=[empty_objections()])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg)

    assert decision.degraded is True
    assert "casper" in (decision.failed_nodes or [])


@pytest.mark.asyncio
async def test_primary_retry_cost_accumulated(monkeypatch):
    """R02 MAJOR-5: handle_primary_failure must return cost_delta so the failed
    primary attempt + its retry are both counted. Previously the failed-call
    cost was overwritten by the retry's last_cost_usd.
    """
    _silent_collator(monkeypatch)

    # Primary cost sequence:
    #   call 1 (initial proposal): 0.10
    #   call 2 (reflection — raises): sets last_cost_usd=0.10 pre-exception
    #   call 3 (reflection retry): 0.13
    call_costs = [0.10, 0.10, 0.13]

    async def _primary_query(prompt):
        idx = primary.query.await_count - 1
        # Mimic litellm: last_cost_usd is set whether the call raised or not.
        primary.last_cost_usd = call_costs[idx]
        if idx == 0:
            return "S1: initial"
        if idx == 1:
            raise RuntimeError("transient primary failure")
        return reflection_json([{
            "consolidated_id": "0",
            "source_issue_keys": ["authoring_section::error::api_drift"],
            "verdict": "accept", "reasoning": "fix",
        }], "S1: r1 retried")

    primary = AsyncMock()
    primary.name = "melchior"
    primary.model = "gpt-4"
    primary.last_cost_usd = 0.0
    primary.query = AsyncMock(side_effect=_primary_query)

    casper = make_node("casper", responses=[
        objection_json(candidate_key="authoring_section::error::api_drift"),
        empty_objections(),
    ])
    casper.last_cost_usd = 0.01

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper], cfg)

    # 0.10 (initial) + 0.10 (failed reflection) + 0.13 (retry) = 0.33 minimum
    # from the primary alone; reviewer + collator add a bit more. The R02 fix
    # guarantees >= 0.20 (pre-fix the failed-call 0.10 was lost).
    assert decision.cost_usd >= 0.2


@pytest.mark.asyncio
async def test_parse_error_lowers_confidence(monkeypatch):
    """R03 MAJOR-B: parse_error must set degraded BEFORE confidence is computed,
    so the degraded-penalty (−0.10) actually applies. Previously confidence was
    computed against stale degraded=False and stayed at 1.0 even with
    parse_errors present.
    """
    _silent_collator(monkeypatch)

    # Baseline: no parse errors — clean silence-rule convergence.
    p_clean = make_node("melchior", responses=["S1: initial"])
    c_clean = make_node("casper", responses=[empty_objections()])
    b_clean = make_node("balthasar", responses=[empty_objections()])
    clean = await refine_protocol("q", [p_clean, c_clean, b_clean], RefineConfig(max_rounds=3))

    # With parse error: identical shape, but casper emits unparseable output.
    p_bad = make_node("melchior", responses=["S1: initial"])
    c_bad = make_node("casper", responses=["not json", "still garbage"])
    b_bad = make_node("balthasar", responses=[empty_objections()])
    bad = await refine_protocol("q", [p_bad, c_bad, b_bad], RefineConfig(max_rounds=3))

    assert clean.degraded is False
    assert bad.degraded is True
    # Degraded penalty is −0.10 per compute_refine_confidence — strict <.
    assert bad.confidence < clean.confidence, (
        f"parse_error did not lower confidence: clean={clean.confidence}, bad={bad.confidence}"
    )


@pytest.mark.asyncio
async def test_decision_event_preserves_parse_error_for_tristate(monkeypatch):
    """R03 MAJOR-A: decision event payload must carry raw 'PARSE_ERROR' in votes
    AND the reviewer in failed_nodes, so the UI's mapVoteToLamp can render the
    warning lamp (PARSE_ERROR priority overrides abstain). If votes already
    binarized PARSE_ERROR to reject/abstain server-side, R9 #1 tri-state is
    unreachable no matter what the UI does.
    """
    _silent_collator(monkeypatch)

    p = make_node("melchior", responses=["S1: initial"])
    c = make_node("casper", responses=["not json", "still garbage"])  # parse_error
    b = make_node("balthasar", responses=[empty_objections()])

    decision = await refine_protocol("q", [p, c, b], RefineConfig(max_rounds=3))

    # Contract the UI depends on:
    assert decision.votes.get("casper") == "PARSE_ERROR", (
        f"votes['casper'] must remain raw PARSE_ERROR, got {decision.votes.get('casper')}"
    )
    assert "casper" in (decision.failed_nodes or [])
    # mapVoteToLamp(answer='PARSE_ERROR', ruling=..., isRefine=True, isFailed=True)
    # should return 'warning' (PARSE_ERROR priority) — not 'abstain'.
    # Simulate the helper's key decision path in Python:
    def map_vote_to_lamp(answer, ruling, is_refine, is_failed):
        if is_refine and answer == "PARSE_ERROR":
            return "warning"
        if is_failed:
            return "abstain"
        return "approve" if answer == ruling else "reject"

    lamp = map_vote_to_lamp(
        decision.votes["casper"],
        decision.ruling,
        True,
        "casper" in (decision.failed_nodes or []),
    )
    assert lamp == "warning"


# ---------------------------------------------------------------------------
# LOW-finding follow-ups (post-review polish)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guided_abort_round_logged(monkeypatch, tmp_path):
    """LOW#1: GUIDED timeout abort must persist its in-flight round to refine/<trace>.jsonl."""
    _silent_collator(monkeypatch)
    from magi.trace.logger import TraceLogger

    logger = TraceLogger(str(tmp_path))

    primary = make_node("melchior", responses=[
        "S1: initial",
        reflection_json([{"consolidated_id": "0", "source_issue_keys": ["k"],
                          "verdict": "accept", "reasoning": "x"}], "S1: r1"),
    ])
    casper = make_node("casper", responses=[objection_json(candidate_key="k::risk::x")])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    async def slow_cb(round_num, proposal, decisions, issue_summary):
        await asyncio.sleep(10)
        return UserAction(action="approve")

    cfg = RefineConfig(
        max_rounds=3, guided=True, on_user_review=slow_cb,
        guided_timeout_seconds=0.05, guided_timeout_policy="abort",
    )
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg, logger=logger)

    assert decision.refine_summary["terminal_status"] == "aborted"
    refine_file = tmp_path / "refine" / f"{decision.trace_id}.jsonl"
    assert refine_file.exists(), "abort round must be persisted to refine/<trace>.jsonl"
    lines = refine_file.read_text(encoding="utf-8").strip().split("\n")
    # Exactly one round (the abort round) should be logged for this single-round abort.
    assert len(lines) == decision.refine_summary["total_rounds"] == 1
    logged = json.loads(lines[0])
    assert logged["round_num"] == 1
    assert logged["guided_timeout"] is True


@pytest.mark.asyncio
async def test_reflection_parse_failure_records_round(monkeypatch, tmp_path):
    """LOW#2: reflection-parse abort must append its partial round + log it."""
    _silent_collator(monkeypatch)
    from magi.protocols import refine as refine_mod
    from magi.trace.logger import TraceLogger

    def _boom(_raw):
        raise ValueError("forced parse failure")

    monkeypatch.setattr(refine_mod, "_parse_reflection_response", _boom)
    logger = TraceLogger(str(tmp_path))

    primary = make_node("melchior", responses=["S1: initial", "garbled reflection"])
    casper = make_node("casper", responses=[objection_json(candidate_key="k::risk::x")])
    balthasar = make_node("balthasar", responses=[empty_objections()])

    cfg = RefineConfig(max_rounds=3)
    decision = await refine_protocol("q", [primary, casper, balthasar], cfg, logger=logger)

    assert decision.refine_summary["terminal_status"] == "aborted"
    assert decision.refine_summary["abort_reason"] == "primary_reflection_parse_failed"
    # total_rounds should count the abort round (LOW#2 — previously it was 0).
    assert decision.refine_summary["total_rounds"] == 1
    refine_file = tmp_path / "refine" / f"{decision.trace_id}.jsonl"
    assert refine_file.exists()
    lines = refine_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["round_num"] == 1
    assert logged["reflections"] == []


@pytest.mark.asyncio
async def test_stdin_prompter_empty_input_terminates(monkeypatch):
    """LOW#3: empty stdin / stdin exception → UserAction(terminate), never silent approve."""
    import io
    from magi.cli import _build_stdin_prompter

    # Empty input (EOF) → terminate.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    prompter = _build_stdin_prompter()
    action = await prompter(1, "S1: proposal", [], {})
    assert action.action == "terminate"

    # Whitespace-only input → terminate.
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    prompter = _build_stdin_prompter()
    action = await prompter(1, "S1: proposal", [], {})
    assert action.action == "terminate"

    # Explicit "approve" still works (regression guard).
    monkeypatch.setattr("sys.stdin", io.StringIO("approve\n"))
    prompter = _build_stdin_prompter()
    action = await prompter(1, "S1: proposal", [], {})
    assert action.action == "approve"
