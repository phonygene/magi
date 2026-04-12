"""MAGI NERV Command Center — WebSocket server for real-time decision visualization.

Optimized:
- Parallel critique rounds (asyncio.gather)
- Cost tracking per node and total
- Model configuration via API
- ICE error-detection framing
"""
import asyncio
import json
import re
import time
import os
import traceback
from pathlib import Path

import atexit
import signal

_DEBUG_LOG = Path(__file__).parent.parent.parent / "_server_debug.log"

def _dbg(msg: str):
    """Debug log to file + stdout."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def _on_exit():
    _dbg("[EXIT] Process exiting via atexit")

atexit.register(_on_exit)

def _sig_handler(signum, frame):
    _dbg(f"[SIGNAL] Received signal {signum} ({signal.Signals(signum).name})")
    # Write stack trace of where signal was received
    if frame:
        import linecache
        _dbg(f"[SIGNAL] At: {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}")

for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGBREAK):
    try:
        signal.signal(_sig, _sig_handler)
    except (OSError, ValueError):
        pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from magi.core.engine import MAGI
from magi.core.node import MagiNode, Persona
from magi.core.decision import Decision
from magi.presets import get_preset, list_presets, PRESETS
from magi.protocols.critique import estimate_agreement, _build_critique_prompt, _extract_revised_answer, _build_synthesis_prompt
from magi.protocols.judge import get_judge_model, get_judge_options, llm_estimate_agreement

app = FastAPI(title="MAGI NERV Command Center")

# Server-wide source mode, set by start_server()
_source_mode = "api"

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/defaults")
async def api_defaults():
    judge_info = {
        "judge": get_judge_model(),
        "judge_options": get_judge_options(),
    }
    if _source_mode == "cli-multi":
        return {
            "melchior": "claude sonnet",
            "balthasar": "codex (default model)",
            "casper": "gemini gemini-3-flash-preview",
            "source": "cli-multi",
            "model_options": {
                "melchior": ["opus", "sonnet", "haiku"],
                "balthasar": [],
                "casper": ["gemini-2.5-flash-preview", "gemini-3-flash-preview", "gemini-2.5-pro-preview"],
            },
            **judge_info,
        }
    elif _source_mode == "cli-single":
        return {
            "melchior": "claude opus",
            "balthasar": "claude sonnet",
            "casper": "claude haiku",
            "source": "cli-single",
            "model_options": {
                "melchior": ["opus", "sonnet", "haiku"],
                "balthasar": ["opus", "sonnet", "haiku"],
                "casper": ["opus", "sonnet", "haiku"],
            },
            **judge_info,
        }
    return {
        "melchior": os.environ.get("MAGI_MELCHIOR", "openrouter/deepseek/deepseek-v3.2"),
        "balthasar": os.environ.get("MAGI_BALTHASAR", "openrouter/xiaomi/mimo-v2-pro"),
        "casper": os.environ.get("MAGI_CASPER", "openrouter/minimax/minimax-m2.7"),
        "source": "api",
        "model_options": {},
        **judge_info,
    }


@app.get("/api/presets")
async def api_presets():
    return {
        name: [{"name": p.name, "description": p.description} for p in personas]
        for name, personas in PRESETS.items()
    }


@app.websocket("/ws/ask")
async def ws_ask(ws: WebSocket):
    """WebSocket endpoint for real-time MAGI decisions.

    Client sends:
    {"query": "...", "mode": "adaptive", "preset": "eva",
     "melchior": "model", "balthasar": "model", "casper": "model"}

    Server streams events:
    {"event": "start", ...}
    {"event": "node_start", "node": "melchior"}
    {"event": "node_done", "node": "melchior", "answer": "...", "latency_ms": ..., "cost_usd": ...}
    {"event": "node_error", "node": "melchior", "error": "..."}
    {"event": "agreement", "score": 0.85, "route": "vote"}
    {"event": "critique_start", "round": 1}
    {"event": "critique_done", "round": 1, "node": "melchior", "answer": "..."}
    {"event": "synthesis_start"}
    {"event": "decision", "ruling": "...", "confidence": ..., "cost_usd": ..., ...}
    """
    await ws.accept()

    try:
        data = await ws.receive_json()
    except Exception:
        await ws.close(code=1003)
        return

    query = data.get("query", "")
    mode = data.get("mode", "adaptive")
    preset_name = data.get("preset", "eva")

    melchior_model = data.get("melchior", os.environ.get("MAGI_MELCHIOR", "openrouter/deepseek/deepseek-v3.2"))
    balthasar_model = data.get("balthasar", os.environ.get("MAGI_BALTHASAR", "openrouter/xiaomi/mimo-v2-pro"))
    casper_model = data.get("casper", os.environ.get("MAGI_CASPER", "openrouter/minimax/minimax-m2.7"))

    try:
        personas = get_preset(preset_name)
    except KeyError:
        personas = get_preset("eva")

    # Model tier overrides from client (cli modes only)
    melchior_tier = data.get("melchior_tier", "")
    balthasar_tier = data.get("balthasar_tier", "")
    casper_tier = data.get("casper_tier", "")
    judge_model = data.get("judge_model", "")

    if _source_mode == "cli-multi":
        from magi.core.cli_adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
        from magi.core.cli_node import CliNode
        nodes = [
            CliNode("melchior", personas[0], ClaudeAdapter(model_tier=melchior_tier or "sonnet"), timeout=600),
            CliNode("balthasar", personas[1], CodexAdapter(), timeout=600),
            CliNode("casper", personas[2], GeminiAdapter(model=casper_tier or "gemini-3-flash-preview"), timeout=600),
        ]
    elif _source_mode == "cli-single":
        from magi.core.cli_adapters import ClaudeAdapter
        from magi.core.cli_node import CliNode
        nodes = [
            CliNode("melchior", personas[0], ClaudeAdapter(model_tier=melchior_tier or "opus"), timeout=600),
            CliNode("balthasar", personas[1], ClaudeAdapter(model_tier=balthasar_tier or "sonnet"), timeout=600),
            CliNode("casper", personas[2], ClaudeAdapter(model_tier=casper_tier or "haiku"), timeout=600),
        ]
    else:
        nodes = [
            MagiNode("melchior", melchior_model, personas[0], timeout=60),
            MagiNode("balthasar", balthasar_model, personas[1], timeout=60),
            MagiNode("casper", casper_model, personas[2], timeout=60),
        ]

    # Send start event
    await ws.send_json({
        "event": "start",
        "query": query,
        "mode": mode,
        "preset": preset_name,
        "nodes": [
            {"name": n.name, "model": n.model, "persona": n.persona.name}
            for n in nodes
        ],
    })

    total_cost = 0.0
    _dbg("WS handler started")

    try:
        # Phase 1: Parallel query all nodes
        results = {}
        failed = []
        start_time = time.monotonic()
        _dbg("[PHASE 1] Starting parallel node queries")

        async def query_node(node):
            nonlocal total_cost
            await ws.send_json({"event": "node_start", "node": node.name})
            node_start = time.monotonic()
            try:
                _dbg(f"[PHASE 1] {node.name}: querying...")
                answer = await node.query(query)
                latency = int((time.monotonic() - node_start) * 1000)
                results[node.name] = answer
                total_cost += node.last_cost_usd
                _dbg(f"[PHASE 1] {node.name}: done ({latency}ms)")
                await ws.send_json({
                    "event": "node_done",
                    "node": node.name,
                    "answer": answer[:2000],
                    "latency_ms": latency,
                    "cost_usd": round(node.last_cost_usd, 6),
                })
                _dbg(f"[PHASE 1] {node.name}: ws event sent")
            except Exception as e:
                _dbg(f"[PHASE 1] {node.name}: FAILED: {e}")
                failed.append(node.name)
                await ws.send_json({
                    "event": "node_error",
                    "node": node.name,
                    "error": str(e)[:200],
                })

        await asyncio.gather(*[query_node(n) for n in nodes])
        _dbg(f"[PHASE 1] All nodes complete. results={len(results)} failed={len(failed)}")

        if not results:
            await ws.send_json({"event": "error", "message": "All nodes failed"})
            return

        # Phase 2: Agreement scoring via LLM Judge
        _dbg("[PHASE 2] Starting agreement scoring...")
        answers = list(results.values())
        judge_used = judge_model or get_judge_model()
        judge_short = judge_used.split("/")[-1].replace(":free", "")
        await ws.send_json({"event": "judge_start", "model": judge_short})
        judge_failed = False
        try:
            score, dissent = await llm_estimate_agreement(
                query, answers, model_override=judge_model or None,
            )
            agreement = score
            await ws.send_json({
                "event": "judge_done",
                "model": judge_short,
                "score": round(agreement, 3),
                "dissent": dissent,
            })
        except Exception as judge_err:
            _dbg(f"[PHASE 2] Judge failed: {judge_err}")
            agreement = 0.5
            judge_failed = True
            await ws.send_json({
                "event": "judge_error",
                "model": judge_short,
                "error": str(judge_err)[:200],
            })
        _dbg(f"[PHASE 2] Agreement score: {agreement:.3f}")
        route = "vote" if agreement > 0.8 else ("critique" if agreement > 0.4 else "escalate")
        if mode != "adaptive":
            route = mode

        await ws.send_json({
            "event": "agreement",
            "score": round(agreement, 3),
            "route": route,
        })
        _dbg(f"[PHASE 2] Route: {route}")

        # Phase 3: Protocol execution
        round_num = 0
        if route == "vote" or len(results) < 2:
            ruling = answers[0]
            confidence = agreement
            minority_parts = [
                f"[{name}]: {ans[:500]}"
                for name, ans in results.items()
                if ans != ruling
            ]
            protocol = "vote"
        else:
            # ICE Critique rounds with error-detection framing + parallel gather
            active_nodes = [n for n in nodes if n.name not in failed]
            current_answers = dict(results)
            max_rounds = 3 if route == "critique" else 2

            for round_num in range(max_rounds):
                if agreement > 0.8:
                    break

                await ws.send_json({
                    "event": "critique_start",
                    "round": round_num + 1,
                })

                # Build all critique tasks
                critique_tasks = {}
                for node in active_nodes:
                    if node.name not in current_answers:
                        continue
                    others = {k: v for k, v in current_answers.items() if k != node.name}
                    prompt = _build_critique_prompt(query, current_answers[node.name], others)
                    critique_tasks[node.name] = asyncio.create_task(node.query(prompt))

                # Parallel gather for all critique responses
                names = list(critique_tasks.keys())
                gather_results = await asyncio.gather(
                    *critique_tasks.values(), return_exceptions=True,
                )

                for name, result in zip(names, gather_results):
                    node_obj = next((n for n in active_nodes if n.name == name), None)
                    if isinstance(result, Exception):
                        await ws.send_json({
                            "event": "critique_error",
                            "round": round_num + 1,
                            "node": name,
                            "error": str(result)[:200],
                        })
                    else:
                        revised = _extract_revised_answer(result)
                        current_answers[name] = revised
                        cost = node_obj.last_cost_usd if node_obj else 0.0
                        total_cost += cost
                        await ws.send_json({
                            "event": "critique_done",
                            "round": round_num + 1,
                            "node": name,
                            "answer": revised[:2000],
                            "cost_usd": round(cost, 6),
                        })

                try:
                    score, _ = await llm_estimate_agreement(
                        query, list(current_answers.values()),
                        model_override=judge_model or None,
                    )
                    agreement = score
                except Exception:
                    agreement = 0.5
                await ws.send_json({
                    "event": "critique_agreement",
                    "round": round_num + 1,
                    "score": round(agreement, 3),
                })

            # Synthesis phase
            await ws.send_json({"event": "synthesis_start"})
            synth_prompt = _build_synthesis_prompt(query, current_answers, round_num + 1)
            try:
                ruling = await active_nodes[0].query(synth_prompt)
                total_cost += active_nodes[0].last_cost_usd
                protocol = f"{route}_ice_synth_r{round_num + 1}"
            except Exception:
                ruling = list(current_answers.values())[0]
                protocol = f"{route}_r{round_num + 1}"

            confidence = agreement
            minority_parts = [
                f"[{name}]: {ans[:500]}"
                for name, ans in current_answers.items()
                if ans != ruling
            ]

        total_ms = int((time.monotonic() - start_time) * 1000)

        # Determine cost_mode from nodes
        cost_modes = set(getattr(n, "cost_mode", "measured") for n in nodes)
        if cost_modes == {"measured"}:
            cost_mode = "measured"
        elif "unavailable" in cost_modes:
            cost_mode = "estimated"
        else:
            cost_mode = "measured"

        # Send final decision
        await ws.send_json({
            "event": "decision",
            "ruling": ruling[:3000],
            "confidence": round(confidence, 3),
            "minority_report": "\n\n".join(minority_parts)[:3000] if minority_parts else "",
            "protocol_used": protocol,
            "degraded": len(failed) > 0,
            "failed_nodes": failed,
            "latency_ms": total_ms,
            "cost_usd": round(total_cost, 6),
            "cost_mode": cost_mode,
            "votes": {k: v[:1000] for k, v in results.items()},
        })

        # Retry loop: keep connection open for retry requests (node or judge)
        can_retry = failed or judge_failed
        while can_retry:
            try:
                retry_data = await asyncio.wait_for(ws.receive_json(), timeout=300)
            except (asyncio.TimeoutError, WebSocketDisconnect):
                break

            if retry_data.get("action") != "retry":
                break

            retry_target = retry_data.get("node", "")

            # --- Judge retry ---
            if retry_target == "judge":
                new_judge = retry_data.get("judge_model", "") or judge_model
                judge_model = new_judge  # update for future rounds
                judge_short = (new_judge or get_judge_model()).split("/")[-1].replace(":free", "")
                _dbg(f"[RETRY] Retrying judge with {judge_short}...")
                await ws.send_json({"event": "judge_start", "model": judge_short})
                try:
                    score, dissent = await llm_estimate_agreement(
                        query, list(results.values()),
                        model_override=new_judge or None,
                    )
                    agreement = score
                    judge_failed = False
                    await ws.send_json({
                        "event": "judge_done",
                        "model": judge_short,
                        "score": round(agreement, 3),
                        "dissent": dissent,
                    })
                    _dbg(f"[RETRY] Judge succeeded: {agreement:.3f}")
                except Exception as e:
                    _dbg(f"[RETRY] Judge FAILED again: {e}")
                    await ws.send_json({
                        "event": "judge_error",
                        "model": judge_short,
                        "error": str(e)[:200],
                    })
                can_retry = failed or judge_failed
                continue

            # --- Node retry ---
            retry_node = next((n for n in nodes if n.name == retry_target), None)
            if not retry_node or retry_target not in failed:
                continue

            # Recreate node if model changed
            new_model = retry_data.get("model", "")
            new_tier = retry_data.get("model_tier", "")
            if new_tier and _source_mode in ("cli-multi", "cli-single"):
                _dbg(f"[RETRY] Recreating {retry_target} with tier={new_tier}")
                idx = next(i for i, n in enumerate(nodes) if n.name == retry_target)
                persona = retry_node.persona
                if _source_mode == "cli-multi":
                    from magi.core.cli_adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
                    from magi.core.cli_node import CliNode
                    if retry_target == "melchior":
                        retry_node = CliNode(retry_target, persona, ClaudeAdapter(model_tier=new_tier), timeout=600)
                    elif retry_target == "balthasar":
                        retry_node = CliNode(retry_target, persona, CodexAdapter(), timeout=600)
                    elif retry_target == "casper":
                        retry_node = CliNode(retry_target, persona, GeminiAdapter(model=new_tier), timeout=600)
                elif _source_mode == "cli-single":
                    from magi.core.cli_adapters import ClaudeAdapter
                    from magi.core.cli_node import CliNode
                    retry_node = CliNode(retry_target, persona, ClaudeAdapter(model_tier=new_tier), timeout=600)
                nodes[idx] = retry_node
            elif new_model and _source_mode == "api":
                _dbg(f"[RETRY] Recreating {retry_target} with model={new_model}")
                idx = next(i for i, n in enumerate(nodes) if n.name == retry_target)
                retry_node = MagiNode(retry_target, new_model, retry_node.persona, timeout=60)
                nodes[idx] = retry_node

            _dbg(f"[RETRY] Retrying {retry_target}...")
            await ws.send_json({"event": "node_start", "node": retry_target})
            node_start = time.monotonic()
            try:
                answer = await retry_node.query(query)
                latency = int((time.monotonic() - node_start) * 1000)
                results[retry_target] = answer
                failed.remove(retry_target)
                total_cost += retry_node.last_cost_usd
                await ws.send_json({
                    "event": "node_done",
                    "node": retry_target,
                    "answer": answer[:2000],
                    "latency_ms": latency,
                    "cost_usd": round(retry_node.last_cost_usd, 6),
                })
                _dbg(f"[RETRY] {retry_target}: done ({latency}ms)")

                # Re-score agreement with updated results
                answers = list(results.values())
                try:
                    score, _ = await llm_estimate_agreement(
                        query, answers, model_override=judge_model or None,
                    )
                    agreement = score
                except Exception:
                    agreement = 0.5
                await ws.send_json({
                    "event": "retry_agreement",
                    "score": round(agreement, 3),
                    "failed_nodes": failed,
                })
            except Exception as e:
                _dbg(f"[RETRY] {retry_target}: FAILED again: {e}")
                await ws.send_json({
                    "event": "node_error",
                    "node": retry_target,
                    "error": str(e)[:200],
                })
            can_retry = failed or judge_failed

    except WebSocketDisconnect:
        _dbg("[WS] Client disconnected")
    except asyncio.CancelledError:
        _dbg("[WS] Handler CANCELLED (asyncio.CancelledError)")
    except Exception as e:
        _dbg(f"[WS] Unhandled Exception: {e}\n{traceback.format_exc()}")
        try:
            await ws.send_json({"event": "error", "message": str(e)[:500]})
        except Exception:
            pass
    except BaseException as e:
        _dbg(f"[WS] BaseException: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        raise


def start_server(host: str = "0.0.0.0", port: int = 3000, source: str = "api"):
    """Start the NERV Command Center."""
    global _source_mode
    _source_mode = source
    import uvicorn
    uvicorn.run(app, host=host, port=port)
