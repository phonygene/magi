from magi.core.node import MagiNode, Persona, MELCHIOR, BALTHASAR, CASPER
from magi.core.decision import Decision
from magi.protocols.vote import vote
from magi.protocols.critique import critique
from magi.protocols.adaptive import adaptive
from magi.trace.logger import TraceLogger
import os
import shutil


class MAGI:
    """
    MAGI Disagreement OS — three LLMs, one decision.

    Optimized with NeurIPS 2025 findings:
    - ICE error-detection framing in critique rounds
    - Parallel execution via asyncio.gather
    - Synthesis phase for final ruling
    - Cost tracking per decision

    Usage:
        engine = MAGI()  # uses defaults from env or config
        decision = await engine.ask("What is quantum entanglement?")
        print(decision.ruling)
        print(decision.minority_report)
    """

    def __init__(
        self,
        melchior: str = "claude-sonnet-4-6",
        balthasar: str = "gpt-4o",
        casper: str = "gemini/gemini-2.5-pro",
        personas: tuple[Persona, Persona, Persona] | None = None,
        timeout: float = 30.0,
        trace_dir: str | None = None,
    ):
        p = personas or (MELCHIOR, BALTHASAR, CASPER)
        self.nodes = [
            MagiNode("melchior", melchior, p[0], timeout),
            MagiNode("balthasar", balthasar, p[1], timeout),
            MagiNode("casper", casper, p[2], timeout),
        ]
        self._init_common(self.nodes, trace_dir)
        self._cost_mode = "measured"  # default for API-based nodes

    def _init_common(self, nodes: list, trace_dir: str | None = None):
        """Shared initialization for all construction paths."""
        self.nodes = nodes
        self.trace_dir = trace_dir or os.path.expanduser("~/.magi/traces")
        self._logger = TraceLogger(self.trace_dir)

    @classmethod
    def cli_multi(
        cls,
        personas: tuple[Persona, Persona, Persona] | None = None,
        timeout: float = 600.0,
        trace_dir: str | None = None,
    ) -> "MAGI":
        """Create MAGI with CLI-native nodes: claude + codex + gemini.

        Zero provider API keys — uses CLI subscriptions/OAuth.
        """
        from magi.core.cli_adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
        from magi.core.cli_node import CliNode

        p = personas or (MELCHIOR, BALTHASAR, CASPER)
        engine = cls.__new__(cls)
        engine._init_common([
            CliNode("melchior", p[0], ClaudeAdapter(model_tier="opus", effort="high"), timeout),
            CliNode("balthasar", p[1], CodexAdapter(effort="high"), timeout),
            CliNode("casper", p[2], GeminiAdapter(model="gemini-3-flash-preview", effort="medium"), timeout),
        ], trace_dir)
        engine._cost_mode = "mixed"
        return engine

    @classmethod
    def cli_single(
        cls,
        personas: tuple[Persona, Persona, Persona] | None = None,
        timeout: float = 600.0,
        trace_dir: str | None = None,
    ) -> "MAGI":
        """Create MAGI with Claude-only CLI nodes at different tiers.

        Only requires Claude subscription.
        """
        from magi.core.cli_adapters import ClaudeAdapter
        from magi.core.cli_node import CliNode

        p = personas or (MELCHIOR, BALTHASAR, CASPER)
        engine = cls.__new__(cls)
        engine._init_common([
            CliNode("melchior", p[0], ClaudeAdapter(model_tier="opus", effort="high"), timeout),
            CliNode("balthasar", p[1], ClaudeAdapter(model_tier="sonnet", effort="medium"), timeout),
            CliNode("casper", p[2], ClaudeAdapter(model_tier="haiku", effort="low"), timeout),
        ], trace_dir)
        engine._cost_mode = "measured"
        return engine

    @staticmethod
    def check_cli_availability() -> dict[str, bool]:
        """Check which CLI tools are available on PATH."""
        return {
            "claude": shutil.which("claude") is not None,
            "codex": shutil.which("codex") is not None,
            "gemini": shutil.which("gemini") is not None,
        }

    def _resolve_cost_mode(self) -> str:
        """Resolve the cost_mode for the current engine configuration.

        H1: extracted from inline logic at engine.py:134-144. Pure refactor.
        """
        cost_mode = getattr(self, "_cost_mode", "measured")
        if cost_mode == "mixed":
            modes = set(getattr(n, "cost_mode", "measured") for n in self.nodes)
            if modes == {"measured"}:
                return "measured"
            if "unavailable" in modes:
                return "estimated"
            return "estimated"
        return cost_mode

    async def ask(self, query: str, mode: str = "vote") -> Decision:
        """
        Ask MAGI a question. Returns a Decision with ruling, confidence,
        minority report, and full trace.

        Modes: "vote" (default), "critique", "adaptive", "escalate", "refine"
        """
        if mode == "refine":
            # H3: dispatch to refine() with default RefineConfig.
            return await self.refine(query)

        if mode == "vote":
            decision = await vote(query, self.nodes)
            # 3-way split with no majority → auto-escalate to critique
            if decision.protocol_used == "vote_no_majority":
                decision = await critique(query, self.nodes)
        elif mode == "critique":
            decision = await critique(query, self.nodes)
        elif mode == "escalate":
            decision = await critique(query, self.nodes, max_rounds=2)
        elif mode == "adaptive":
            decision = await adaptive(query, self.nodes)
        else:
            raise NotImplementedError(f"Mode '{mode}' not yet implemented.")

        # Aggregate cost from all nodes
        decision.cost_usd = sum(n.last_cost_usd for n in self.nodes)

        # Determine cost_mode from nodes
        decision.cost_mode = self._resolve_cost_mode()

        self._logger.log(decision)
        return decision

    async def refine(
        self,
        query: str,
        config=None,
    ) -> Decision:
        """H2: REFINE protocol entry point.

        Cost aggregation is done per-call inside ``refine_protocol`` (not
        via ``sum(n.last_cost_usd)``), avoiding the per-call overwrite bug.
        """
        from magi.protocols.refine import refine_protocol
        from magi.protocols.refine_types import RefineConfig

        cfg = config or RefineConfig()
        decision = await refine_protocol(query, self.nodes, cfg, logger=self._logger)
        decision.cost_mode = self._resolve_cost_mode()
        self._logger.log(decision)
        return decision
