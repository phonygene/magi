"""CritiqueProtocol (ICE — Iterative Consensus Ensemble).

Optimized with NeurIPS 2025 "Debate or Vote" findings:
- Error-detection framing instead of persuasion (88% vs 76% accuracy)
- Parallel critique rounds via asyncio.gather
- Synthesis phase for final ruling
- Improved agreement heuristic (bigram + position extraction)

Flow:
1. All nodes answer the query in parallel (round 0)
2. Each node sees the other two answers and critiques them using
   error-detection framing ("find errors") not persuasion ("agree/disagree")
3. Each node revises its answer based on critiques
4. Repeat until consensus (agreement_score > threshold) or max_rounds reached
5. Synthesizer node produces a final ruling incorporating all perspectives
"""
import asyncio
import re
import time

from magi.core.decision import Decision


# ── NeurIPS 2025 Error-Detection Prompt ──
# Key insight: persuasive debate is a martingale (expected value doesn't improve).
# Error-detection (ICE) achieves 88% vs single-model 76%.

_ICE_CRITIQUE_PROMPT = """\
Original question: {query}

Your previous answer:
{own_answer}

Other perspectives:
{others_text}

TASK: You are an error-detection reviewer, NOT a debater.

1. FIND ERRORS: Identify specific factual errors, logical flaws, unsupported \
claims, or missing considerations in EACH of the other perspectives above. \
Quote the exact problematic statement and explain why it is wrong.

2. SELF-CHECK: Apply the same error-detection to your OWN previous answer. \
What did you get wrong or miss?

3. REVISED ANSWER: Provide your corrected answer. Incorporate valid points \
from others. Fix any errors you found in your own reasoning. If your position \
hasn't changed, explain specifically why the criticisms don't apply.

Start with: ERRORS FOUND: (list errors)
Then: REVISED ANSWER: (your updated response)"""

_SYNTHESIS_PROMPT = """\
Original question: {query}

Three expert perspectives after {rounds} rounds of error-detection critique:

{all_answers}

TASK: Synthesize these perspectives into a single, authoritative answer.
- Incorporate the strongest points from each perspective
- Resolve any remaining disagreements by favoring the most evidence-backed position
- Note any significant dissent that the reader should be aware of
- Be comprehensive but concise

Provide your synthesis below."""


def _build_critique_prompt(query: str, own_answer: str, other_answers: dict[str, str]) -> str:
    """Build an ICE error-detection critique prompt (NeurIPS 2025 optimized)."""
    others_text = "\n\n".join(
        f"[{name}]: {answer}" for name, answer in other_answers.items()
    )
    return _ICE_CRITIQUE_PROMPT.format(
        query=query, own_answer=own_answer, others_text=others_text,
    )


def _build_synthesis_prompt(query: str, answers: dict[str, str], rounds: int) -> str:
    """Build a synthesis prompt for the final ruling."""
    all_answers = "\n\n".join(
        f"[{name}]: {answer}" for name, answer in answers.items()
    )
    return _SYNTHESIS_PROMPT.format(
        query=query, all_answers=all_answers, rounds=rounds,
    )


