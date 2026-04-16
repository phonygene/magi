"""REFINE protocol — primary-reviewer iterative refinement (G1-G3).

Spec: .omc/plans/refine-mode-proposal-v4.md §1-§11.
All LLM calls are async; tests mock them via `AsyncMock` on node.query().
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from typing import Any

from magi.core.decision import Decision
from magi.protocols.refine_collator import collate_objections, fallback_consolidate
from magi.protocols.refine_convergence import (
    TERMINAL_ABORTED,
    TERMINAL_BUDGET,
    TERMINAL_CANCELLED,
    TERMINAL_CONVERGED,
    TERMINAL_MAX_ROUNDS,
    TERMINAL_THRESHOLD,
    check_convergence,
    check_sycophancy,
    compute_refine_confidence,
    compute_refine_minority_report,
    compute_refine_votes,
    track_best_round,
)
from magi.protocols.refine_keys import (
    canonicalize_key,
    merge_similar_keys,
    reconcile_cross_round,
)
from magi.protocols.refine_prompts import (
    build_primary_initial,
    build_primary_reflection,
    build_reviewer,
    format_decisions_summary,
)
from magi.protocols.refine_types import (
    IssueTracker,
    Objection,
    RefineConfig,
    RefineRound,
    Reflection,
    UserAction,
    UserOverride,
)


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)
_REVISED_RE = re.compile(r"REVISED_PROPOSAL:\s*(.+)$", re.DOTALL)


def _extract_json_list(text: str) -> list:
    """Pull a JSON array out of fenced or bare LLM output."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    m = _FENCE_RE.search(text)
    candidate = m.group(1).strip() if m else text
    # If we still have prose, find the first [ ... last ]
    if not candidate.startswith("["):
        lb = candidate.find("[")
        rb = candidate.rfind("]")
        if lb == -1 or rb <= lb:
            raise ValueError("no JSON array found in response")
        candidate = candidate[lb : rb + 1]
    data = json.loads(candidate)
    if not isinstance(data, list):
        raise ValueError(f"expected JSON array, got {type(data).__name__}")
    return data


def _parse_reviewer_response(
    raw: str,
    reviewer_name: str,
    round_num: int,
) -> list[Objection]:
    """Parse a reviewer's JSON objection list into Objection dataclasses."""
    data = _extract_json_list(raw)
    out: list[Objection] = []
    for seq, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            continue
        candidate = entry.get("candidate_key") or entry.get("issue_key") or ""
        canon = canonicalize_key(candidate) or f"unknown_issue_r{round_num}_{reviewer_name}_{seq:02d}"
        out.append(Objection(
            id=f"R{round_num}-{reviewer_name}-{seq:02d}",
            candidate_key=str(candidate),
            issue_key=canon,
            reviewer=reviewer_name,
            category=str(entry.get("category", "improvement")),
            severity=str(entry.get("severity", "minor")),
            target=str(entry.get("target", "")),
            description=str(entry.get("issue") or entry.get("description", "")),
            suggestion=entry.get("suggestion"),
        ))
    return out


