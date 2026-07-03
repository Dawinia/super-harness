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

from super_harness.core.parse_ts import parse_ts

# Lowest-sorting sentinel: an unparseable/absent ``last_event_at`` sorts LOWEST so
# it never wins the "most recent" pick unless every candidate is unparseable.
_TS_MIN = datetime.min.replace(tzinfo=timezone.utc)


def pick_active_change(candidates: Iterable[tuple[str, str, object]]) -> str | None:
    """THE definition of "which change is active": given ``(change_id,
    current_state, last_event_at)`` triples, return the non-terminal change with
    the latest (parsed) ``last_event_at``, ties broken by ``change_id``. None if
    there is no non-terminal change. PURE — no I/O, no git. The ``last_event_at``
    slot is typed ``object`` because a state.yaml value may be a ``str`` OR a
    ``datetime`` (PyYAML loads an unquoted ISO timestamp as a ``datetime``);
    ``parse_ts`` handles both and never raises, and ``or _TS_MIN`` turns its
    ``None`` (unparseable/absent) into the lowest-sorting sentinel."""
    from super_harness.core.state import TERMINAL_STATES

    live = [(cid, at) for cid, st, at in candidates if st not in TERMINAL_STATES]
    if not live:
        return None
    return max(live, key=lambda t: (parse_ts(t[1]) or _TS_MIN, t[0]))[0]


def read_active_change_id(root: Path) -> str | None:
    """Resolve the active change_id from state.yaml's derived `changes` map — the
    most recently active non-terminal change (via `pick_active_change`). Returns
    None if state.yaml is missing/unparseable or has no non-terminal change.
    Reads state.yaml directly — the same in-process snapshot seam the PreToolUse
    gate uses (core.state_snapshot); there is no resident process to consult.
    """
    state_path = root / ".harness" / "state.yaml"
    if not state_path.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        # Valid YAML but not a mapping (scalar / non-empty list) — same
        # non-mapping guard state_yaml carries; this path feeds the
        # PreToolUse hook, where an AttributeError would exit 1 = a NON-blocking
        # error to Claude Code (silent fail-open).
        return None
    changes = data.get("changes")
    if not isinstance(changes, dict):
        return None
    candidates = (
        (str(cid), r.get("current_state", ""), r.get("last_event_at", ""))
        for cid, r in changes.items()
        if isinstance(r, dict)
    )
    # Guard the pick too: an unhashable `current_state` (list/dict) would raise
    # TypeError from pick_active_change's TERMINAL_STATES membership test. The
    # PreToolUse hot path uses core.state_snapshot (which guards this), but the
    # `done`/`verify`/`change`/`status` callers of read_active_change_id would
    # otherwise crash with a traceback on a hand-corrupted state.yaml.
    try:
        return pick_active_change(candidates)
    except Exception:
        return None
