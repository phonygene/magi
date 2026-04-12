"""CLI adapters for MAGI CLI-native mode.

Each adapter encapsulates per-provider CLI behavior:
- How to build the command
- How to deliver the prompt (stdin vs arg)
- How to parse the output (returns text + cost tuple)
- What isolation flags to use

All per-invocation state lives in InvocationContext (concurrency-safe).
No mutable state on adapter instances — parse_output returns cost directly.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# ParseResult — return type for parse_output (text + cost)
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """Result of parsing CLI output. Bundles text and cost together."""
    text: str
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# InvocationContext — per-call state (concurrency-safe)
# ---------------------------------------------------------------------------

@dataclass
class InvocationContext:
    """Per-call invocation state. Created by adapter.prepare(), consumed by CliNode."""
    command: list[str]
    stdin_data: bytes | None = None
    temp_files: list[str] = field(default_factory=list)

    def cleanup(self):
        for f in self.temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CliAdapter Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class CliAdapter(Protocol):
    """Per-provider CLI behavior adapter."""
    model_description: str
    cli_name: ClassVar[str]
    cost_mode: str

    def available(self) -> bool: ...
    def prepare(self, prompt: str) -> InvocationContext: ...
    def parse_output(self, ctx: InvocationContext, stdout: bytes, stderr: bytes, returncode: int) -> ParseResult: ...


# ---------------------------------------------------------------------------
# CliOutputCleaner — fallback text cleaner
# ---------------------------------------------------------------------------

class CliOutputCleaner:
    """Strip ANSI codes, spinner noise, and unwrap lone code blocks."""
    _ANSI_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
    _NOISE_PATTERNS = [
        re.compile(r"^⠋.*$", re.MULTILINE),
        re.compile(r"^Thinking\.+$", re.MULTILINE),
        re.compile(r"^Claude is thinking\.\.\.$", re.MULTILINE),
    ]
    _CODEBLOCK_RE = re.compile(r"^```(?:\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)

    @classmethod
    def clean(cls, text: str) -> str:
        text = cls._ANSI_RE.sub("", text)
        for p in cls._NOISE_PATTERNS:
            text = p.sub("", text)
        stripped = text.strip()
        match = cls._CODEBLOCK_RE.search(stripped)
        if match and match.group(0).strip() == stripped:
            text = match.group(1)
        return text.strip()


# ---------------------------------------------------------------------------
# ClaudeAdapter
# ---------------------------------------------------------------------------

class ClaudeAdapter:
    """Claude CLI adapter. stdin-based, --output-format json.

    Cost mode: measured (JSON response includes total_cost_usd).
    Isolation: auth-safe profile (no --bare — preserves OAuth).
    --tools "" disables all tools (verified with claude v2.1.87).
    """
    cli_name = "claude"

    def __init__(self, model_tier: str = "opus", effort: str = "high"):
        self.model_tier = model_tier
        self.effort = effort
        self.model_description = f"claude {model_tier}"
        self.cost_mode = "measured"

    def available(self) -> bool:
        return shutil.which("claude") is not None

    def prepare(self, prompt: str) -> InvocationContext:
        cmd = [
            "claude", "-p",
            "--model", self.model_tier,
            "--effort", self.effort,
            "--output-format", "json",
            # Isolation: auth-safe profile (v7+)
            "--no-session-persistence",
            "--permission-mode", "plan",
            "--tools", "",  # Disables all tools — verified with claude v2.1.87
        ]
        return InvocationContext(
            command=cmd,
            stdin_data=prompt.encode("utf-8"),
            temp_files=[],
        )

    def parse_output(self, ctx: InvocationContext, stdout: bytes, stderr: bytes, returncode: int) -> ParseResult:
        text = stdout.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
            cost = data.get("total_cost_usd", 0.0)
            result = data.get("result", data.get("content", ""))
            if isinstance(result, str):
                return ParseResult(text=result.strip(), cost_usd=cost)
            return ParseResult(text=str(result).strip(), cost_usd=cost)
        except (json.JSONDecodeError, AttributeError):
            return ParseResult(text=CliOutputCleaner.clean(text))


# ---------------------------------------------------------------------------
# CodexAdapter
# ---------------------------------------------------------------------------

class CodexAdapter:
    """Codex CLI adapter. -o <tmpfile> for output.

    Cost mode: unavailable (no cost info in output).
    Note: ChatGPT subscription accounts cannot specify -m model flag.
    """
    cli_name = "codex"

    def __init__(self, effort: str = "high"):
        self.effort = effort
        self.model_description = "codex (default model)"
        self.cost_mode = "unavailable"

    def available(self) -> bool:
        return shutil.which("codex") is not None

    def prepare(self, prompt: str) -> InvocationContext:
        # Per-invocation temp file (Windows-safe: mkstemp + close fd)
        fd, output_file = tempfile.mkstemp(suffix=".txt", prefix="magi_codex_")
        os.close(fd)

        cmd = [
            "codex", "exec",
            # No -m flag: ChatGPT subscription can't specify model (v9 correction)
            "-c", f"model_reasoning_effort={self.effort}",
            "-o", output_file,
            # Isolation
            "--skip-git-repo-check",
            "--ephemeral",
            # No prompt in argv — always use stdin to prevent flag injection
        ]

        return InvocationContext(
            command=cmd,
            stdin_data=prompt.encode("utf-8"),
            temp_files=[output_file],
        )

    def parse_output(self, ctx: InvocationContext, stdout: bytes, stderr: bytes, returncode: int) -> ParseResult:
        # Read from -o file (plain text, no metadata)
        output_file = ctx.temp_files[0] if ctx.temp_files else None
        if output_file and os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    text = f.read().strip()
            except UnicodeDecodeError:
                with open(output_file, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            if text:
                return ParseResult(text=CliOutputCleaner.clean(text))
        # Fallback: plain stdout
        return ParseResult(text=CliOutputCleaner.clean(stdout.decode("utf-8", errors="replace")))


# ---------------------------------------------------------------------------
# GeminiAdapter
# ---------------------------------------------------------------------------

# Gemini Flash pricing as of 2026-03 (per 1M tokens) — update when pricing changes
_FLASH_INPUT_COST_PER_M = 0.10
_FLASH_OUTPUT_COST_PER_M = 0.40


def _estimate_gemini_cost(token_stats: dict) -> float:
    """Estimate cost from Gemini token stats."""
    total_input = 0
    total_output = 0
    for model_stats in token_stats.values():
        if isinstance(model_stats, dict):
            total_input += model_stats.get("inputTokens", 0)
            total_output += model_stats.get("outputTokens", 0)
    input_cost = total_input * _FLASH_INPUT_COST_PER_M / 1_000_000
    output_cost = total_output * _FLASH_OUTPUT_COST_PER_M / 1_000_000
    return input_cost + output_cost


class GeminiAdapter:
    """Gemini CLI adapter. --prompt with value, -o json.

    Cost mode: estimated (JSON response includes token stats for estimation).
    """
    cli_name = "gemini"

    def __init__(self, model: str = "gemini-3-flash-preview", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.model_description = f"gemini {model}"
        self.cost_mode = "estimated"

    def available(self) -> bool:
        return shutil.which("gemini") is not None

    def prepare(self, prompt: str) -> InvocationContext:
        cmd = [
            "gemini",
            "-m", self.model,
            "-o", "json",
            "--approval-mode", "plan",
            # Always use empty --prompt + stdin to prevent flag injection
            "--prompt", "",
        ]

        return InvocationContext(
            command=cmd,
            stdin_data=prompt.encode("utf-8"),
            temp_files=[],
        )

    def parse_output(self, ctx: InvocationContext, stdout: bytes, stderr: bytes, returncode: int) -> ParseResult:
        text = stdout.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
            # Extract token stats for cost estimation
            cost = 0.0
            stats = data.get("stats", {})
            if isinstance(stats, dict):
                models = stats.get("models", {})
                cost = _estimate_gemini_cost(models)
            # Key confirmed as "response" (v9 correction)
            result = data.get("response", data.get("text", ""))
            if isinstance(result, str):
                return ParseResult(text=result.strip(), cost_usd=cost)
            return ParseResult(text=str(result).strip(), cost_usd=cost)
        except (json.JSONDecodeError, AttributeError):
            return ParseResult(text=CliOutputCleaner.clean(text))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_adapter(cli_name: str, **kwargs) -> CliAdapter:
    """Create an adapter by CLI name."""
    adapters = {
        "claude": ClaudeAdapter,
        "codex": CodexAdapter,
        "gemini": GeminiAdapter,
    }
    cls = adapters.get(cli_name)
    if cls is None:
        raise ValueError(f"Unknown CLI adapter: {cli_name}. Available: {list(adapters.keys())}")
    return cls(**kwargs)
