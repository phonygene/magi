"""Tests for CLI adapters — prepare/parse_output, CliOutputCleaner, concurrency."""
import json
import os
import tempfile

import pytest

from magi.core.cli_adapters import (
    ClaudeAdapter,
    CodexAdapter,
    GeminiAdapter,
    CliOutputCleaner,
    InvocationContext,
    ParseResult,
    create_adapter,
)


# ---------------------------------------------------------------------------
# InvocationContext
# ---------------------------------------------------------------------------

class TestInvocationContext:
    def test_cleanup_removes_temp_files(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("x")
        f2.write_text("y")
        ctx = InvocationContext(command=[], temp_files=[str(f1), str(f2)])
        ctx.cleanup()
        assert not f1.exists()
        assert not f2.exists()

    def test_cleanup_ignores_missing_files(self):
        ctx = InvocationContext(command=[], temp_files=["/nonexistent/file.txt"])
        ctx.cleanup()  # should not raise


# ---------------------------------------------------------------------------
# CliOutputCleaner
# ---------------------------------------------------------------------------

class TestCliOutputCleaner:
    def test_strips_ansi(self):
        assert CliOutputCleaner.clean("\x1B[32mhello\x1B[0m") == "hello"

    def test_strips_spinner_noise(self):
        text = "⠋ Loading...\nActual answer here"
        assert "Actual answer here" in CliOutputCleaner.clean(text)

    def test_unwraps_lone_codeblock(self):
        text = "```\nthe content\n```"
        assert CliOutputCleaner.clean(text) == "the content"

    def test_keeps_codeblock_if_not_sole_content(self):
        text = "Before\n```\ncode\n```\nAfter"
        cleaned = CliOutputCleaner.clean(text)
        assert "Before" in cleaned
        assert "After" in cleaned

    def test_strips_thinking_lines(self):
        text = "Thinking...\nClaude is thinking...\nReal answer"
        assert CliOutputCleaner.clean(text) == "Real answer"

    def test_empty_string(self):
        assert CliOutputCleaner.clean("") == ""


# ---------------------------------------------------------------------------
# ClaudeAdapter
# ---------------------------------------------------------------------------

class TestClaudeAdapter:
    def test_prepare_command(self):
        adapter = ClaudeAdapter(model_tier="opus", effort="high")
        ctx = adapter.prepare("test prompt")
        assert ctx.command[0] == "claude"
        assert "-p" in ctx.command
        assert "--output-format" in ctx.command
        assert "json" in ctx.command
        assert "--no-session-persistence" in ctx.command
        assert "--permission-mode" in ctx.command
        assert "--tools" in ctx.command
        assert ctx.stdin_data == b"test prompt"
        assert ctx.temp_files == []

    def test_parse_json_output(self):
        adapter = ClaudeAdapter()
        data = json.dumps({"result": "Hello world", "total_cost_usd": 0.05})
        ctx = InvocationContext(command=[])
        parsed = adapter.parse_output(ctx, data.encode(), b"", 0)
        assert isinstance(parsed, ParseResult)
        assert parsed.text == "Hello world"
        assert parsed.cost_usd == 0.05

    def test_parse_json_with_content_key(self):
        adapter = ClaudeAdapter()
        data = json.dumps({"content": "Fallback content"})
        ctx = InvocationContext(command=[])
        parsed = adapter.parse_output(ctx, data.encode(), b"", 0)
        assert parsed.text == "Fallback content"

    def test_parse_fallback_plain_text(self):
        adapter = ClaudeAdapter()
        ctx = InvocationContext(command=[])
        parsed = adapter.parse_output(ctx, b"plain text response", b"", 0)
        assert parsed.text == "plain text response"
        assert parsed.cost_usd == 0.0

    def test_model_description(self):
        adapter = ClaudeAdapter(model_tier="sonnet")
        assert adapter.model_description == "claude sonnet"

    def test_cost_mode(self):
        assert ClaudeAdapter().cost_mode == "measured"


# ---------------------------------------------------------------------------
# CodexAdapter
# ---------------------------------------------------------------------------

class TestCodexAdapter:
    def test_prepare_command(self):
        adapter = CodexAdapter(effort="high")
        ctx = adapter.prepare("short prompt")
        assert ctx.command[0] == "codex"
        assert "exec" in ctx.command
        # No -m flag (v9: ChatGPT can't specify model)
        assert "-m" not in ctx.command
        assert "--skip-git-repo-check" in ctx.command
        assert "--ephemeral" in ctx.command
        # Prompt always via stdin (security: never in argv)
        assert "short prompt" not in ctx.command
        assert ctx.stdin_data == b"short prompt"
        assert len(ctx.temp_files) == 1
        ctx.cleanup()

    def test_parse_from_output_file(self, tmp_path):
        adapter = CodexAdapter()
        outfile = tmp_path / "out.txt"
        outfile.write_text("CLI response text")
        ctx = InvocationContext(command=[], temp_files=[str(outfile)])
        parsed = adapter.parse_output(ctx, b"", b"", 0)
        assert parsed.text == "CLI response text"
        assert parsed.cost_usd == 0.0

    def test_parse_fallback_to_stdout(self):
        adapter = CodexAdapter()
        ctx = InvocationContext(command=[], temp_files=["/nonexistent/file.txt"])
        parsed = adapter.parse_output(ctx, b"stdout fallback", b"", 0)
        assert parsed.text == "stdout fallback"

    def test_cost_mode(self):
        assert CodexAdapter().cost_mode == "unavailable"

    def test_temp_file_created_and_cleanable(self):
        adapter = CodexAdapter()
        ctx = adapter.prepare("test")
        assert len(ctx.temp_files) == 1
        assert os.path.exists(ctx.temp_files[0])
        ctx.cleanup()
        assert not os.path.exists(ctx.temp_files[0])


# ---------------------------------------------------------------------------
# GeminiAdapter
# ---------------------------------------------------------------------------

class TestGeminiAdapter:
    def test_prepare_command(self):
        adapter = GeminiAdapter(model="gemini-3-flash-preview")
        ctx = adapter.prepare("test prompt")
        assert ctx.command[0] == "gemini"
        assert "-m" in ctx.command
        assert "-o" in ctx.command
        assert "json" in ctx.command
        # Always empty --prompt + stdin (security: never in argv)
        idx = ctx.command.index("--prompt")
        assert ctx.command[idx + 1] == ""
        assert "test prompt" not in ctx.command
        assert ctx.stdin_data == b"test prompt"

    def test_parse_json_output(self):
        adapter = GeminiAdapter()
        data = json.dumps({
            "response": "Gemini answer",
            "stats": {"models": {"gemini-3-flash": {"inputTokens": 100, "outputTokens": 50}}}
        })
        ctx = InvocationContext(command=[])
        parsed = adapter.parse_output(ctx, data.encode(), b"", 0)
        assert parsed.text == "Gemini answer"
        assert parsed.cost_usd > 0  # should calculate from tokens

    def test_parse_json_response_key(self):
        """Confirmed: gemini JSON uses 'response' key, not 'text'."""
        adapter = GeminiAdapter()
        data = json.dumps({"response": "the answer"})
        ctx = InvocationContext(command=[])
        parsed = adapter.parse_output(ctx, data.encode(), b"", 0)
        assert parsed.text == "the answer"

    def test_parse_fallback_plain_text(self):
        adapter = GeminiAdapter()
        ctx = InvocationContext(command=[])
        parsed = adapter.parse_output(ctx, b"plain text", b"", 0)
        assert parsed.text == "plain text"
        assert parsed.cost_usd == 0.0

    def test_cost_mode(self):
        assert GeminiAdapter().cost_mode == "estimated"


# ---------------------------------------------------------------------------
# Concurrency safety
# ---------------------------------------------------------------------------

class TestConcurrencySafety:
    def test_prepare_creates_independent_contexts(self):
        """Each prepare() call must create independent InvocationContext."""
        adapter = CodexAdapter()
        ctx1 = adapter.prepare("prompt 1")
        ctx2 = adapter.prepare("prompt 2")
        # Different temp files
        assert ctx1.temp_files[0] != ctx2.temp_files[0]
        # Cleanup one doesn't affect the other
        ctx1.cleanup()
        assert os.path.exists(ctx2.temp_files[0])
        ctx2.cleanup()

    def test_claude_contexts_independent(self):
        adapter = ClaudeAdapter()
        ctx1 = adapter.prepare("p1")
        ctx2 = adapter.prepare("p2")
        assert ctx1.stdin_data != ctx2.stdin_data
        assert ctx1 is not ctx2

    def test_gemini_contexts_independent(self):
        adapter = GeminiAdapter()
        ctx1 = adapter.prepare("p1")
        ctx2 = adapter.prepare("p2")
        assert ctx1 is not ctx2


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestCreateAdapter:
    def test_creates_claude(self):
        adapter = create_adapter("claude", model_tier="sonnet")
        assert isinstance(adapter, ClaudeAdapter)

    def test_creates_codex(self):
        adapter = create_adapter("codex")
        assert isinstance(adapter, CodexAdapter)

    def test_creates_gemini(self):
        adapter = create_adapter("gemini")
        assert isinstance(adapter, GeminiAdapter)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown CLI adapter"):
            create_adapter("unknown_cli")
