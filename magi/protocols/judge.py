"""LLM-as-Judge for semantic agreement scoring.

Replaces the lexical Jaccard heuristic with a single low-cost LLM call
that understands whether three answers actually agree or disagree,
regardless of wording differences.

Fallback chain (tries in order until one succeeds):
1. OpenRouter API via litellm (MAGI_JUDGE_MODEL env var, default: stepfun/step-3.5-flash:free)
2. Claude CLI (haiku tier) — local OAuth subscription
3. Gemini CLI — local OAuth subscription
4. Codex CLI — local OAuth subscription
"""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

import litellm

logger = logging.getLogger(__name__)

_DEFAULT_JUDGE_MODEL = "openrouter/stepfun/step-3.5-flash:free"

_JUDGE_PROMPT = """\
You are a consensus evaluator. Given three expert answers to the same question, \
assess whether they reach the same conclusion.

Question: {query}

Answer A (Melchior):
{answer_a}

Answer B (Balthasar):
{answer_b}

Answer C (Casper):
{answer_c}

Evaluate:
1. Do the three answers reach the SAME core conclusion? (yes/partial/no)
2. Rate the agreement level from 0.0 to 1.0:
   - 1.0 = identical conclusions (even if worded differently)
   - 0.7-0.9 = same direction with minor differences
   - 0.4-0.6 = partial agreement, some key disagreements
   - 0.1-0.3 = mostly disagree on core points
   - 0.0 = completely contradictory conclusions
3. If disagreement exists, describe the core divergence in one sentence.

Respond in EXACTLY this format (no other text):
CONCLUSION: yes|partial|no
AGREEMENT_SCORE: <float between 0.0 and 1.0>
DISSENT_SUMMARY: <one sentence or "none">\
"""

_JUDGE_PROMPT_TWO = """\
You are a consensus evaluator. Given two expert answers to the same question, \
assess whether they reach the same conclusion.

Question: {query}

Answer A:
{answer_a}

Answer B:
{answer_b}

Evaluate their agreement level.

Respond in EXACTLY this format (no other text):
CONCLUSION: yes|partial|no
AGREEMENT_SCORE: <float between 0.0 and 1.0>
DISSENT_SUMMARY: <one sentence or "none">\
"""

# Patterns for parsing judge response
_SCORE_RE = re.compile(r"AGREEMENT_SCORE:\s*(-?[\d.]+)", re.IGNORECASE)
_DISSENT_RE = re.compile(r"DISSENT_SUMMARY:\s*(.+)", re.IGNORECASE)


def get_judge_model() -> str:
    """Get the judge model from env var or default."""
    return os.environ.get("MAGI_JUDGE_MODEL", _DEFAULT_JUDGE_MODEL)


def get_judge_options() -> list[str]:
    """Return available judge model choices for the UI dropdown."""
    return [
        "openrouter/stepfun/step-3.5-flash:free",
        "openrouter/deepseek/deepseek-v3.2:free",
        "openrouter/google/gemini-2.5-flash-preview:thinking",
        "claude-cli-haiku",
        "gemini-cli",
        "codex-cli",
    ]


def _parse_judge_response(text: str) -> tuple[float, str | None]:
    """Parse the structured judge response.

    Returns (score, dissent_summary). Raises ValueError on parse failure.
    """
    score_match = _SCORE_RE.search(text)
    if not score_match:
        raise ValueError(f"Could not parse AGREEMENT_SCORE from judge response: {text[:200]}")

    score = float(score_match.group(1))
    score = max(0.0, min(1.0, score))  # clamp to [0, 1]

    dissent = None
    dissent_match = _DISSENT_RE.search(text)
    if dissent_match:
        d = dissent_match.group(1).strip()
        if d.lower() != "none":
            dissent = d

    return score, dissent


async def _call_api_judge(
    model: str,
    prompt: str,
    timeout: float,
) -> tuple[float, str | None]:
    """Call a judge model via litellm API. Raises on any failure."""
    response = await asyncio.wait_for(
        litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=16000,
            num_retries=1,
        ),
        timeout=timeout,
    )

    msg = response.choices[0].message
    content = msg.content
    # Reasoning models (e.g. stepfun) put output in reasoning_content
    if not content and hasattr(msg, "reasoning_content") and msg.reasoning_content:
        content = msg.reasoning_content
    if not content or not content.strip():
        raise ValueError(f"Judge ({model}) returned empty response")

    return _parse_judge_response(content.strip())


