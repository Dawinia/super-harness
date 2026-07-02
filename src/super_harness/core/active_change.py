"""Resolve the *active* change_id from a workspace's state.yaml.

The active change is the **most recently active** non-terminal change (by
`last_event_at`; was previously the first/oldest, a v0.1 placeholder that let a
stale merged-but-not-archived change hijack the gate — HG-STALE-MERGED-CHANGE).
state.yaml is reducer-generated and carries NO top-level `active_change_id` field
— "active" is a derived notion computed at read time. `pick_active_change` is the
single definition, shared with `super-harness status` so the two never drift.

Returns None if state.yaml is missing/unparseable or has no non-terminal change.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path


def _parse_ts(value: object) -> datetime:
    """Parse a ``last_event_at`` value for ORDERING, into an aware-UTC datetime.
    NEVER raises — this feeds the gate hot path.

    Robust to the shapes a state.yaml value can take: PyYAML loads an UNQUOTED ISO
    timestamp as a ``datetime`` (a quoted one stays a ``str``), so accept either.
    - ``datetime`` → normalized to aware UTC (naive gets UTC attached).
    - ISO ``str`` with ``Z`` or ``+00:00`` → parsed (parse, don't string-compare —
      mixed forms misfire lexically; a tz-less string parses to a NAIVE datetime,
      normalized to aware UTC so it can't ``TypeError`` against the aware entries).
    - empty / malformed / ``None`` / any other type → ``datetime.min`` (UTC), which
      sorts LOWEST so it never wins unless everything is unparseable.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def pick_active_change(candidates: Iterable[tuple[str, str, object]]) -> str | None:
    """THE definition of "which change is active": given ``(change_id,
    current_state, last_event_at)`` triples, return the non-terminal change with
    the latest (parsed) ``last_event_at``, ties broken by ``change_id``. None if
    there is no non-terminal change. PURE — no I/O, no git. The ``last_event_at``
    slot is typed ``object`` because a state.yaml value may be a ``str`` OR a
    ``datetime`` (PyYAML) — ``_parse_ts`` handles both and never raises."""
    from super_harness.core.state import TERMINAL_STATES

    live = [(cid, at) for cid, st, at in candidates if st not in TERMINAL_STATES]
    if not live:
        return None
    return max(live, key=lambda t: (_parse_ts(t[1]), t[0]))[0]


def read_active_change_id(root: Path) -> str | None:
    """Resolve the active change_id from state.yaml's derived `changes` map — the
    most recently active non-terminal change (via `pick_active_change`). Returns
    None if state.yaml is missing/unparseable or has no non-terminal change.
    Intentionally reads state.yaml directly (NOT via HotState — that's daemon-side);
    callers that talk to the daemon only need a change_id to pass in the request.
    """
    state_path = root / ".harness" / "state.yaml"
    if not state_path.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(state_path.read_text()) or {}
    except Exception:
        return None
    changes = data.get("changes")
    if not isinstance(changes, dict):
        return None
    candidates = (
        (str(cid), r.get("current_state", ""), r.get("last_event_at", ""))
        for cid, r in changes.items()
        if isinstance(r, dict)
    )
    return pick_active_change(candidates)
