"""Tests for CliNode — subprocess isolation, error handling, cost tracking."""
import asyncio
import json
import time

import pytest
from unittest.mock import MagicMock, patch

from magi.core.cli_adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter, InvocationContext, ParseResult
from magi.core.cli_node import CliNode
from magi.core.cli_errors import (
    MagiCliAuthError,
    MagiCliExecutionError,
    MagiNodeTimeoutError,
    MagiProviderNotFoundError,
)
from magi.core.node import MELCHIOR, BALTHASAR, CASPER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claude_node(timeout=30.0):
    return CliNode("melchior", MELCHIOR, ClaudeAdapter(model_tier="opus"), timeout=timeout)


def _make_codex_node(timeout=30.0):
    return CliNode("balthasar", BALTHASAR, CodexAdapter(), timeout=timeout)


def _make_gemini_node(timeout=30.0):
    return CliNode("casper", CASPER, GeminiAdapter(), timeout=timeout)


def _mock_popen(stdout=b"", stderr=b"", returncode=0):
    """Create a mock for subprocess.Popen (sync, not async)."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (stdout, stderr)
    mock_proc.returncode = returncode
    return mock_proc


# ---------------------------------------------------------------------------
# Basic query tests
# ---------------------------------------------------------------------------

class TestCliNodeQuery:
    @pytest.mark.asyncio
    async def test_claude_node_query_success(self):
        node = _make_claude_node()
        response = json.dumps({"result": "The answer is 42", "total_cost_usd": 0.05})
        mock_proc = _mock_popen(stdout=response.encode(), returncode=0)

        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            result = await node.query("What is the meaning of life?")

        assert result == "The answer is 42"
        assert node.last_cost_usd == 0.05
        assert node.cost_mode == "measured"

    @pytest.mark.asyncio
    async def test_codex_node_query_success(self, tmp_path):
        node = _make_codex_node()
        outfile = tmp_path / "output.txt"
        outfile.write_text("Codex response here")

        mock_proc = _mock_popen(stdout=b"", returncode=0)

        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with patch.object(node.adapter, "prepare") as mock_prepare:
                mock_prepare.return_value = InvocationContext(
                    command=["codex", "exec", "test"],
                    stdin_data=None,
                    temp_files=[str(outfile)],
                )
                result = await node.query("test")

        assert result == "Codex response here"
        assert node.cost_mode == "unavailable"

    @pytest.mark.asyncio
    async def test_gemini_node_query_success(self):
        node = _make_gemini_node()
        response = json.dumps({
            "response": "Gemini says hello",
            "stats": {"models": {"gemini-3-flash": {"inputTokens": 50, "outputTokens": 20}}}
        })
        mock_proc = _mock_popen(stdout=response.encode(), returncode=0)

        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            result = await node.query("Hello?")

        assert result == "Gemini says hello"
        assert node.cost_mode == "estimated"


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestCliNodeErrors:
    @pytest.mark.asyncio
    async def test_provider_not_found(self):
        node = _make_claude_node()
        with patch("magi.core.cli_node.subprocess.Popen", side_effect=FileNotFoundError):
            with pytest.raises(MagiProviderNotFoundError, match="claude"):
                await node.query("test")

    @pytest.mark.asyncio
    async def test_execution_error(self):
        node = _make_claude_node()
        mock_proc = _mock_popen(stdout=b"", stderr=b"some error", returncode=1)
        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(MagiCliExecutionError, match="code 1"):
                await node.query("test")

    @pytest.mark.asyncio
    async def test_auth_error_detected(self):
        node = _make_claude_node()
        mock_proc = _mock_popen(stdout=b"", stderr=b"Error: not authenticated. Please login.", returncode=1)
        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(MagiCliAuthError, match="authentication failed"):
                await node.query("test")

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        import subprocess as _sp
        node = _make_claude_node(timeout=0.5)

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = [
            _sp.TimeoutExpired(cmd="claude", timeout=0.5),
            (b"", b""),  # second call after kill()
        ]
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(MagiNodeTimeoutError, match="timed out"):
                await node.query("test")

    @pytest.mark.asyncio
    async def test_empty_response_raises(self):
        node = _make_claude_node()
        response = json.dumps({"result": ""})
        mock_proc = _mock_popen(stdout=response.encode(), returncode=0)
        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(ValueError, match="empty response"):
                await node.query("test")


# ---------------------------------------------------------------------------
# Node interface compliance
# ---------------------------------------------------------------------------

class TestCliNodeInterface:
    def test_has_required_attributes(self):
        node = _make_claude_node()
        assert hasattr(node, "name")
        assert hasattr(node, "model")
        assert hasattr(node, "persona")
        assert hasattr(node, "last_cost_usd")
        assert hasattr(node, "query")

    def test_name_matches(self):
        node = _make_claude_node()
        assert node.name == "melchior"

    def test_model_description(self):
        node = _make_claude_node()
        assert node.model == "claude opus"

    def test_initial_cost_zero(self):
        node = _make_claude_node()
        assert node.last_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_includes_persona(self):
        node = _make_claude_node()
        prompt = node._build_prompt("What is AI?")
        assert "melchior" in prompt.lower() or "MELCHIOR" in prompt or "Melchior" in prompt
        assert "What is AI?" in prompt
        assert "analytical scientist" in prompt.lower() or "logic" in prompt.lower()

    def test_includes_instruction_tag(self):
        node = _make_claude_node()
        prompt = node._build_prompt("test")
        assert "[INSTRUCTION]" in prompt


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_called_on_success(self, tmp_path):
        node = _make_codex_node()
        outfile = tmp_path / "out.txt"
        outfile.write_text("result")

        mock_proc = _mock_popen(stdout=b"", returncode=0)

        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with patch.object(node.adapter, "prepare") as mock_prepare:
                ctx = InvocationContext(
                    command=["codex", "exec", "test"],
                    temp_files=[str(outfile)],
                )
                mock_prepare.return_value = ctx
                await node.query("test")

        # Temp file should be cleaned up
        assert not outfile.exists()

    @pytest.mark.asyncio
    async def test_cleanup_called_on_error(self, tmp_path):
        node = _make_codex_node()
        outfile = tmp_path / "out.txt"
        outfile.write_text("")

        mock_proc = _mock_popen(stdout=b"", stderr=b"error", returncode=1)

        with patch("magi.core.cli_node.subprocess.Popen", return_value=mock_proc):
            with patch.object(node.adapter, "prepare") as mock_prepare:
                ctx = InvocationContext(
                    command=["codex", "exec", "test"],
                    temp_files=[str(outfile)],
                )
                mock_prepare.return_value = ctx
                with pytest.raises(MagiCliExecutionError):
                    await node.query("test")

        # Temp file should still be cleaned up
        assert not outfile.exists()