async def _call_cli_judge(
    cli_name: str,
    prompt: str,
    timeout: float,
) -> tuple[float, str | None]:
    """Call a local CLI tool as judge fallback via subprocess.

    Uses the same CLI adapters as MAGI CLI-native mode but lightweight —
    no persona wrapping, just raw prompt in / text out.
    """
    from magi.core.cli_adapters import ClaudeAdapter, GeminiAdapter, CodexAdapter

    adapters = {
        "claude": lambda: ClaudeAdapter(model_tier="haiku", effort="low"),
        "gemini": lambda: GeminiAdapter(),
        "codex": lambda: CodexAdapter(effort="low"),
    }

    factory = adapters.get(cli_name)
    if not factory:
        raise ValueError(f"Unknown CLI judge: {cli_name}")

    adapter = factory()
    if not adapter.available():
        raise RuntimeError(f"CLI '{cli_name}' not found on PATH")

    ctx = adapter.prepare(prompt)
    try:
        # Build command with Windows .cmd wrapper handling
        command = list(ctx.command)
        if sys.platform == "win32":
            resolved = shutil.which(command[0])
            if resolved and resolved.lower().endswith((".cmd", ".bat")):
                command = ["cmd.exe", "/c"] + command

        # Strip API keys — CLI uses OAuth only
        env = {k: v for k, v in os.environ.items()
               if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"}}

        # On Windows, isolate subprocess console to prevent signal propagation
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        )

        # Use subprocess.Popen + run_in_executor to avoid Python 3.10
        # ProactorEventLoop bug (ACCESS_VIOLATION in ntdll.dll pipe transport).
        loop = asyncio.get_event_loop()

        def _run_blocking():
            with tempfile.TemporaryDirectory() as tmpdir:
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if ctx.stdin_data else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=tmpdir,
                    env=env,
                    creationflags=creation_flags,
                )
                try:
                    stdout, stderr = proc.communicate(
                        input=ctx.stdin_data,
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise asyncio.TimeoutError()
                return stdout, stderr, proc.returncode

        stdout, stderr, returncode = await asyncio.wait_for(
            loop.run_in_executor(None, _run_blocking),
            timeout=timeout + 5,
        )

        if returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"CLI judge '{cli_name}' exited {returncode}: {stderr_text}")

        parsed = adapter.parse_output(ctx, stdout, stderr, returncode)
        if not parsed.text or not parsed.text.strip():
            raise ValueError(f"CLI judge '{cli_name}' returned empty response")

        return _parse_judge_response(parsed.text.strip())
    finally:
        ctx.cleanup()


# Fallback chain: CLI name → description for logging
_CLI_FALLBACKS = [
    ("claude", "Claude CLI (haiku)"),
    ("gemini", "Gemini CLI"),
    ("codex", "Codex CLI"),
]


async def llm_estimate_agreement(
    query: str,
    answers: list[str],
    timeout: float = 15.0,
    model_override: str | None = None,
) -> tuple[float, str | None]:
    """Estimate agreement using an LLM judge with multi-model fallback.

    Fallback chain:
    1. OpenRouter API via litellm (primary, free model)
    2. Claude CLI haiku (local OAuth subscription)
    3. Gemini CLI (local OAuth subscription)
    4. Codex CLI (local OAuth subscription)

    Args:
        query: The original question asked.
        answers: List of 2-3 answer strings.
        timeout: Max seconds to wait per judge attempt.
        model_override: If set, use this model instead of the default.
            CLI models: "claude-cli-haiku", "gemini-cli", "codex-cli".
            Otherwise treated as a litellm model ID.

    Returns:
        (agreement_score, dissent_summary) where score is 0.0-1.0
        and dissent_summary is None if answers agree.

    Raises:
        RuntimeError if ALL judge models fail.
    """
    if len(answers) < 2:
        return 1.0, None

    # Build prompt once, reuse across fallback attempts
    max_len = 1500
    truncated = [a[:max_len] for a in answers]

    if len(truncated) == 2:
        prompt = _JUDGE_PROMPT_TWO.format(
            query=query,
            answer_a=truncated[0],
            answer_b=truncated[1],
        )
    else:
        prompt = _JUDGE_PROMPT.format(
            query=query,
            answer_a=truncated[0],
            answer_b=truncated[1],
            answer_c=truncated[2] if len(truncated) > 2 else "(no third answer)",
        )

    last_error = None

    # If a specific CLI model is requested, try only that
    _CLI_MODEL_MAP = {
        "claude-cli-haiku": "claude",
        "gemini-cli": "gemini",
        "codex-cli": "codex",
    }
    if model_override and model_override in _CLI_MODEL_MAP:
        cli_name = _CLI_MODEL_MAP[model_override]
        return await _call_cli_judge(cli_name, prompt, timeout=60.0)

    # Step 1: Try OpenRouter API (free, fast)
    primary = model_override or get_judge_model()
    try:
        return await _call_api_judge(primary, prompt, timeout)
    except Exception as e:
        logger.warning("Judge API (%s) failed: %s", primary, e)
        last_error = e

    # If user explicitly chose an API model, don't fall back to CLI
    if model_override:
        raise RuntimeError(f"Judge model '{model_override}' failed: {last_error}")

    # Step 2: Try local CLI fallbacks (OAuth subscriptions)
    for cli_name, description in _CLI_FALLBACKS:
        try:
            result = await _call_cli_judge(cli_name, prompt, timeout=60.0)
            logger.info("Judge fallback succeeded with %s", description)
            return result
        except Exception as e:
            logger.warning("Judge fallback %s failed: %s", description, e)
            last_error = e
            continue

    raise RuntimeError(
        f"All judge models failed (API + CLI fallbacks). Last error: {last_error}"
    )
