"""magi diff — multi-model code review."""
import subprocess
import sys


MAX_DIFF_WARN = 50_000   # 50KB warning
MAX_DIFF_REJECT = 200_000  # 200KB hard limit


def get_git_diff(staged: bool = False, file_path: str | None = None) -> str:
    """Read diff from git. Returns diff text or raises."""
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--staged")
    if file_path:
        cmd.extend(["--", file_path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        raise RuntimeError("Not inside a git repository, or git is not installed.")
    except FileNotFoundError:
        raise RuntimeError("git is not installed.")

    diff = result.stdout
    if not diff.strip():
        if staged:
            raise ValueError("Nothing staged. Run `git add` first, or use `magi diff <file>`.")
        else:
            raise ValueError("No changes detected. Specify a file or use --staged.")

    return diff


def check_diff_size(diff: str) -> None:
    """Warn or reject oversized diffs."""
    size = len(diff.encode("utf-8"))
    if size > MAX_DIFF_REJECT:
        raise ValueError(
            f"Diff is too large ({size // 1000}KB > {MAX_DIFF_REJECT // 1000}KB). "
            "Consider splitting into smaller PRs."
        )
    if size > MAX_DIFF_WARN:
        print(
            f"[magi] warning: large diff ({size // 1000}KB). "
            "Review quality may be reduced for very large diffs.",
            file=sys.stderr,
        )


def build_review_prompt(diff: str) -> str:
    """Build the prompt for multi-model code review."""
    return (
        "You are reviewing the following code diff. Provide a thorough code review.\n"
        "Focus on: bugs, security issues, performance problems, readability, and best practices.\n"
        "Be specific — reference line numbers and explain WHY something is an issue.\n"
        "Rate each issue as HIGH / MEDIUM / LOW severity.\n"
        "At the end, give an overall assessment: APPROVE, REQUEST_CHANGES, or COMMENT.\n\n"
        f"```diff\n{diff}\n```"
    )


def format_review_output(decision) -> str:
    """Format a Decision into a readable multi-model review report."""
    lines = []
    lines.append("=" * 60)
    lines.append("MAGI CODE REVIEW — Three Models, One Assessment")
    lines.append("=" * 60)
    lines.append("")

    # Show each model's review
    for node_name, review in decision.votes.items():
        lines.append(f"── {node_name.upper()} ──")
        lines.append(review)
        lines.append("")

    # Disagreement analysis
    lines.append("=" * 60)
    lines.append("DISAGREEMENT ANALYSIS")
    lines.append("=" * 60)
    lines.append(f"Confidence: {decision.confidence:.0%}")
    lines.append(f"Protocol: {decision.protocol_used}")
    if decision.degraded:
        lines.append(f"⚠ Degraded mode — failed nodes: {', '.join(decision.failed_nodes)}")
    lines.append("")

    if decision.minority_report:
        lines.append("MINORITY REPORT (dissenting views):")
        lines.append(decision.minority_report)
    else:
        lines.append("All models agree on the assessment.")

    lines.append("")
    lines.append(f"Trace ID: {decision.trace_id}")
    lines.append(f"Latency: {decision.latency_ms}ms")
    cost_mode = getattr(decision, "cost_mode", "measured")
    if cost_mode == "unavailable":
        lines.append("Cost: N/A")
    elif cost_mode == "estimated" and decision.cost_usd > 0:
        lines.append(f"Cost: ~${decision.cost_usd:.4f} (est.)")
    elif decision.cost_usd > 0:
        lines.append(f"Cost: ${decision.cost_usd:.4f}")

    return "\n".join(lines)
