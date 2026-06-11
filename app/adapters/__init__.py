"""Target adapters. The harness reaches every target only through this package.

Importing the package registers the built-in adapters so `get_adapter(name)`
can resolve them by the suite's `target` field.
"""

from app.adapters.base import (
    Adapter,
    TargetResult,
    available_targets,
    get_adapter,
    register,
)

# Importing concrete adapters populates the registry (side effect on import).
from app.adapters import rag_copilot  # noqa: E402,F401

__all__ = [
    "Adapter",
    "TargetResult",
    "available_targets",
    "get_adapter",
    "register",
]
