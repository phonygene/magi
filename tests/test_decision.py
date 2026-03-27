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
