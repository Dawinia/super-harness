"""ULID-based event_id generator.

Per lifecycle-event-model §2 (events.jsonl format): each event has a unique
`event_id` field formed as `ev_<ULID>`. The `ev_` prefix marks the type
(distinguishes events from change_ids / adapter ids) and the ULID provides
monotonic ordering + cross-process uniqueness.

Caveat (§3.9 #2): multi-process ULID generation is NOT guaranteed strictly
monotonic across processes — the reducer relies on events.jsonl **append
order** (file offset) for causal sequencing, not ULID order. ULIDs serve only
as identifiers, not as a sortable key.
"""
import ulid


def new_event_id() -> str:
    """Generate a fresh event_id like `ev_01H8KX2GH0000000000000000`."""
    return f"ev_{ulid.ULID()}"
