"""ChangeState + 11-state constants per lifecycle-event-model §3.7.

State is a derived value (reducer output, Task 1.6); this module just defines
the dataclass + constants. Per Axiom 7 (events immutable; state derived), this
ChangeState is the **per-change** record inside state.yaml's `changes` map.
"""
from dataclasses import dataclass, field
from typing import Any

from super_harness.core.events import Framework

# 11 states per lifecycle-event-model §3.7. Order matches the spec's listing.
STATES: tuple[str, ...] = (
    "INTENT_DECLARED",
    "AWAITING_PLAN_REVIEW",
    "PLAN_REJECTED",
    "PLAN_APPROVED",
    "IMPLEMENTATION_IN_PROGRESS",
    "AWAITING_CODE_REVIEW",
    "CODE_REVIEW_REJECTED",
    "READY_TO_MERGE",
    "MERGED",
    "ARCHIVED",
    "ABANDONED",
)

TERMINAL_STATES: frozenset[str] = frozenset({"ARCHIVED", "ABANDONED"})


@dataclass
class ChangeState:
    """Per-change state record (entry in state.yaml's `changes` map).

    Per lifecycle §2 state.yaml schema. Not frozen — reducer mutates this
    record while replaying events; state.yaml writer (Task 1.7) serializes
    via asdict(). Construct empty via `ChangeState(change_id="...")`.

    Fields populated by reducer (Task 1.6):
    - current_state: one of STATES (default INTENT_DECLARED for newly seen change)
    - framework: which framework emitted (openspec/spec-kit/superpowers/plain)
    - last_event_*: identity of most recent event affecting this change
    - event_counts: per-type counter (KNOWN_EVENT_TYPES only per §3.8.5 invariant 5)
    - description: latest intent_declared description
    - tier: tier_hint from latest plan_ready
    - scope: scope from latest plan_ready (files + components)
    - affected_anchors: from latest plan_ready
    - pr_url: from implementation_complete or merged
    - merge_commit_sha: from merged
    - redeclaration_history: append-only audit of intent/plan_redeclared events
    """
    change_id: str
    current_state: str = "INTENT_DECLARED"
    framework: Framework = "plain"
    last_event_id: str = ""
    last_event_type: str = ""
    last_event_at: str = ""
    event_counts: dict[str, int] = field(default_factory=dict)
    description: str = ""
    tier: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    affected_anchors: list[str] = field(default_factory=list)
    pr_url: str | None = None
    merge_commit_sha: str | None = None
    redeclaration_history: list[dict[str, Any]] = field(default_factory=list)
