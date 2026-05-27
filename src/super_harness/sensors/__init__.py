"""Sensor ABC + result/activity/context dataclasses for super-harness.

Per sensor-gate-architecture §2.1: Sensors observe lifecycle events / activities and
emit new events. A Sensor subclass declares which events / activities trigger it and
implements an idempotent `check()` that returns a SensorResult.

Public surface:
- Sensor — ABC; subclasses set class attributes and implement check()
- SensorResult — what check() returns (status / summary / details / emit_events / suggested_action)
- Activity / ActivityType — non-event triggers (commit, push, file_change, cli_done, cli_verify)
- WorkspaceContext — workspace info passed into check()
- Determinism / SensorStatus — Literal aliases
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from super_harness.core.events import Event

ActivityType = Literal["commit", "push", "file_change", "cli_done", "cli_verify"]
Determinism = Literal["computational", "inferential"]
SensorStatus = Literal["pass", "fail", "warning", "informational"]


@dataclass
class Activity:
    type: ActivityType
    change_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkspaceContext:
    workspace_root: Path
    git_branch: str | None = None
    active_change_id: str | None = None


@dataclass
class SensorResult:
    status: SensorStatus
    summary: str
    details: dict[str, Any] | None = None
    emit_events: list[Event] = field(default_factory=list)
    suggested_action: str | None = None


class Sensor(ABC):
    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    triggers_on_events: ClassVar[list[str]] = []
    triggers_on_activities: ClassVar[list[ActivityType]] = []
    determinism: ClassVar[Determinism] = "computational"

    @abstractmethod
    def check(self, trigger: Event | Activity, context: WorkspaceContext) -> SensorResult: ...

    def reviewer_strategy(self) -> Literal["subagent", "human", "hybrid"] | None:
        return None
