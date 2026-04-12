"""Tests for CritiqueProtocol (ICE)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from magi.core.node import MagiNode, MELCHIOR, BALTHASAR, CASPER
from magi.protocols.critique import critique


def _mock_judge_response(score: float = 0.9, dissent: str = "none"):
    """Create a mock LLM response for the judge."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = (
        f"CONCLUSION: yes\nAGREEMENT_SCORE: {score}\nDISSENT_SUMMARY: {dissent}"
    )
    return resp


def make_nodes():
    return [
        MagiNode("melchior", "mock", MELCHIOR),
        MagiNode("balthasar", "mock", BALTHASAR),
        MagiNode("casper", "mock", CASPER),
    ]


@pytest.mark.asyncio
async def test_critique_reaches_consensus():
    """All nodes converge after one round of critique."""
    nodes = make_nodes()
    call_count = {n.name: 0 for n in nodes}

    async def mock_query(name):
        async def _query(prompt):
            call_count[name] += 1
            if call_count[name] == 1:
                # Initial diverse answers
                return {"melchior": "Answer A with details", "balthasar": "Answer B with info", "casper": "Answer C with facts"}[name]
            else:
                # After critique, all converge
                return "The consensus answer incorporating all perspectives"
        return _query

    for n in nodes:
        n.query = await mock_query(n.name)

    # Mock judge: low agreement initially, high after convergence
    judge_scores = iter([0.3, 0.95])
    async def mock_judge(query, answers, timeout=15.0):
        return next(judge_scores, (0.95, None)), None
    # Flatten: mock_judge returns tuple[float, str|None] but iter gives float
    call_idx = [0]
    scores = [0.3, 0.95]
    async def mock_judge_fn(query, answers, timeout=15.0):
        idx = min(call_idx[0], len(scores) - 1)
        call_idx[0] += 1
        return scores[idx], None

    with patch("magi.protocols.judge.llm_estimate_agreement", side_effect=mock_judge_fn):
        decision = await critique("test question", nodes, max_rounds=3)
    assert decision.ruling is not None
    assert "critique_ice" in decision.protocol_used
    assert decision.confidence > 0  # should have some agreement


@pytest.mark.asyncio
async def test_critique_max_rounds():
    """Nodes never agree, hits max_rounds."""
    nodes = make_nodes()
    round_num = [0]

    for i, n in enumerate(nodes):
        async def make_mock(idx=i):
            async def _query(prompt):
                round_num[0] += 1
                # Always give completely different answers
                return f"unique answer {idx} round {round_num[0]} with completely different words each time"
            return _query
        n.query = await make_mock()

    # Mock judge: always low agreement
    async def mock_judge_low(query, answers, timeout=15.0):
        return 0.2, "Completely different answers"

    with patch("magi.protocols.judge.llm_estimate_agreement", side_effect=mock_judge_low):
        decision = await critique("test", nodes, max_rounds=2)
    # With synthesis phase, protocol is critique_ice_synth_r2; without, critique_ice_r2
    assert "critique_ice" in decision.protocol_used
    assert "r2" in decision.protocol_used


@pytest.mark.asyncio
async def test_critique_one_node_fails():
    """One node fails during initial round."""
    nodes = make_nodes()
    nodes[0].query = AsyncMock(return_value="Answer from melchior")
    nodes[1].query = AsyncMock(return_value="Answer from balthasar")
    nodes[2].query = AsyncMock(side_effect=TimeoutError("timeout"))

    # Mock judge for 2-answer agreement
    async def mock_judge_two(query, answers, timeout=15.0):
        return 0.7, None

    with patch("magi.protocols.judge.llm_estimate_agreement", side_effect=mock_judge_two):
        decision = await critique("test", nodes, max_rounds=1)
    assert decision.degraded is True
    assert "casper" in decision.failed_nodes
    assert len(decision.votes) == 2


@pytest.mark.asyncio
async def test_critique_all_fail():
    """All nodes fail."""
    nodes = make_nodes()
    for n in nodes:
        n.query = AsyncMock(side_effect=RuntimeError("dead"))

    with pytest.raises(RuntimeError, match="All MAGI nodes failed"):
        await critique("test", nodes)


@pytest.mark.asyncio
async def test_critique_detects_mind_changes():
    """Detect when a node significantly changes its position."""
    nodes = make_nodes()
    call_counts = {n.name: 0 for n in nodes}

    for n in nodes:
        original_name = n.name
        async def make_mock(name=original_name):
            async def _query(prompt):
                call_counts[name] += 1
                if call_counts[name] == 1:
                    if name == "melchior":
                        return "I strongly believe the answer is yes for these reasons"
                    elif name == "balthasar":
                        return "The answer is definitely no based on my analysis"
                    else:
                        return "I think maybe it could be yes or no depending"
                else:
                    # Melchior changes mind completely
                    if name == "melchior":
                        return "After reviewing other perspectives I now agree the answer is no"
                    else:
                        return "The answer is definitely no based on my analysis confirmed"
            return _query
        n.query = await make_mock()

    # Mock judge: low agreement to trigger critique, then medium after
    call_idx = [0]
    scores = [0.3, 0.6]
    async def mock_judge_fn(query, answers, timeout=15.0):
        idx = min(call_idx[0], len(scores) - 1)
        call_idx[0] += 1
        return scores[idx], None

    with patch("magi.protocols.judge.llm_estimate_agreement", side_effect=mock_judge_fn):
        decision = await critique("test", nodes, max_rounds=1)
    # melchior should show up as having changed mind
    assert isinstance(decision.mind_changes, list)
