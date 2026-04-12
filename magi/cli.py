"""MAGI CLI — Disagreement OS for LLMs."""
import asyncio
import os
import sys

import click

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from magi.core.engine import MAGI
from magi.core.node import MELCHIOR, BALTHASAR, CASPER, AuthenticationError
from magi.core.cli_errors import MagiCliError
from magi.commands.diff import (
    get_git_diff,
    check_diff_size,
    build_review_prompt,
    format_review_output,
)

# Default models from env
_DEFAULT_M = os.environ.get("MAGI_MELCHIOR", "claude-sonnet-4-6")
_DEFAULT_B = os.environ.get("MAGI_BALTHASAR", "gpt-4o")
_DEFAULT_C = os.environ.get("MAGI_CASPER", "gemini/gemini-2.5-pro")


@click.group()
@click.version_option(package_name="magi-system")
def main():
    """MAGI — Disagreement OS for LLMs. Three models, one decision."""
    pass


def _build_engine(source: str, preset: str, melchior: str | None, balthasar: str | None, casper: str | None) -> MAGI:
    """Build MAGI engine based on source mode."""
    from magi.presets import get_preset
    try:
        personas = get_preset(preset)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if source == "cli-multi":
        return MAGI.cli_multi(personas=personas)
    elif source == "cli-single":
        return MAGI.cli_single(personas=personas)
    else:
        m = melchior or _DEFAULT_M
        b = balthasar or _DEFAULT_B
        c = casper or _DEFAULT_C
        return MAGI(melchior=m, balthasar=b, casper=c, personas=personas)


def _format_cost(decision) -> str:
    """Format cost display based on cost_mode."""
    if decision.cost_mode == "unavailable":
        return " | Cost: N/A"
    elif decision.cost_mode == "estimated":
        return f" | Cost: ~${decision.cost_usd:.6f} (est.)" if decision.cost_usd else " | Cost: ~$0 (est.)"
    else:
        return f" | Cost: ${decision.cost_usd:.6f}" if decision.cost_usd else ""


@main.command()
@click.argument("query")
@click.option("--mode", default="vote", type=click.Choice(["vote", "critique", "adaptive", "escalate"]))
@click.option("--preset", default="eva", help="Persona preset (eva, code-review, research, writing, strategy, ice-debug, architecture)")
@click.option("--source", default="api", type=click.Choice(["api", "cli-multi", "cli-single"]), help="Node backend: api (default), cli-multi (claude+codex+gemini), cli-single (claude only)")
@click.option("--melchior", default=None, help="Model for Melchior node (api mode)")
@click.option("--balthasar", default=None, help="Model for Balthasar node (api mode)")
@click.option("--casper", default=None, help="Model for Casper node (api mode)")
def ask(query: str, mode: str, preset: str, source: str, melchior: str, balthasar: str, casper: str):
    """Ask MAGI a question. Three models deliberate, one decision emerges."""
    engine = _build_engine(source, preset, melchior, balthasar, casper)
    try:
        decision = asyncio.run(engine.ask(query, mode=mode))
    except AuthenticationError as e:
        click.echo(f"Authentication error: {e}", err=True)
        sys.exit(1)
    except MagiCliError as e:
        click.echo(f"CLI error: {e}", err=True)
        sys.exit(1)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"\n{'=' * 60}")
    click.echo("MAGI DECISION")
    click.echo(f"{'=' * 60}")
    click.echo(f"\nRuling:\n{decision.ruling}")
    click.echo(f"\nConfidence: {decision.confidence:.0%}")
    click.echo(f"Protocol: {decision.protocol_used}")

    if decision.minority_report:
        click.echo(f"\nMinority Report:\n{decision.minority_report}")

    if decision.degraded:
        click.echo(f"\n⚠ Degraded: failed nodes = {', '.join(decision.failed_nodes)}")

    cost_str = _format_cost(decision)
    click.echo(f"\nTrace: {decision.trace_id} | Latency: {decision.latency_ms}ms{cost_str}")


