"""Gate ABC + decision/result/action dataclasses for super-harness.

Per sensor-gate-architecture §2.2: Gates are gatekeepers — given a ProposedAction,
the current ChangeState, and the event log, they return GateResult(allow|block).
Gates do NOT emit events (pure query); the dispatcher logs decisions out-of-band.

Public surface:
- Gate — ABC; subclasses set class attributes and implement decide()
- GateDecision — Enum (ALLOW / BLOCK)
- GateResult — what decide() returns (decision / reason / blocked / suggested / related_events)
- ProposedAction — the action a Gate is asked to allow or block
- GateFiresOn / ProposedActionKind — Literal aliases
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Literal

from super_harness.core.events import Event
from super_harness.core.state import ChangeState

GateFiresOn = Literal["pre_tool_use", "pre_commit", "pre_push", "pr_open", "pr_merge"]
ProposedActionKind = Literal["edit", "write", "commit", "push", "pr_open", "pr_merge"]


class GateDecision(Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass
class ProposedAction:
    kind: ProposedActionKind
    file: str | None = None
    pr_number: int | None = None
    commit_sha: str | None = None


@dataclass
class GateResult:
    decision: GateDecision
    reason: str = ""
    blocked_action: str | None = None
    suggested_action: str | None = None
    related_events: list[str] = field(default_factory=list)


class Gate(ABC):
    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    @abstractmethod
    def decide(
        self,
        action: ProposedAction,
        state: ChangeState | None,
        events: list[Event],
    ) -> GateResult: ...
