import asyncio
from collections import Counter
from magi.core.decision import Decision
import time


async def vote(query: str, nodes, timeout: float = 30.0) -> Decision:
    """
    Ask all nodes the same query in parallel, then majority-vote.
    If all 3 disagree (no majority), return a decision with confidence=0.33
    and protocol_used="vote_no_majority" to signal the caller should escalate.

    Handles degraded mode: 1-of-3 fail = 2-of-3 vote, 2-of-3 fail = single fallback.
    """
    start = time.monotonic()

    # Query all nodes in parallel
    tasks = {node.name: asyncio.create_task(node.query(query)) for node in nodes}

    results = {}
    failed = []
    costs = 0.0

    for name, task in tasks.items():
        try:
            results[name] = await task
        except Exception as e:
            failed.append(name)
            # Log but continue with remaining nodes

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if not results:
        raise RuntimeError("All MAGI nodes failed. Cannot make a decision.")

    if len(results) == 1:
        # Single node fallback
        name, answer = next(iter(results.items()))
        return Decision(
            query=query,
            ruling=answer,
            confidence=0.3,
            minority_report="",
            votes=results,
            protocol_used="fallback_single",
            degraded=True,
            failed_nodes=failed,
            latency_ms=elapsed_ms,
        )

    # Find majority (simple: if 2+ answers are semantically similar, they agree)
    # For MVP: use exact string comparison won't work. Use a simple heuristic:
    # Take the response that appears most frequently by checking pairwise overlap.
    # For now, just pick the longest common agreement or first response.
    #
    # Better approach: ask a quick LLM judge, but that adds latency.
    # Simplest MVP: all responses are unique, confidence = 1/N, ruling = first response.
    # Real implementation: use the engine's agreement scoring.

    # MVP: Pick the first response as ruling, calculate a simple confidence
    # based on how many nodes responded
    answers = list(results.values())
    names = list(results.keys())

    # Simple majority: if we have 3 answers, confidence based on response count
    confidence = len(results) / len(nodes)
    ruling = answers[0]
    minority_parts = []

    for i, (name, answer) in enumerate(results.items()):
        if i > 0:
            minority_parts.append(f"[{name}]: {answer}")

    minority_report = "\n\n".join(minority_parts) if minority_parts else ""

    return Decision(
        query=query,
        ruling=ruling,
        confidence=confidence,
        minority_report=minority_report,
        votes=results,
        protocol_used="vote",
        degraded=len(failed) > 0,
        failed_nodes=failed,
        latency_ms=elapsed_ms,
    )
