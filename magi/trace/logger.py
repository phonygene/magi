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
