"""REFINE Collator — ad-hoc LiteLLM call for objection consolidation (D1, D2).

Spec: .omc/plans/refine-mode-proposal-v4.md §2.1 + §4 Collator Prompt.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

import litellm

from magi.protocols.refine_prompts import build_collator
from magi.protocols.refine_types import Objection


# ---------------------------------------------------------------------------
# D2 — fallback_consolidate
# ---------------------------------------------------------------------------


def fallback_consolidate(raw_objections: list[Objection]) -> list[dict]:
    """Wrap each raw Objection into the ConsolidatedObjection schema.

    Used when Collator fails twice — guarantees Primary always sees one
    input shape regardless of whether collation succeeded.
    """
    return [
        {
            "issue_key": o.issue_key,
            "category": o.category,
            "severity": o.severity,
            "target": o.target,
            "description": o.description,
            "suggestions": [{"reviewer": o.reviewer, "text": o.suggestion or ""}],
            "conflicting_suggestions": False,
            "source_reviewers": [o.reviewer],
            "source_issue_keys": [o.issue_key],
        }
        for o in raw_objections
    ]


# ---------------------------------------------------------------------------
# D1 — collate_objections
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _parse_collator_response(content: str) -> list[dict]:
    """Extract a JSON array from collator output (supports fenced or bare)."""
    content = content.strip()
    if not content:
        raise ValueError("empty collator response")
    m = _FENCE_RE.search(content)
    payload = m.group(1).strip() if m else content
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError(f"collator did not return a JSON array (got {type(data).__name__})")
    return data


def _resolve_collator_model(collator_model: str | None, reviewer_nodes: list) -> str | None:
    """Model selection per §2.1.

    Returns the model string or None to signal "skip collation, use fallback".
    """
    if collator_model:
        return collator_model
    # Lazy import to avoid circular dependency if cli_node imports anything
    # from protocols.
    try:
        from magi.core.cli_node import CliNode
    except Exception:
        CliNode = None  # pragma: no cover

    if CliNode is not None and any(isinstance(n, CliNode) for n in reviewer_nodes):
        return None  # CLI node — skip
    if not reviewer_nodes:
        return None
    return getattr(reviewer_nodes[0], "model", None)


async def collate_objections(
    objections: list[Objection],
    round_num: int,
    config: Any,
    reviewer_nodes: list,
) -> tuple[list[dict], float, bool]:
    """Run the Collator LLM call.

    Returns ``(consolidated, cost_usd, collator_failed)``.

    - If the Collator is skipped (CLI node or no model), returns the fallback
      shape immediately with ``collator_failed=True`` so the caller records it.
    - On parse failure, retries once with a schema hint. Second failure →
      returns fallback with ``collator_failed=True``.
    """
    model = _resolve_collator_model(
        getattr(config, "collator_model", None), reviewer_nodes,
    )

    if not objections:
        return [], 0.0, False

    if model is None:
        # Skip path (CLI node or misconfigured) — fallback used.
        return fallback_consolidate(objections), 0.0, True

    # Serialize all reviewer objections as JSON input.
    raw_payload = [asdict(o) for o in objections]
    prompt = build_collator(round_num, raw_payload)
    total_cost = 0.0

    async def _one_call(extra_hint: str = "") -> tuple[list[dict], float]:
        final_prompt = prompt + (f"\n\n{extra_hint}" if extra_hint else "")
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": final_prompt}],
        )
        msg = resp.choices[0].message
        content = (msg.content or "").strip()
        if not content and hasattr(msg, "reasoning_content"):
            content = (msg.reasoning_content or "").strip()
        try:
            call_cost = litellm.completion_cost(completion_response=resp)
        except Exception:
            call_cost = 0.0
        return _parse_collator_response(content), call_cost or 0.0

    # First attempt.
    try:
        consolidated, cost1 = await _one_call()
        total_cost += cost1
        return consolidated, total_cost, False
    except Exception:
        pass

    # Retry with schema hint.
    try:
        schema_hint = (
            "Your previous response could not be parsed. "
            "Respond ONLY with a JSON array matching the schema shown above — "
            "no prose, no extra text. Wrap it in a ```json fence."
        )
        consolidated, cost2 = await _one_call(schema_hint)
        total_cost += cost2
        return consolidated, total_cost, False
    except Exception:
        # Second failure — fallback.
        return fallback_consolidate(objections), total_cost, True
