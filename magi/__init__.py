import sys

# Python 3.10 + pydantic + litellm + fastapi: pydantic's type evaluation
# during litellm's lazy imports exceeds the default 1000-frame limit.
if sys.getrecursionlimit() < 3000:
    sys.setrecursionlimit(3000)

from magi.core.engine import MAGI

__version__ = "0.1.0"
__all__ = ["MAGI"]
