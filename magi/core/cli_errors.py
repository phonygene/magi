"""Unified error types for CLI-native mode."""
import re

# Scrub known secret patterns from stderr before storing on exceptions
_SECRET_RE = re.compile(r'(sk-|AIza|ya29\.|Bearer\s)[A-Za-z0-9_\-]{8,}', re.IGNORECASE)


def _scrub_stderr(text: str) -> str:
    """Remove API key fragments and bearer tokens from stderr text."""
    return _SECRET_RE.sub(r'\1[REDACTED]', text)


class MagiCliError(Exception):
    """Base class for CLI-native mode errors."""


class MagiProviderNotFoundError(MagiCliError):
    """CLI tool not installed or not on PATH."""

    def __init__(self, cli_name: str):
        self.cli_name = cli_name
        super().__init__(
            f"CLI tool '{cli_name}' not found. "
            f"Install it and ensure it is on your PATH."
        )


class MagiCliExecutionError(MagiCliError):
    """CLI process exited with non-zero return code."""

    def __init__(self, cli_name: str, returncode: int, stderr: str = ""):
        self.cli_name = cli_name
        self.returncode = returncode
        self.stderr = _scrub_stderr(stderr[:500])
        detail = f": {self.stderr[:300]}" if self.stderr else ""
        super().__init__(
            f"CLI '{cli_name}' exited with code {returncode}{detail}"
        )


class MagiCliAuthError(MagiCliError):
    """CLI authentication or subscription error."""

    # Per-provider stderr patterns that indicate auth issues
    AUTH_PATTERNS = {
        "claude": ["not authenticated", "api key", "unauthorized", "login required"],
        "codex": ["not authenticated", "api key", "unauthorized", "login"],
        "gemini": ["not authenticated", "api key", "unauthorized", "login"],
    }

    def __init__(self, cli_name: str, stderr: str = ""):
        self.cli_name = cli_name
        self.stderr = _scrub_stderr(stderr[:500])
        super().__init__(
            f"CLI '{cli_name}' authentication failed. "
            f"Check your login status or subscription."
        )

    @classmethod
    def check_stderr(cls, cli_name: str, stderr: str) -> bool:
        """Return True if stderr indicates an auth error."""
        lower = stderr.lower()
        patterns = cls.AUTH_PATTERNS.get(cli_name, [])
        return any(p in lower for p in patterns)


class MagiNodeTimeoutError(MagiCliError):
    """CLI node timed out."""

    def __init__(self, node_name: str, timeout: float):
        self.node_name = node_name
        self.timeout = timeout
        super().__init__(f"Node '{node_name}' timed out after {timeout}s")
