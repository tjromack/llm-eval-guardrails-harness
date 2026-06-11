"""The thin adapter interface every target implements.

The harness reaches a target ONLY through an Adapter (Decision 002). The contract
is deliberately small: given a case input, return the target's text output, any
trace (retrieved context / citations / raw payload), and the target version that
gets stamped onto the run. Adapters never raise into the runner — transport and
target failures come back as a `TargetResult` with `error` set, so one bad case
never sinks a whole suite.

Targets are looked up by the `target` name on the suite via `get_adapter`, so the
runner stays target-agnostic — it never imports a concrete adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TargetResult:
    """What an adapter returns for one case. `error` is None on success."""

    output: str
    trace: dict[str, Any] = field(default_factory=dict)
    target_version: str = ""
    latency_ms: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Adapter(ABC):
    """One interface for all targets. Subclasses are registered via `@register`."""

    target_name: str = "abstract"

    @property
    @abstractmethod
    def target_version(self) -> str:
        """Version stamped on every run (the prompt/model revision under test)."""

    @abstractmethod
    def run(self, case_input: Any) -> TargetResult:
        """Call the target for one case input (str or dict) and capture its output."""


# ---- Registry: targets are resolved by name, never hard-coded in the runner ----

_REGISTRY: dict[str, type[Adapter]] = {}


def register(name: str):
    """Class decorator: make an adapter discoverable as `name`."""

    def deco(cls: type[Adapter]) -> type[Adapter]:
        cls.target_name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def get_adapter(target: str, **kwargs: Any) -> Adapter:
    """Construct the adapter registered for `target`. Raises on unknown target."""
    if target not in _REGISTRY:
        raise KeyError(
            f"no adapter registered for target {target!r}; "
            f"known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[target](**kwargs)


def available_targets() -> list[str]:
    return sorted(_REGISTRY)
