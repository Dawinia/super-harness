import json
from dataclasses import FrozenInstanceError

import pytest

from super_harness.core.events import (
    CORE_EVENT_TYPES,
    EXTENSION_EVENT_TYPES,
    KNOWN_EVENT_TYPES,
    Actor,
    Event,
    EventSchemaError,
    parse_event_line,
    serialize_event,
)


def test_core_event_types():
    assert CORE_EVENT_TYPES == frozenset({
        "intent_declared",
        "plan_ready",
        "implementation_started",
        "implementation_complete",
        "merged",
    })


def test_extension_event_types_disjoint_from_core():
    assert CORE_EVENT_TYPES.isdisjoint(EXTENSION_EVENT_TYPES)


def test_known_is_union():
    assert KNOWN_EVENT_TYPES == CORE_EVENT_TYPES | EXTENSION_EVENT_TYPES


def test_extension_includes_required_events():
    # Per spec §3.6 + §3.7, these extension events MUST exist
    required = {
        "plan_approved", "plan_rejected",
        "verification_passed", "verification_failed",
        "code_review_passed", "code_review_failed",
        "scope_drift_detected",
        "intent_redeclared", "intent_abandoned",
        "plan_redeclared", "implementation_restarted", "implementation_invalidated",
        "implementation_withdrawn", "merged_reverted", "pr_opened",
        "sensor_timeout_exceeded", "sensor_crashed",
    }
    missing = required - EXTENSION_EVENT_TYPES
    assert not missing, f"missing extension event types: {missing}"


def test_serialize_then_parse_round_trip():
    ev = Event(
        event_id="ev_01H8KX2GH0000000000000000",
        type="intent_declared",
        change_id="2026-05-27-add-foo",
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="openspec-adapter"),
        framework="openspec",
        payload={"description": "Add foo"},
    )
    line = serialize_event(ev)
    assert "\n" not in line  # one event per line invariant
    parsed = parse_event_line(line)
    assert parsed.event_id == ev.event_id
    assert parsed.actor.type == "adapter"
    assert parsed.payload == {"description": "Add foo"}


def test_parse_rejects_missing_required():
    bad = json.dumps({"type": "intent_declared", "change_id": "foo"})
    with pytest.raises(EventSchemaError) as exc:
        parse_event_line(bad)
    msg = str(exc.value).lower()
    assert "event_id" in msg or "missing" in msg


def test_parse_rejects_unknown_actor_type():
    payload = {
        "event_id": "ev_x",
        "type": "intent_declared",
        "change_id": "foo",
        "timestamp": "2026-05-27T10:00:00Z",
        "actor": {"type": "robot", "identifier": "x"},
        "framework": "plain",
    }
    with pytest.raises(EventSchemaError):
        parse_event_line(json.dumps(payload))


def test_parse_rejects_unknown_framework():
    payload = {
        "event_id": "ev_x",
        "type": "intent_declared",
        "change_id": "foo",
        "timestamp": "2026-05-27T10:00:00Z",
        "actor": {"type": "human", "identifier": "alice"},
        "framework": "elixir-spec-thing",
    }
    with pytest.raises(EventSchemaError):
        parse_event_line(json.dumps(payload))


def test_parse_rejects_invalid_json():
    with pytest.raises(EventSchemaError):
        parse_event_line("{not valid json")


def test_parse_rejects_non_object():
    with pytest.raises(EventSchemaError):
        parse_event_line('["array", "not", "object"]')


def test_event_is_frozen():
    ev = Event(
        event_id="ev_x", type="intent_declared", change_id="c1",
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="human", identifier="alice"),
        framework="plain",
    )
    with pytest.raises(FrozenInstanceError):
        ev.type = "other"  # type: ignore[misc]


def test_parse_handles_null_payload():
    """JSON line with explicit `"payload": null` should parse to empty dict."""
    payload_json = {
        "event_id": "ev_null",
        "type": "intent_declared",
        "change_id": "c1",
        "timestamp": "2026-05-27T10:00:00Z",
        "actor": {"type": "human", "identifier": "alice"},
        "framework": "plain",
        "payload": None,
    }
    ev = parse_event_line(json.dumps(payload_json))
    assert ev.payload == {}


def test_serialize_compact_no_spaces():
    """events.jsonl format §2 requires compact one-line JSON."""
    ev = Event(
        event_id="ev_x", type="intent_declared", change_id="c1",
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="human", identifier="alice"),
        framework="plain",
    )
    line = serialize_event(ev)
    assert ": " not in line  # no JSON space after colon
    assert ", " not in line  # no JSON space after comma


def test_parse_rejects_empty_event_id():
    line = json.dumps({
        "event_id": "",
        "type": "intent_declared",
        "change_id": "c1",
        "timestamp": "2026-05-27T10:00:00Z",
        "actor": {"type": "adapter", "identifier": "test"},
        "framework": "plain",
    })
    with pytest.raises(EventSchemaError, match="event_id"):
        parse_event_line(line)


def test_parse_rejects_empty_change_id():
    line = json.dumps({
        "event_id": "ev_1",
        "type": "intent_declared",
        "change_id": "",
        "timestamp": "2026-05-27T10:00:00Z",
        "actor": {"type": "adapter", "identifier": "test"},
        "framework": "plain",
    })
    with pytest.raises(EventSchemaError, match="change_id"):
        parse_event_line(line)
