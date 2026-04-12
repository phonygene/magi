"""CLI-backed MAGI node implementing the same interface as MagiNode.

CliNode uses subprocess isolation to run local CLI tools (claude, codex, gemini)
as MAGI decision nodes. No provider API keys required — uses CLI subscriptions.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

from magi.core.cli_adapters import CliAdapter, InvocationContext
from magi.core.cli_errors import (
    MagiCliAuthError,
    MagiCliExecutionError,
    MagiNodeTimeoutError,
    MagiProviderNotFoundError,
)
from magi.core.node import Persona

# CLI-native mode uses CLI login/OAuth only — strip all provider API keys.
# Node.js CLIs on Windows need many system env vars to initialize;
# allowlisting is fragile, so we blocklist credentials instead.
_CREDENTIAL_KEYS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"}


class CliNode:
    """CLI-backed MAGI node.

    Implements the same interface as MagiNode (name, model, persona, last_cost_usd, query).
    Uses CliAdapter for per-provider behavior and InvocationContext for concurrency safety.
    """

    def __init__(
        self,
        name: str,
        persona: Persona,
        adapter: CliAdapter,
        timeout: float = 600.0,
    ):
        self.name = name
        self.persona = persona
        self.model = adapter.model_description
        self.last_cost_usd: float = 0.0
        self.cost_mode: str = adapter.cost_mode
        self.adapter = adapter
        self.timeout = timeout

    def preflight_check(self) -> bool:
        """Check if the CLI tool is available on PATH."""
        return self.adapter.available()

    async def query(self, prompt: str) -> str:
        """Send a query to this CLI node. Returns the response text."""
        full_prompt = self._build_prompt(prompt)
        ctx = self.adapter.prepare(full_prompt)

        try:
            stdout, stderr, returncode = await self._run_isolated(ctx)

            # Check for auth errors before general execution errors
            if returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if MagiCliAuthError.check_stderr(self.adapter.cli_name, stderr_text):
                    raise MagiCliAuthError(self.adapter.cli_name, stderr_text)
                raise MagiCliExecutionError(
                    self.adapter.cli_name, returncode, stderr_text
                )

            parsed = self.adapter.parse_output(ctx, stdout, stderr, returncode)

            # Update cost from parse result (no shared mutable state on adapter)
            self.last_cost_usd = parsed.cost_usd

            if not parsed.text or not parsed.text.strip():
                raise ValueError(f"Node {self.name} returned empty response")

            return parsed.text

        except asyncio.TimeoutError:
            raise MagiNodeTimeoutError(self.name, self.timeout)
        finally:
            ctx.cleanup()

    async def _run_isolated(self, ctx: InvocationContext) -> tuple[bytes, bytes, int]:
        """Run CLI command in isolated subprocess with least-privilege env.

        Uses subprocess.Popen + run_in_executor instead of asyncio.create_subprocess_exec
        to avoid a Python 3.10 ProactorEventLoop bug on Windows that causes native
        ACCESS_VIOLATION (0xc0000005) crashes in ntdll.dll during pipe transport cleanup.
        """
        # Strip all provider API keys — CLI-native mode uses CLI login only
        env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_KEYS}
        env["MAGI_NODE_MODE"] = "1"

        # Windows: .cmd/.bat wrappers (npm-installed CLIs) need cmd.exe /c
        command = list(ctx.command)
        if sys.platform == "win32":
            resolved = shutil.which(command[0])
            if resolved and resolved.lower().endswith((".cmd", ".bat")):
                command = ["cmd.exe", "/c"] + command

        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        )

        loop = asyncio.get_event_loop()

        def _run_blocking():
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    proc = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE if ctx.stdin_data else None,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=tmpdir,
                        env=env,
                        creationflags=creation_flags,
                    )
                except FileNotFoundError:
                    raise MagiProviderNotFoundError(self.adapter.cli_name)

                try:
                    stdout, stderr = proc.communicate(
                        input=ctx.stdin_data,
                        timeout=self.timeout,
                    )
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise asyncio.TimeoutError()

            return stdout, stderr, proc.returncode

        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_blocking),
            timeout=self.timeout + 5,  # slight buffer over Popen timeout
        )

    def _build_prompt(self, query: str) -> str:
        """Build full prompt with persona context."""
        parts = [
            f"[INSTRUCTION] You are {self.name}. Follow the instructions precisely.",
            f"Your role: {self.persona.system_prompt}",
            f"Question: {query}",
        ]
        return "\n\n".join(parts)