@main.command()
@click.argument("file", required=False)
@click.option("--staged", is_flag=True, help="Review staged changes (git add first)")
@click.option("--source", default="api", type=click.Choice(["api", "cli-multi", "cli-single"]), help="Node backend")
@click.option("--melchior", default=None, help="Model for Melchior node (api mode)")
@click.option("--balthasar", default=None, help="Model for Balthasar node (api mode)")
@click.option("--casper", default=None, help="Model for Casper node (api mode)")
def diff(file: str | None, staged: bool, source: str, melchior: str, balthasar: str, casper: str):
    """Multi-model code review. Three models review your diff independently."""
    if not file and not staged:
        staged = True  # default to --staged

    try:
        diff_text = get_git_diff(staged=staged, file_path=file)
        check_diff_size(diff_text)
    except (RuntimeError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    engine = _build_engine(source, "code-review", melchior, balthasar, casper)
    prompt = build_review_prompt(diff_text)

    try:
        decision = asyncio.run(engine.ask(prompt, mode="vote"))
    except AuthenticationError as e:
        click.echo(f"Authentication error: {e}", err=True)
        sys.exit(1)
    except MagiCliError as e:
        click.echo(f"CLI error: {e}", err=True)
        sys.exit(1)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(format_review_output(decision))


@main.command()
@click.option("--question", "-q", required=True, help="The question that was asked")
@click.option("--answer", "-a", required=True, help="The answer to evaluate")
@click.option("--source", default="api", type=click.Choice(["api", "cli-multi", "cli-single"]), help="Node backend")
@click.option("--melchior", default=None, help="Model for Melchior node (api mode)")
@click.option("--balthasar", default=None, help="Model for Balthasar node (api mode)")
@click.option("--casper", default=None, help="Model for Casper node (api mode)")
def judge(question: str, answer: str, source: str, melchior: str, balthasar: str, casper: str):
    """Multi-model answer scoring. Three models rate a Q&A pair."""
    from magi.commands.judge import build_judge_prompt, format_judge_output

    engine = _build_engine(source, "eva", melchior, balthasar, casper)
    prompt = build_judge_prompt(question, answer)

    try:
        decision = asyncio.run(engine.ask(prompt, mode="vote"))
    except AuthenticationError as e:
        click.echo(f"Authentication error: {e}", err=True)
        sys.exit(1)
    except MagiCliError as e:
        click.echo(f"CLI error: {e}", err=True)
        sys.exit(1)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(format_judge_output(decision))


@main.command()
def presets():
    """List available persona presets."""
    from magi.presets import PRESETS
    for name, personas in sorted(PRESETS.items()):
        names = " / ".join(p.name for p in personas)
        click.echo(f"  {name:15s} {names}")


@main.command()
@click.option("--dataset", default="builtin", help="Dataset to benchmark against")
@click.option("--mode", default="vote", type=click.Choice(["vote", "critique", "adaptive"]))
@click.option("--concurrency", default=3, help="Max concurrent questions")
@click.option("--source", default="api", type=click.Choice(["api", "cli-multi", "cli-single"]), help="Node backend")
@click.option("--melchior", default=None, help="Model for Melchior node (api mode)")
@click.option("--balthasar", default=None, help="Model for Balthasar node (api mode)")
@click.option("--casper", default=None, help="Model for Casper node (api mode)")
def bench(dataset: str, mode: str, concurrency: int, source: str, melchior: str, balthasar: str, casper: str):
    """Run benchmark: MAGI vs individual models on multiple-choice questions."""
    from magi.bench.datasets import get_dataset
    from magi.bench.runner import run_benchmark
    from magi.bench.report import format_report

    try:
        questions = get_dataset(dataset)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    engine = _build_engine(source, "eva", melchior, balthasar, casper)
    node_desc = " / ".join(n.model for n in engine.nodes)
    click.echo(f"Running benchmark: {len(questions)} questions, mode={mode}, concurrency={concurrency}")
    click.echo(f"Source: {source} | Nodes: {node_desc}")
    click.echo("")

    try:
        report = asyncio.run(run_benchmark(engine, questions, mode=mode, concurrency=concurrency))
    except Exception as e:
        click.echo(f"Benchmark failed: {e}", err=True)
        sys.exit(1)

    click.echo(format_report(report))


@main.command()
@click.option("--trace-dir", default=None, help="Trace directory (default: ~/.magi/traces)")
def analytics(trace_dir: str | None):
    """Analyze decision history from traces."""
    import os
    from magi.commands.analytics import analyze_stream, format_analytics

    trace_dir = trace_dir or os.path.expanduser("~/.magi/traces")
    report = analyze_stream(trace_dir)
    click.echo(format_analytics(report))


@main.command()
@click.argument("trace_id")
@click.option("--trace-dir", default=None, help="Trace directory (default: ~/.magi/traces)")
def replay(trace_id: str, trace_dir: str | None):
    """Replay a specific decision by trace ID."""
    import os
    from magi.commands.analytics import load_traces, format_replay

    trace_dir = trace_dir or os.path.expanduser("~/.magi/traces")
    traces = load_traces(trace_dir)

    match = [t for t in traces if t.get("trace_id", "").startswith(trace_id)]
    if not match:
        click.echo(f"No decision found with trace ID starting with '{trace_id}'", err=True)
        sys.exit(1)
    if len(match) > 1:
        click.echo(f"Multiple matches for '{trace_id}'. Be more specific.", err=True)
        for t in match:
            click.echo(f"  {t['trace_id']} — {t.get('query', 'N/A')[:60]}", err=True)
        sys.exit(1)

    click.echo(format_replay(match[0]))


@main.command()
def check():
    """Check CLI tool availability for cli-multi/cli-single modes."""
    avail = MAGI.check_cli_availability()
    click.echo("MAGI CLI Backend Check")
    click.echo("=" * 40)
    for cli, ok in avail.items():
        status = "OK" if ok else "NOT FOUND"
        click.echo(f"  {cli:10s} {status}")
    click.echo("")
    all_ok = all(avail.values())
    claude_ok = avail.get("claude", False)
    if all_ok:
        click.echo("cli-multi:  ready (claude + codex + gemini)")
    elif claude_ok:
        click.echo("cli-multi:  partial (missing: " + ", ".join(k for k, v in avail.items() if not v) + ")")
    else:
        click.echo("cli-multi:  not available")
    click.echo(f"cli-single: {'ready' if claude_ok else 'not available'} (claude only)")


@main.command()
@click.option("--host", default="0.0.0.0", help="Server host")
@click.option("--port", default=3000, help="Server port")
@click.option("--source", default="api", type=click.Choice(["api", "cli-multi", "cli-single"]), help="Node backend")
def dashboard(host: str, port: int, source: str):
    """Launch NERV Command Center — real-time MAGI visualization."""
    try:
        from magi.web.server import start_server
    except ImportError:
        click.echo("Web dependencies not installed. Run: pip install magi-system[web]", err=True)
        sys.exit(1)
    click.echo(f"NERV Command Center starting at http://localhost:{port} (source: {source})")
    start_server(host=host, port=port, source=source)


if __name__ == "__main__":
    main()
