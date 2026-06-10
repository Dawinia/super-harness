"""Event dataclasses + JSON serialization for super-harness.

Per lifecycle-event-model §2 (events.jsonl format) + §3 (5 core + 18 extension
event types). Events are the single source of truth; state.yaml is derived.

Public surface:
- CORE_EVENT_TYPES / EXTENSION_EVENT_TYPES / KNOWN_EVENT_TYPES — frozensets
- Actor + Event dataclasses (frozen — events are immutable per Axiom 7)
- parse_event_line / serialize_event — JSON ↔ Event with schema validation
- EventSchemaError — raised by parse_event_line on validation failure
"""
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Per lifecycle-event-model §3.1-3.5: 5 core lifecycle events
CORE_EVENT_TYPES: frozenset[str] = frozenset({
    "intent_declared",
    "plan_ready",
    "implementation_started",
    "implementation_complete",
    "merged",
})

# Per lifecycle-event-model §3.6: extension events (sensor-emitted /
# user-initiated / system-detected / sensor lifecycle)
EXTENSION_EVENT_TYPES: frozenset[str] = frozenset({
    # sensor-emitted (state-changing)
    "plan_approved", "plan_rejected",
    "verification_passed", "verification_failed",
    "code_review_passed", "code_review_failed",
    "scope_drift_detected",
    # user-initiated
    "intent_redeclared", "intent_abandoned",
    "plan_redeclared", "implementation_restarted",
    "implementation_invalidated",
    # system-detected
    "implementation_withdrawn", "merged_reverted", "pr_opened",
    # sensor lifecycle (added by SensorDispatcher on timeout / crash)
    "sensor_timeout_exceeded", "sensor_crashed",
})

KNOWN_EVENT_TYPES: frozenset[str] = CORE_EVENT_TYPES | EXTENSION_EVENT_TYPES

ActorType = Literal["human", "agent", "adapter", "sensor", "ci"]
Framework = Literal["openspec", "spec-kit", "superpowers", "plain"]

_VALID_ACTOR_TYPES: frozenset[str] = frozenset({"human", "agent", "adapter", "sensor", "ci"})
_VALID_FRAMEWORKS: frozenset[str] = frozenset({"openspec", "spec-kit", "superpowers", "plain"})
_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "event_id", "type", "change_id", "timestamp", "actor", "framework",
})


class EventSchemaError(ValueError):
    """Raised when an events.jsonl line fails schema validation.

    Emit-time (CLI / adapter writes) MUST treat this strictly (reject); reducer-time
    replays MUST treat this tolerantly (warn + skip) per lifecycle §3.8.1 layered
    validation.
    """


@dataclass(frozen=True)
class Actor:
    """Who/what emitted an event. Frozen — events are immutable (Axiom 7)."""
    type: ActorType
    identifier: str


# @decision:d-events-append-only
@dataclass(frozen=True)
class Event:
    """A single event in events.jsonl. Frozen — events are immutable.

    Fields:
        event_id: ULID-prefixed unique id (see ulid.new_event_id)
        type: a known event type (CORE or EXTENSION). parse_event_line does NOT
            validate this against KNOWN_EVENT_TYPES — unknown types are accepted
            at parse time per lifecycle §3.8.1 layered-validation (reducer skips them).
        change_id: slug identifying the change this event belongs to
        timestamp: ISO 8601 UTC (e.g., "2026-05-27T10:00:00Z")
        actor: who emitted (human/agent/adapter/sensor/ci + identifier)
        framework: which spec-framework context (openspec/spec-kit/superpowers/plain)
        framework_state: optional opaque adapter-specific state
        payload: type-specific fields (per §3.1-3.6)
    """
    event_id: str
    type: str
    change_id: str
    timestamp: str
    actor: Actor
    framework: Framework
    framework_state: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def parse_event_line(line: str) -> Event:
    """Parse one JSON line from events.jsonl into an Event.

    This validator checks SHAPE (required fields present + actor.type / framework in
    enum) but NOT SEMANTICS (payload schema per event type / timestamp ISO format /
    event.type ∈ KNOWN_EVENT_TYPES). Semantic checks belong in:
    - emit_validation (Task 1.5) for emit-time strict rejection
    - reducer (Task 1.6) for reducer-time tolerant warn + skip

    Strict at emit-time; reducer-time callers should catch EventSchemaError and
    `log.warning + skip` per layered-validation rules (§3.8.1).

    Raises:
        EventSchemaError: on JSON parse failure, missing required fields,
            invalid actor.type, or invalid framework.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise EventSchemaError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise EventSchemaError(f"event must be a JSON object, got {type(obj).__name__}")
    missing = _REQUIRED_FIELDS - obj.keys()
    if missing:
        raise EventSchemaError(f"missing required fields: {sorted(missing)}")
    if not obj["event_id"]:
        raise EventSchemaError("event_id must be non-empty")
    if not obj["change_id"]:
        raise EventSchemaError("change_id must be non-empty")
    actor_raw = obj["actor"]
    if not isinstance(actor_raw, dict):
        raise EventSchemaError("actor must be an object with 'type' and 'identifier'")
    if actor_raw.get("type") not in _VALID_ACTOR_TYPES:
        raise EventSchemaError(
            f"actor.type must be one of {sorted(_VALID_ACTOR_TYPES)}, "
            f"got {actor_raw.get('type')!r}"
        )
    if "identifier" not in actor_raw:
        raise EventSchemaError("actor.identifier is required")
    if obj["framework"] not in _VALID_FRAMEWORKS:
        raise EventSchemaError(
            f"framework must be one of {sorted(_VALID_FRAMEWORKS)}, "
            f"got {obj['framework']!r}"
        )
    return Event(
        event_id=obj["event_id"],
        type=obj["type"],
        change_id=obj["change_id"],
        timestamp=obj["timestamp"],
        actor=Actor(type=actor_raw["type"], identifier=actor_raw["identifier"]),
        framework=obj["framework"],
        framework_state=obj.get("framework_state"),
        payload=obj.get("payload") or {},
    )


def serialize_event(event: Event) -> str:
    """Render an Event as one compact JSON line for events.jsonl.

    Per §2 format requirement: single line, no trailing newline (caller appends).
    Compact separators (no space after `:` or `,`) for space efficiency.
    """
    return json.dumps(asdict(event), separators=(",", ":"), sort_keys=False)