def _extract_revised_answer(response: str) -> str:
    """Extract the revised answer portion from an ICE critique response."""
    # Look for "REVISED ANSWER:" marker
    match = re.search(r"REVISED ANSWER:\s*(.*)", response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Fallback: return full response
    return response


async def estimate_agreement(answers: list[str], query: str = "") -> float:
    """Estimate agreement using LLM judge with multi-model fallback.

    All agreement scoring goes through the LLM judge — no lexical heuristics.
    The judge module handles fallback across multiple models (OpenRouter →
    Claude CLI → Gemini CLI → Codex CLI) internally.

    Args:
        answers: List of answer strings to compare.
        query: The original question (required for meaningful scoring).

    Returns:
        Agreement score 0.0-1.0. Returns 0.5 (uncertain) if all
        judge models fail.
    """
    import logging
    from magi.protocols.judge import llm_estimate_agreement

    if not query:
        query = "(no query provided — compare answers directly)"

    try:
        score, _ = await llm_estimate_agreement(query, answers)
        return score
    except Exception as e:
        logging.getLogger(__name__).warning(
            "All judge models failed, defaulting to 0.5: %s", e,
        )
        return 0.5


async def critique(
    query: str,
    nodes,
    max_rounds: int = 3,
    consensus_threshold: float = 0.8,
    synthesize: bool = True,
) -> Decision:
    """Run the ICE (Iterative Consensus Ensemble) protocol.

    Optimized with NeurIPS 2025 findings:
    - Error-detection framing in critique prompts
    - Parallel execution of critique rounds (asyncio.gather)
    - Optional synthesis phase for final ruling
    - Bigram-enhanced agreement heuristic

    Args:
        query: The user's question.
        nodes: List of MagiNode instances.
        max_rounds: Maximum critique rounds before forcing a result.
        consensus_threshold: agreement_score above which we stop early.
        synthesize: If True, run a synthesis step for the final ruling.

    Returns:
        A Decision with the synthesized result.
    """
    start = time.monotonic()
    failed = []
    mind_changes: list[str] = []

    # Round 0: initial parallel answers
    tasks = {node.name: asyncio.create_task(node.query(query)) for node in nodes}

    current_answers: dict[str, str] = {}
    for name, task in tasks.items():
        try:
            current_answers[name] = await task
        except Exception:
            failed.append(name)

    if not current_answers:
        raise RuntimeError("All MAGI nodes failed. Cannot make a decision.")

    if len(current_answers) == 1:
        name, answer = next(iter(current_answers.items()))
        return Decision(
            query=query,
            ruling=answer,
            confidence=0.3,
            minority_report="",
            votes=current_answers,
            protocol_used="fallback_single",
            degraded=True,
            failed_nodes=failed,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    # Track initial answers for mind_changes detection
    initial_answers = dict(current_answers)

    # Critique rounds — parallel execution with error-detection framing
    active_nodes = [n for n in nodes if n.name not in failed]
    agreement = await estimate_agreement(list(current_answers.values()), query=query)
    rounds_run = 0

    for round_num in range(max_rounds):
        if agreement >= consensus_threshold:
            break

        rounds_run = round_num + 1

        # Build all critique prompts
        critique_tasks = {}
        for node in active_nodes:
            if node.name not in current_answers:
                continue
            others = {k: v for k, v in current_answers.items() if k != node.name}
            prompt = _build_critique_prompt(query, current_answers[node.name], others)
            critique_tasks[node.name] = asyncio.create_task(node.query(prompt))

        # Collect revised answers IN PARALLEL (asyncio.gather)
        names = list(critique_tasks.keys())
        results = await asyncio.gather(
            *critique_tasks.values(), return_exceptions=True,
        )

        for name, result in zip(names, results):
            if isinstance(result, Exception):
                # Node failed mid-critique, keep its last answer
                continue
            revised = _extract_revised_answer(result)
            if revised != current_answers.get(name):
                current_answers[name] = revised

        agreement = await estimate_agreement(list(current_answers.values()), query=query)

    # Detect mind changes
    for name in current_answers:
        if name in initial_answers:
            initial_words = set(initial_answers[name].lower().split())
            final_words = set(current_answers[name].lower().split())
            if initial_words and final_words:
                overlap = len(initial_words & final_words) / len(initial_words | final_words)
                if overlap < 0.5:
                    mind_changes.append(name)

    # Synthesis phase: have first active node produce a unified ruling
    ruling_node = active_nodes[0].name
    if synthesize and len(current_answers) > 1 and rounds_run > 0:
        synthesis_prompt = _build_synthesis_prompt(query, current_answers, rounds_run)
        try:
            ruling = await active_nodes[0].query(synthesis_prompt)
            protocol_tag = f"critique_ice_synth_r{rounds_run}"
        except Exception:
            # Synthesis failed, fall back to first node's answer
            ruling = current_answers[ruling_node]
            protocol_tag = f"critique_ice_r{rounds_run}"
    else:
        ruling = current_answers[ruling_node]
        protocol_tag = f"critique_ice_r{rounds_run}"

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Minority report: other answers that differ
    minority_parts = [
        f"[{name}]: {answer}"
        for name, answer in current_answers.items()
        if name != ruling_node
    ]

    return Decision(
        query=query,
        ruling=ruling,
        confidence=agreement,
        minority_report="\n\n".join(minority_parts),
        votes=current_answers,
        mind_changes=mind_changes,
        protocol_used=protocol_tag,
        degraded=len(failed) > 0,
        failed_nodes=failed,
        latency_ms=elapsed_ms,
    )
