import os
import json
from datetime import datetime, timezone


class TraceLogger:
    """Logs Decision objects to JSONL files for replay and analytics."""

    def __init__(self, trace_dir: str):
        self.trace_dir = trace_dir

    def log(self, decision) -> None:
        """Write a decision to the JSONL trace file. Never crashes the caller."""
        try:
            os.makedirs(self.trace_dir, exist_ok=True)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = os.path.join(self.trace_dir, f"{today}.jsonl")
            line = decision.to_jsonl()
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # Trace write failure is non-fatal. Warn but don't crash.
            import sys
            print(f"[magi] warning: failed to write trace to {self.trace_dir}", file=sys.stderr)

    def log_round(self, trace_id: str, round_data: dict) -> None:
        """Append a per-round REFINE trace record to ``refine/{trace_id}.jsonl``.

        E1: used by ``refine_protocol`` to persist each round's full detail
        (proposal_text, decisions, user_overrides, ...). Never crashes the caller.
        """
        try:
            refine_dir = os.path.join(self.trace_dir, "refine")
            os.makedirs(refine_dir, exist_ok=True)
            path = os.path.join(refine_dir, f"{trace_id}.jsonl")
            line = json.dumps(round_data, ensure_ascii=False, default=str)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            import sys
            print(
                f"[magi] warning: failed to write refine round trace to {self.trace_dir}",
                file=sys.stderr,
            )