def _parse_reflection_response(raw: str) -> tuple[list[Reflection], str]:
    """Return ``(reflections, revised_proposal_text)``.

    Reflections are the JSON array; the REVISED_PROPOSAL text trails it.
    If the REVISED_PROPOSAL marker is missing we use the leftover text.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty reflection response")

    # Extract reflections JSON (first fenced block, or from position 0 to first `]`).
    m = _FENCE_RE.search(raw)
    if m:
        json_text = m.group(1).strip()
        remainder = raw[m.end():]
    else:
        # Bare form — look for first `[`, match to `]`.
        lb = raw.find("[")
        if lb == -1:
            raise ValueError("no JSON array in reflection")
        # scan forward for matching closing bracket (shallow — array always top-level here)
        depth = 0
        end = -1
        for i in range(lb, len(raw)):
            if raw[i] == "[":
                depth += 1
            elif raw[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            raise ValueError("unterminated JSON array in reflection")
        json_text = raw[lb : end + 1]
        remainder = raw[end + 1 :]

    data = json.loads(json_text)
    if not isinstance(data, list):
        raise ValueError("reflection JSON is not an array")

    reflections: list[Reflection] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        reflections.append(Reflection(
            consolidated_id=str(entry.get("consolidated_id", "0")),
            source_issue_keys=list(entry.get("source_issue_keys") or []),
            verdict=str(entry.get("verdict", "reject")),
            reasoning=str(entry.get("reasoning", "")),
            chosen_suggestion=entry.get("chosen_suggestion"),
            change_summary=entry.get("change_summary"),
            conflict_check=entry.get("conflict_check"),
            severity_after=entry.get("severity_after"),
        ))

    # Extract revised proposal text.
    rev_match = _REVISED_RE.search(remainder)
    if rev_match:
        proposal = rev_match.group(1).strip()
    else:
        proposal = remainder.strip()
    return reflections, proposal


# ---------------------------------------------------------------------------
# G1 — Primary / parse failure helpers
# ---------------------------------------------------------------------------


async def handle_primary_failure(
    error: Exception,
    nodes: list,
    primary_index: int,
    current_proposal: str,
    current_round: int,
    retry_prompt: str,
) -> tuple[str | None, bool, float]:
    """One retry of the primary call.

    Returns ``(new_proposal, degraded, cost_delta)``. ``cost_delta`` is the
    retry-call cost read immediately from ``node.last_cost_usd`` (R02 MAJOR-5)
    — callers must add it to their round/total totals. Degraded == True means
    primary still failed; caller should abort with the current proposal as the
    last known good state.
    """
    primary = nodes[primary_index]
    try:
        new_proposal = await primary.query(retry_prompt)
        cost_delta = getattr(primary, "last_cost_usd", 0.0) or 0.0
        return new_proposal, False, cost_delta
    except Exception:
        cost_delta = getattr(primary, "last_cost_usd", 0.0) or 0.0
        return current_proposal, True, cost_delta


# Note: parse-failure retry is inlined into ``_reviewer_call`` below rather
# than exposed as a standalone helper (D4 separation remains intact —
# parse_error does not increment rejected_count).


# ---------------------------------------------------------------------------
# G2 + G3 — Main protocol loop (with GUIDED integration)
# ---------------------------------------------------------------------------


async def _reviewer_call(
    node,
    prompt: str,
    round_num: int,
) -> tuple[list[Objection] | None, bool, float]:
    """Run one reviewer + parse. Returns ``(objections, parse_error, cost)``."""
    try:
        raw = await node.query(prompt)
    except Exception:
        return None, True, getattr(node, "last_cost_usd", 0.0) or 0.0

    cost = getattr(node, "last_cost_usd", 0.0) or 0.0
    try:
        objs = _parse_reviewer_response(raw, node.name, round_num)
        return objs, False, cost
    except Exception:
        # Parse-retry with schema hint.
        schema_hint = (
            "\n\nYour previous response could not be parsed. "
            "Respond ONLY with a JSON array of objection objects inside a ```json fence, "
            "with no other text."
        )
        try:
            raw2 = await node.query(prompt + schema_hint)
            cost += getattr(node, "last_cost_usd", 0.0) or 0.0
            objs = _parse_reviewer_response(raw2, node.name, round_num)
            return objs, False, cost
        except Exception:
            return None, True, cost


async def _run_guided_callback(
    config: RefineConfig,
    round_num: int,
    proposal: str,
    reflections: list[Reflection],
    tracker: IssueTracker,
) -> tuple[UserAction | None, bool, str | None]:
    """Invoke on_user_review with timeout + exception policy.

    Returns ``(user_action, guided_timeout_flag, abort_reason)``.
    A non-None abort_reason means the caller must abort with terminal_status="aborted".
    """
    cb = config.on_user_review
    decisions_payload = [asdict(r) for r in reflections]
    try:
        action = await asyncio.wait_for(
            cb(round_num, proposal, decisions_payload, tracker.to_dict()),
            timeout=config.guided_timeout_seconds,
        )
        return action, False, None
    except asyncio.TimeoutError:
        if config.guided_timeout_policy == "abort":
            return None, True, "user_review_timeout"
        # opt-in silent approve.
        return UserAction(action="approve"), True, None
    except Exception as exc:
        return None, False, f"user_review_failed: {exc!r}"


def _fallback_objections(consolidated: list[dict]) -> list[dict]:
    """Safety: ensure every consolidated entry has the schema Primary expects."""
    fixed = []
    for entry in consolidated:
        if not isinstance(entry, dict):
            continue
        e = dict(entry)
        e.setdefault("source_issue_keys", [e.get("issue_key", "")])
        e.setdefault("suggestions", [])
        e.setdefault("conflicting_suggestions", False)
        fixed.append(e)
    return fixed


async def refine_protocol(
    query: str,
    nodes: list,
    config: RefineConfig,
    logger: Any = None,
) -> Decision:
    """Core REFINE protocol. See spec §1 for the flow diagram.

    Returns a ``Decision`` with ``protocol_used="refine"`` and
    ``refine_summary`` populated. Uses ``Decision.trace_id`` directly for
    the refine trace subdirectory (R9 #3 — no ``refine_trace_id`` field).
    """
    if not nodes:
        raise ValueError("refine_protocol requires at least one node")

    primary_idx = config.primary_index
    if primary_idx < 0 or primary_idx >= len(nodes):
        raise ValueError(f"primary_index {primary_idx} out of range for {len(nodes)} nodes")

    primary = nodes[primary_idx]
    reviewers = [n for i, n in enumerate(nodes) if i != primary_idx]

    decision = Decision(
        query=query,
        ruling="",
        confidence=0.0,
        minority_report="",
        votes={},
        protocol_used="refine",
    )

    tracker = IssueTracker()
    rounds: list[RefineRound] = []
    total_cost = 0.0
    terminal_status: str = ""
    abort_reason: str | None = None
    last_round_reflections: list[Reflection] = []

    # --- Round 0: initial proposal -----------------------------------------
    initial_prompt = build_primary_initial(query)
    try:
        proposal = await primary.query(initial_prompt)
        total_cost += getattr(primary, "last_cost_usd", 0.0) or 0.0
    except Exception as e:
        # R02 MAJOR-5: count the failed first call's cost before the retry
        # overwrites last_cost_usd; handle_primary_failure now returns cost.
        total_cost += getattr(primary, "last_cost_usd", 0.0) or 0.0
        recovered, degraded, retry_cost = await handle_primary_failure(
            e, nodes, primary_idx, current_proposal="",
            current_round=0, retry_prompt=initial_prompt,
        )
        total_cost += retry_cost
        if degraded or not recovered:
            decision.degraded = True
            decision.failed_nodes = [primary.name]
            decision.refine_summary = {
                "total_rounds": 0,
                "total_objections": 0,
                "resolved": 0,
                "partial_resolved": 0,
                "open": 0,
                "parse_errors": 0,
                "best_round": 0,
                "best_round_score": {"critical": 0, "major": 0, "minor": 0},
                "best_round_note": None,
                "terminal_status": TERMINAL_ABORTED,
                "guided": config.guided,
                "user_overrides_count": 0,
                "sycophancy_warning": False,
                "abort_reason": "primary_failed",
            }
            decision.cost_usd = total_cost
            decision.confidence = 0.1
            decision.votes = {primary.name: ""}
            return decision
        proposal = recovered

    # --- N-round loop -------------------------------------------------------
    for round_num in range(1, config.max_rounds + 1):
        # Budget gate (at round start).
        if config.max_budget_usd is not None and total_cost >= config.max_budget_usd:
            terminal_status = TERMINAL_BUDGET
            break
        # Cancellation.
        if config.cancel_event is not None and config.cancel_event.is_set():
            terminal_status = TERMINAL_CANCELLED
            break

        # --- Reviewers parallel -------------------------------------------
        decisions_summary = format_decisions_summary(
            [asdict(r) for r in last_round_reflections]
        ) if round_num > 1 else ""
        resolved_summary = ", ".join(
            f"{k} ({v.severity})" for k, v in tracker.issues.items()
            if v.resolution == "resolved"
        ) or "(none)"
        unresolved_summary = ", ".join(
            f"{k} ({v.severity})" for k, v in tracker.issues.items()
            if v.resolution in ("open", "partial_resolved")
        ) or "(none)"

        reviewer_tasks = []
        for r_node in reviewers:
            prompt = build_reviewer(
                query=query,
                primary_node=primary.name,
                round_num=round_num,
                proposal_or_diff=proposal,
                decisions_summary=decisions_summary,
                resolved_issues_summary=resolved_summary,
                unresolved_issues_summary=unresolved_summary,
            )
            reviewer_tasks.append(_reviewer_call(r_node, prompt, round_num))
        reviewer_results = await asyncio.gather(*reviewer_tasks, return_exceptions=False)

        round_objections: list[Objection] = []
        parse_errors: list[str] = []
        successful_reviewers: list[str] = []
        round_cost = 0.0
        for r_node, (objs, parse_err, r_cost) in zip(reviewers, reviewer_results):
            round_cost += r_cost
            if parse_err:
                parse_errors.append(r_node.name)
                continue
            successful_reviewers.append(r_node.name)
            if objs:
                round_objections.extend(objs)

        total_cost += round_cost

        # Abort if every reviewer dead on any round — late-round infra failure
        # must not be disguised as normal convergence (R02 MAJOR-3).
        if len(reviewers) > 0 and not successful_reviewers and parse_errors:
            decision.degraded = True
            decision.failed_nodes = list(parse_errors)
            terminal_status = TERMINAL_ABORTED
            abort_reason = "all_reviewers_offline"
            break

        # --- Canonicalize / merge / reconcile -----------------------------
        round_objections = merge_similar_keys(round_objections)
        round_objections = reconcile_cross_round(round_objections, tracker, round_num)

        # Upsert to tracker.
        for obj in round_objections:
            tracker.upsert(
                obj.issue_key, round_num, obj.reviewer,
                severity=obj.severity, category=obj.category,
                description=obj.description,
                target=obj.target,
            )

        # --- Collator -----------------------------------------------------
        consolidated, collator_cost, collator_failed = await collate_objections(
            round_objections, round_num, config, reviewers,
        )
        total_cost += collator_cost
        round_cost += collator_cost
        consolidated = _fallback_objections(consolidated)

        # --- Primary reflection + revised proposal ------------------------
        reflections: list[Reflection] = []
        if round_objections:
            reflect_prompt = build_primary_reflection(
                round_num, consolidated, proposal,
            )
            try:
                raw_reflect = await primary.query(reflect_prompt)
                _reflect_cost = getattr(primary, "last_cost_usd", 0.0) or 0.0
                total_cost += _reflect_cost
                round_cost += _reflect_cost
            except Exception as e:
                # R02 MAJOR-5: record the failed-call cost before retry overwrites it.
                _fail_cost = getattr(primary, "last_cost_usd", 0.0) or 0.0
                total_cost += _fail_cost
                round_cost += _fail_cost
                retry_reflect, degraded, retry_cost = await handle_primary_failure(
                    e, nodes, primary_idx, current_proposal=proposal,
                    current_round=round_num, retry_prompt=reflect_prompt,
                )
                total_cost += retry_cost
                round_cost += retry_cost
                if degraded:
                    decision.degraded = True
                    decision.failed_nodes = [primary.name]
                    terminal_status = TERMINAL_ABORTED
                    abort_reason = "primary_failed"
                    break
                raw_reflect = retry_reflect

            try:
                reflections, new_proposal = _parse_reflection_response(raw_reflect)
                if new_proposal:
                    proposal = new_proposal
            except Exception:
                # Could not parse reflection — treat as degraded abort on this round.
                decision.degraded = True
                terminal_status = TERMINAL_ABORTED
                abort_reason = "primary_reflection_parse_failed"
                # LOW#2: record the in-flight round so total_rounds counts the abort.
                _partial = RefineRound(
                    round_num=round_num,
                    proposal_text=proposal,
                    objections=[asdict(o) for o in round_objections],
                    collated_suggestions=consolidated,
                    reflections=[],
                    user_overrides=None,
                    parse_errors=parse_errors,
                    issue_snapshot={k: v.resolution for k, v in tracker.issues.items()},
                    issue_severity_snapshot={k: v.severity for k, v in tracker.issues.items()},
                    cost_usd=round_cost,
                    accept_rate=0.0,
                    collator_cost_usd=collator_cost,
                    collator_failed=collator_failed,
                    guided_timeout=False,
                )
                rounds.append(_partial)
                if logger is not None:
                    try:
                        logger.log_round(decision.trace_id, asdict(_partial))
                    except Exception:
                        pass
                break

            # Multi-key resolve on the tracker.
            for refl in reflections:
                for key in refl.source_issue_keys or []:
                    try:
                        tracker.resolve(
                            key, refl.verdict,
                            severity_after=refl.severity_after,
                            current_round=round_num,
                        )
                    except ValueError:
                        # partial without severity_after — treat as reject
                        tracker.resolve(key, "reject", current_round=round_num)

        # Compute accept_rate for sycophancy.
        if reflections:
            accepted = sum(1 for r in reflections if r.verdict == "accept")
            accept_rate = accepted / len(reflections)
        else:
            accept_rate = 0.0

        # --- GUIDED hook --------------------------------------------------
        user_overrides: list[UserOverride] | None = None
        guided_timeout = False
        if config.guided:
            action, guided_timeout, abort_reason_g = await _run_guided_callback(
                config, round_num, proposal, reflections, tracker,
            )
            if abort_reason_g is not None:
                decision.degraded = True
                terminal_status = TERMINAL_ABORTED
                abort_reason = abort_reason_g
                # Record the in-flight round before break.
                _abort_round = RefineRound(
                    round_num=round_num,
                    proposal_text=proposal,
                    objections=[asdict(o) for o in round_objections],
                    collated_suggestions=consolidated,
                    reflections=[asdict(r) for r in reflections],
                    user_overrides=None,
                    parse_errors=parse_errors,
                    issue_snapshot={k: v.resolution for k, v in tracker.issues.items()},
                    issue_severity_snapshot={k: v.severity for k, v in tracker.issues.items()},
                    cost_usd=round_cost,
                    accept_rate=accept_rate,
                    collator_cost_usd=collator_cost,
                    collator_failed=collator_failed,
                    guided_timeout=guided_timeout,
                )
                rounds.append(_abort_round)
                # LOW#1: persist the abort round to refine/{trace_id}.jsonl for audit completeness.
                if logger is not None:
                    try:
                        logger.log_round(decision.trace_id, asdict(_abort_round))
                    except Exception:
                        pass
                break
            if action is not None:
                if action.action == "terminate":
                    terminal_status = TERMINAL_CANCELLED
                elif action.action == "override" and action.overrides:
                    user_overrides = list(action.overrides)
                    for ov in action.overrides:
                        try:
                            tracker.resolve(
                                ov.issue_key, ov.verdict,
                                severity_after=ov.severity_after,
                                current_round=round_num,
                            )
                        except ValueError:
                            tracker.resolve(ov.issue_key, "reject", current_round=round_num)

        # --- Record round ------------------------------------------------
        round_record = RefineRound(
            round_num=round_num,
            proposal_text=proposal,
            objections=[asdict(o) for o in round_objections],
            collated_suggestions=consolidated,
            reflections=[asdict(r) for r in reflections],
            user_overrides=user_overrides,
            parse_errors=parse_errors,
            issue_snapshot={k: v.resolution for k, v in tracker.issues.items()},
            issue_severity_snapshot={k: v.severity for k, v in tracker.issues.items()},
            cost_usd=round_cost,
            accept_rate=accept_rate,
            collator_cost_usd=collator_cost,
            collator_failed=collator_failed,
            guided_timeout=guided_timeout,
        )
        rounds.append(round_record)
        if logger is not None:
            try:
                logger.log_round(decision.trace_id, asdict(round_record))
            except Exception:
                pass

        last_round_reflections = reflections

        # User terminated — stop before convergence check.
        if terminal_status == TERMINAL_CANCELLED:
            break

        # --- Convergence check -------------------------------------------
        converged, status = check_convergence(
            tracker=tracker,
            threshold=config.convergence_threshold,
            current_round=round_num,
            max_rounds=config.max_rounds,
            round_objections=round_objections,
            round_parse_errors=parse_errors,
            successful_reviewer_names=successful_reviewers,
        )
        if converged:
            terminal_status = status
            break

    # Loop exited without early break — max_rounds reached.
    if not terminal_status:
        terminal_status = TERMINAL_MAX_ROUNDS

    # --- Best-round recovery on abort/budget/cancel ------------------------
    bst = track_best_round(rounds, tracker)
    if terminal_status in (TERMINAL_ABORTED, TERMINAL_BUDGET) and bst["best_round"] > 0:
        for r in rounds:
            if r.round_num == bst["best_round"] and r.proposal_text:
                proposal = r.proposal_text
                break

    # --- Final decision assembly ------------------------------------------
    max_rounds_hit = terminal_status == TERMINAL_MAX_ROUNDS
    decision.ruling = proposal

    # R03 MAJOR-B: aggregate parse_errors into degraded BEFORE confidence calc,
    # otherwise degraded=True + confidence=1.0 becomes possible.
    parse_err_count = sum(len(r.parse_errors) for r in rounds)
    if parse_err_count > 0:
        decision.degraded = True
        existing = set(decision.failed_nodes or [])
        for r in rounds:
            for name in r.parse_errors:
                if name not in existing:
                    existing.add(name)
                    decision.failed_nodes = list(decision.failed_nodes or []) + [name]

    decision.confidence = compute_refine_confidence(tracker, max_rounds_hit, decision.degraded)

    last_objection_objs: list[Objection] = []
    last_parse_errors: list[str] = []
    if rounds:
        last_parse_errors = list(rounds[-1].parse_errors)
        for d in rounds[-1].objections:
            last_objection_objs.append(Objection(
                id=d.get("id", ""),
                candidate_key=d.get("candidate_key", ""),
                issue_key=d.get("issue_key", ""),
                reviewer=d.get("reviewer", ""),
                category=d.get("category", "improvement"),
                severity=d.get("severity", "minor"),
                target=d.get("target", ""),
                description=d.get("description", ""),
                suggestion=d.get("suggestion"),
            ))

    decision.votes = compute_refine_votes(
        primary_node_name=primary.name,
        reviewer_nodes=reviewers,
        last_round_objections=last_objection_objs,
        last_round_parse_errors=last_parse_errors,
        ruling=proposal,
    )
    decision.minority_report = compute_refine_minority_report(
        last_objection_objs, reviewers,
    )
    decision.cost_usd = total_cost

    total_objs = sum(len(r.objections) for r in rounds)
    resolved = sum(1 for s in tracker.issues.values() if s.resolution == "resolved")
    partial_resolved = sum(1 for s in tracker.issues.values() if s.resolution == "partial_resolved")
    open_count = sum(1 for s in tracker.issues.values() if s.resolution == "open")
    active = tracker.active_issues()
    open_crit = sum(1 for s in active if s.severity == "critical")
    open_maj = sum(1 for s in active if s.severity == "major")
    open_min = sum(1 for s in active if s.severity == "minor")
    total_overrides = sum(len(r.user_overrides or []) for r in rounds)

    decision.refine_summary = {
        "total_rounds": len(rounds),
        "total_objections": total_objs,
        "resolved": resolved,
        "partial_resolved": partial_resolved,
        "open": open_count,
        "open_critical": open_crit,
        "open_major": open_maj,
        "open_minor": open_min,
        "parse_errors": parse_err_count,
        "best_round": bst["best_round"],
        "best_round_score": bst["best_round_score"],
        "best_round_note": bst["best_round_note"],
        "terminal_status": terminal_status,
        "guided": config.guided,
        "user_overrides_count": total_overrides,
        "sycophancy_warning": check_sycophancy(rounds),
        "abort_reason": abort_reason,
    }

    return decision
