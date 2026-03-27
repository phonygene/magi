"""Tests for VoteProtocol with mocked LLM responses."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from magi.core.node import MagiNode, MELCHIOR, BALTHASAR, CASPER
from magi.protocols.vote import vote


def make_nodes():
    return [
        MagiNode("melchior", "mock-model", MELCHIOR),
        MagiNode("balthasar", "mock-model", BALTHASAR),
        MagiNode("casper", "mock-model", CASPER),
    ]


@pytest.mark.asyncio
async def test_vote_all_agree():
    nodes = make_nodes()
    for n in nodes:
        n.query = AsyncMock(return_value="The answer is 42")

    decision = await vote("What is the meaning of life?", nodes)
    assert decision.ruling == "The answer is 42"
    assert len(decision.votes) == 3
    assert decision.degraded is False
    assert decision.protocol_used == "vote"


@pytest.mark.asyncio
async def test_vote_one_node_fails():
    nodes = make_nodes()
    nodes[0].query = AsyncMock(return_value="Answer A")
    nodes[1].query = AsyncMock(return_value="Answer B")
    nodes[2].query = AsyncMock(side_effect=TimeoutError("timeout"))

    decision = await vote("test", nodes)
    assert decision.degraded is True
    assert "casper" in decision.failed_nodes
    assert len(decision.votes) == 2


@pytest.mark.asyncio
async def test_vote_two_nodes_fail():
    nodes = make_nodes()
    nodes[0].query = AsyncMock(return_value="Only answer")
    nodes[1].query = AsyncMock(side_effect=TimeoutError("timeout"))
    nodes[2].query = AsyncMock(side_effect=RuntimeError("API error"))

    decision = await vote("test", nodes)
    assert decision.degraded is True
    assert decision.protocol_used == "fallback_single"
    assert decision.confidence == 0.3
    assert len(decision.votes) == 1


@pytest.mark.asyncio
async def test_vote_all_nodes_fail():
    nodes = make_nodes()
    for n in nodes:
        n.query = AsyncMock(side_effect=TimeoutError("timeout"))

    with pytest.raises(RuntimeError, match="All MAGI nodes failed"):
        await vote("test", nodes)


@pytest.mark.asyncio
async def test_vote_minority_report():
    nodes = make_nodes()
    nodes[0].query = AsyncMock(return_value="Answer A")
    nodes[1].query = AsyncMock(return_value="Answer B")
    nodes[2].query = AsyncMock(return_value="Answer C")

    decision = await vote("test", nodes)
    assert decision.minority_report  # should not be empty
    assert len(decision.votes) == 3
