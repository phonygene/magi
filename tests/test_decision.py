"""Tests for Decision dataclass."""
import json
from magi.core.decision import Decision


def test_decision_defaults():
    d = Decision(
        query="test",
        ruling="answer",
        confidence=0.9,
        minority_report="",
        votes={"melchior": "answer"},
    )
    assert d.protocol_used == "vote"
    assert d.degraded is False
    assert d.failed_nodes == []
    assert d.mind_changes == []
    assert len(d.trace_id) == 8


def test_decision_to_jsonl():
    d = Decision(
        query="what is 1+1?",
        ruling="2",
        confidence=1.0,
        minority_report="",
        votes={"melchior": "2", "balthasar": "2", "casper": "2"},
    )
    line = d.to_jsonl()
    parsed = json.loads(line)
    assert parsed["query"] == "what is 1+1?"
    assert parsed["ruling"] == "2"
    assert parsed["confidence"] == 1.0
    assert len(parsed["votes"]) == 3


def test_decision_degraded():
    d = Decision(
        query="test",
        ruling="answer",
        confidence=0.5,
        minority_report="",
        votes={"melchior": "answer"},
        degraded=True,
        failed_nodes=["balthasar", "casper"],
        protocol_used="fallback_single",
    )
    assert d.degraded is True
    assert len(d.failed_nodes) == 2
    assert d.protocol_used == "fallback_single"


def test_decision_refine_summary_serialization():
    """A1: Decision with refine_summary serializes/roundtrips cleanly."""
    summary = {
        "terminal_status": "converged",
        "rounds_executed": 3,
        "collator_cost_usd": 0.0012,
        "best_round": 3,
        "sycophancy_detected": False,
    }
    d = Decision(
        query="Design a rate limiter",
        ruling="Use token bucket with 100 rps default.",
        confidence=0.92,
        minority_report="",
        votes={"melchior": "APPROVE", "balthasar": "APPROVE", "casper": "APPROVE"},
        protocol_used="refine",
        refine_summary=summary,
    )
    line = d.to_jsonl()
    parsed = json.loads(line)
    assert parsed["refine_summary"] == summary
    assert parsed["refine_summary"]["terminal_status"] == "converged"
    assert parsed["protocol_used"] == "refine"


def test_decision_backcompat_no_refine_summary():
    """A1: Existing vote/critique/adaptive Decisions serialize with refine_summary=None (no KeyError)."""
    d = Decision(
        query="test",
        ruling="answer",
        confidence=1.0,
        minority_report="",
        votes={},
    )
    assert d.refine_summary is None
    line = d.to_jsonl()
    parsed = json.loads(line)
    assert "refine_summary" in parsed
    assert parsed["refine_summary"] is None
    assert "refine_trace_id" not in parsed  # R9 #3: must NOT add a separate refine_trace_id field
