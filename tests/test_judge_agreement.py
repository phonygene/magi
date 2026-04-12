"""Tests for LLM-as-Judge agreement scoring (magi.protocols.judge)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from magi.protocols.judge import (
    _parse_judge_response,
    llm_estimate_agreement,
    get_judge_model,
)
from magi.protocols.critique import estimate_agreement


class TestParseJudgeResponse:
    def test_parse_full_agreement(self):
        text = "CONCLUSION: yes\nAGREEMENT_SCORE: 0.95\nDISSENT_SUMMARY: none"
        score, dissent = _parse_judge_response(text)
        assert score == 0.95
        assert dissent is None

    def test_parse_partial_agreement(self):
        text = "CONCLUSION: partial\nAGREEMENT_SCORE: 0.55\nDISSENT_SUMMARY: They disagree on the timeline."
        score, dissent = _parse_judge_response(text)
        assert score == 0.55
        assert dissent == "They disagree on the timeline."

    def test_parse_no_agreement(self):
        text = "CONCLUSION: no\nAGREEMENT_SCORE: 0.1\nDISSENT_SUMMARY: Completely opposite conclusions."
        score, dissent = _parse_judge_response(text)
        assert score == 0.1
        assert dissent == "Completely opposite conclusions."

    def test_clamp_score_above_1(self):
        text = "CONCLUSION: yes\nAGREEMENT_SCORE: 1.5\nDISSENT_SUMMARY: none"
        score, _ = _parse_judge_response(text)
        assert score == 1.0

    def test_clamp_score_below_0(self):
        text = "CONCLUSION: no\nAGREEMENT_SCORE: -0.3\nDISSENT_SUMMARY: none"
        score, _ = _parse_judge_response(text)
        assert score == 0.0

    def test_parse_with_extra_text(self):
        text = "Let me evaluate...\nCONCLUSION: yes\nAGREEMENT_SCORE: 0.85\nDISSENT_SUMMARY: none\nExtra."
        score, dissent = _parse_judge_response(text)
        assert score == 0.85
        assert dissent is None

    def test_parse_failure_no_score(self):
        with pytest.raises(ValueError, match="Could not parse"):
            _parse_judge_response("Random text without format.")

    def test_dissent_none_string(self):
        text = "CONCLUSION: yes\nAGREEMENT_SCORE: 0.9\nDISSENT_SUMMARY: None"
        _, dissent = _parse_judge_response(text)
        assert dissent is None


class TestGetJudgeModel:
    def test_default_model(self):
        import os
        original = os.environ.pop("MAGI_JUDGE_MODEL", None)
        try:
            assert get_judge_model() == "openrouter/stepfun/step-3.5-flash:free"
        finally:
            if original:
                os.environ["MAGI_JUDGE_MODEL"] = original

    def test_custom_model(self):
        with patch.dict("os.environ", {"MAGI_JUDGE_MODEL": "openrouter/custom/model"}):
            assert get_judge_model() == "openrouter/custom/model"


@pytest.mark.asyncio
async def test_llm_estimate_single_answer():
    score, dissent = await llm_estimate_agreement("test", ["only one"])
    assert score == 1.0
    assert dissent is None


@pytest.mark.asyncio
async def test_llm_estimate_calls_litellm():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "CONCLUSION: yes\nAGREEMENT_SCORE: 0.9\nDISSENT_SUMMARY: none"
    )

    with patch("magi.protocols.judge.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
        score, dissent = await llm_estimate_agreement(
            "Is water wet?",
            ["Yes it is", "Indeed water is wet", "Absolutely yes"],
        )
        assert score == 0.9
        assert dissent is None
        mock_llm.assert_called_once()
        call_args = mock_llm.call_args
        assert call_args.kwargs["temperature"] == 0.0
        assert call_args.kwargs["max_tokens"] == 16000


@pytest.mark.asyncio
async def test_llm_estimate_two_answers():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "CONCLUSION: partial\nAGREEMENT_SCORE: 0.6\nDISSENT_SUMMARY: Different emphasis."
    )

    with patch("magi.protocols.judge.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        score, dissent = await llm_estimate_agreement(
            "test question",
            ["Answer A", "Answer B"],
        )
        assert score == 0.6
        assert dissent == "Different emphasis."


@pytest.mark.asyncio
async def test_llm_fallback_to_cli():
    """Primary API fails, falls back to CLI judge."""
    async def mock_cli_judge(cli_name, prompt, timeout=60.0):
        return 0.85, None

    with patch("magi.protocols.judge.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("OpenRouter down")):
        with patch("magi.protocols.judge._call_cli_judge", side_effect=mock_cli_judge) as mock_cli:
            score, dissent = await llm_estimate_agreement(
                "test question",
                ["Answer A", "Answer B", "Answer C"],
            )
            assert score == 0.85
            assert dissent is None
            # First CLI fallback (claude) should have been tried
            mock_cli.assert_called_once()
            assert mock_cli.call_args[0][0] == "claude"


@pytest.mark.asyncio
async def test_llm_fallback_chain_order():
    """CLI fallbacks are tried in order: claude → gemini → codex."""
    cli_calls = []

    async def mock_cli_judge(cli_name, prompt, timeout=60.0):
        cli_calls.append(cli_name)
        if cli_name in ("claude", "gemini"):
            raise RuntimeError(f"{cli_name} unavailable")
        return 0.7, "Minor differences"

    with patch("magi.protocols.judge.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("API down")):
        with patch("magi.protocols.judge._call_cli_judge", side_effect=mock_cli_judge):
            score, dissent = await llm_estimate_agreement(
                "test", ["A", "B"],
            )
            assert score == 0.7
            assert cli_calls == ["claude", "gemini", "codex"]


@pytest.mark.asyncio
async def test_llm_all_fail():
    """All judge models (API + CLI) fail raises RuntimeError."""
    async def mock_cli_fail(cli_name, prompt, timeout=60.0):
        raise RuntimeError(f"{cli_name} down")

    with patch("magi.protocols.judge.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("API down")):
        with patch("magi.protocols.judge._call_cli_judge", side_effect=mock_cli_fail):
            with pytest.raises(RuntimeError, match="All judge models failed"):
                await llm_estimate_agreement("test", ["A", "B"])


@pytest.mark.asyncio
async def test_estimate_agreement_no_query_still_uses_judge():
    """Without query, still uses LLM judge (with placeholder query)."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "CONCLUSION: no\nAGREEMENT_SCORE: 0.15\nDISSENT_SUMMARY: Completely different."
    )

    with patch("magi.protocols.judge.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
        score = await estimate_agreement(["alpha beta", "gamma delta", "epsilon zeta"])
        mock_llm.assert_called_once()
        assert score == 0.15
