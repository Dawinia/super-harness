"""Emit-time validation for EventWriter (lifecycle-event-model §3.8.1 layered validation).

Two-layer policy:
- Emit-time (this module): STRICT — reject illegal transitions BEFORE writing to
  events.jsonl. Raises `EmitPreconditionError`.
- Reducer-time (Task 1.6): TOLERANT — warn + skip illegal events at replay
  (events already on disk are immutable per Axiom 7).

This module also encodes hard prerequisites beyond the per-state transition
table (e.g. `implementation_complete` must follow a `verification_passed` on
the same change_id per spec §3.4). The transition table alone can't express
"event X requires event Y to have happened before" — only "state S accepts
event X" — so we layer `_HARD_PREREQ_EVENTS` on top.

Cost note: validate_preconditions reads events.jsonl on every emit (O(N) per
emit where N = total event count). v0.1 accepts this. v0.2 may add in-memory
state cache + per-change_id seen-events bitmask for O(1) emit if it shows up
in profiling.
"""
from __future__ import annotations

from pathlib import Path

from super_harness.core.events import Event, parse_event_line
from super_harness.core.transitions import INVALID, compute_target_state


class EmitPreconditionError(ValueError):
    """Raised by EventWriter when the new event would create an illegal transition."""


# Events with hard prerequisites beyond the transition table.
# Each entry: event_type -> list of event types that must have been emitted
# previously on the same change_id.
_HARD_PREREQ_EVENTS: dict[str, list[str]] = {
    # implementation_complete must follow verification_passed (lifecycle §3.4)
    "implementation_complete": ["verification_passed"],
}


def _current_state(events_file: Path, change_id: str) -> str | None:
    """Compute the current state for a single change by replaying its events.

    Cheap because each `emit` only needs the latest state; we don't build full
    state.yaml here. Mirrors reducer-time tolerant semantics (§3.8.1): illegal
    events on disk are skipped, not raised — emit-time strictness applies to
    the NEW event we're about to write, not to history.
    """
    if not events_file.exists():
        return None
    current: str | None = None
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except Exception:
            # Tolerant per §3.8.1 reducer-time: skip malformed lines on disk.
            continue
        if ev.change_id != change_id:
            continue
        target = compute_target_state(current, ev.type)
        if target == INVALID:
            continue  # tolerant per §3.8.1 reducer-time
        current = target
    return current


def _change_event_types(events_file: Path, change_id: str) -> set[str]:
    """Return the set of event types previously emitted for this change_id."""
    if not events_file.exists():
        return set()
    seen: set[str] = set()
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except Exception:
            continue
        if ev.change_id == change_id:
            seen.add(ev.type)
    return seen


def validate_preconditions(events_file: Path, new_event: Event) -> None:
    """Raise EmitPreconditionError if the new event violates strict emit-time rules.

    Two checks:
    1. Transition legality: (current_state, event_type) must be a legal
       transition per the table in `transitions.py`.
    2. Hard prerequisites: certain events require specific prior events on the
       same change_id (e.g. implementation_complete needs verification_passed).
    """
    current = _current_state(events_file, new_event.change_id)
    target = compute_target_state(current, new_event.type)
    if target == INVALID:
        if current is None:
            # No prior state for this change_id — only intent_declared can start
            # a change. Include that hint in the message so callers (and the
            # tests in test_emit_validation.py) get an actionable error.
            raise EmitPreconditionError(
                f"event {new_event.type!r} illegal as first event "
                f"(change_id={new_event.change_id}); "
                f"a change must start with 'intent_declared' "
                f"to reach INTENT_DECLARED state"
            )
        raise EmitPreconditionError(
            f"event {new_event.type!r} illegal from state {current!r} "
            f"(change_id={new_event.change_id})"
        )
    # additional hard prerequisites
    required = _HARD_PREREQ_EVENTS.get(new_event.type, [])
    if required:
        seen = _change_event_types(events_file, new_event.change_id)
        missing = [r for r in required if r not in seen]
        if missing:
            raise EmitPreconditionError(
                f"event {new_event.type!r} requires prior {missing} "
                f"(change_id={new_event.change_id})"
            )
