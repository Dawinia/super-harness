"""Gate base architecture for super-harness.

Per sensor-gate-architecture §2.2: Gates are gatekeepers — given a ProposedAction,
the current ChangeState, and the event log, they return GateResult(allow|block).
Gates do NOT emit events (pure query); the dispatcher logs decisions out-of-band.

Public surface:
- Gate (ABC) — subclass to write a new gate
- GateDecision — Enum (ALLOW / BLOCK), the only two values
- GateResult — what decide() returns (decision / reason / blocked / suggested / related_events)
- ProposedAction — the action a Gate is asked to allow or block
- GateFiresOn / ProposedActionKind — Literal aliases

See sensor-gate-architecture spec §2.2 for the full contract.

API stability: **experimental** (v0.1). The Gate interface may change in v0.2
without backwards compatibility. Pin to v0.1 if depending on this API.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Literal

from super_harness.core.events import Event
from super_harness.core.state import ChangeState

__all__ = [
    "Gate",
    "GateDecision",
    "GateFiresOn",
    "GateResult",
    "ProposedAction",
    "ProposedActionKind",
]

GateFiresOn = Literal["pre_tool_use", "pre_commit", "pre_push", "pr_open", "pr_merge"]
ProposedActionKind = Literal["edit", "write", "commit", "push", "pr_open", "pr_merge"]


class GateDecision(Enum):
    """Gate verdict: ALLOW or BLOCK. The only two values, intentionally.

    See sensor-gate-architecture spec §2.2.
    """

    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class ProposedAction:
    """Action the agent or CI wants to take, passed to gates for allow/block decision.

    See sensor-gate-architecture spec §2.2.
    """

    kind: ProposedActionKind
    file: str | None = None
    pr_number: int | None = None
    commit_sha: str | None = None


@dataclass(frozen=True)
class GateResult:
    """Outcome of a `Gate.decide()` call. Immutable.

    See sensor-gate-architecture spec §2.2.
    """

    decision: GateDecision
    reason: str = ""
    blocked_action: str | None = None
    suggested_action: str | None = None
    related_events: list[str] = field(default_factory=list)


class Gate(ABC):
    """Gatekeeper that decides allow/block on a proposed action.

    Pure query — gates do NOT emit events; the dispatcher logs decisions out-of-band.
    See sensor-gate-architecture spec §2.2 for the full contract.
    Subclasses must define `name` (non-empty) and `version` (not the default "0.0.0").
    """

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not cls.name:
            raise TypeError(
                f"{cls.__name__} must define a non-empty `name` class attribute"
            )
        if not cls.version or cls.version == "0.0.0":
            raise TypeError(
                f"{cls.__name__} must define `version` (not the default '0.0.0')"
            )

    @abstractmethod
    def decide(
        self,
        action: ProposedAction,
        state: ChangeState | None,
        events: list[Event],
    ) -> GateResult: ...
