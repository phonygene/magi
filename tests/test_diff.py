"""Tests for magi diff command utilities."""
import pytest
from magi.commands.diff import check_diff_size, build_review_prompt, MAX_DIFF_REJECT, MAX_DIFF_WARN


def test_check_diff_size_ok():
    check_diff_size("small diff")  # should not raise


def test_check_diff_size_warn(capsys):
    big = "x" * (MAX_DIFF_WARN + 1)
    check_diff_size(big)  # warns but doesn't raise
    captured = capsys.readouterr()
    assert "warning" in captured.err


def test_check_diff_size_reject():
    huge = "x" * (MAX_DIFF_REJECT + 1)
    with pytest.raises(ValueError, match="too large"):
        check_diff_size(huge)


def test_build_review_prompt():
    prompt = build_review_prompt("+ added line\n- removed line")
    assert "code review" in prompt.lower()
    assert "+ added line" in prompt
    assert "HIGH" in prompt or "MEDIUM" in prompt or "LOW" in prompt
