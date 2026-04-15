from dataclasses import dataclass, field, asdict
import json, time, uuid


@dataclass
class Decision:
    query: str
    ruling: str
    confidence: float  # 0-1, agreement_score for vote, last-round for critique, 0.5 for escalate
    minority_report: str  # dissenting opinion (losing vote answer in vote mode)
    votes: dict[str, str]  # node_name -> raw answer
    mind_changes: list[str] = field(default_factory=list)  # nodes that changed position (empty for vote)
    protocol_used: str = "vote"
    degraded: bool = False
    failed_nodes: list[str] = field(default_factory=list)
    latency_ms: int = 0
    cost_usd: float = 0.0
    cost_mode: str = "measured"  # measured | estimated | unavailable
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    refine_summary: dict | None = None  # REFINE protocol summary (None for vote/critique/adaptive)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
