"""Resolve the *active* change_id from a workspace's state.yaml.

v0.1 convention (mirrors `super-harness status`): the active change is the
first non-terminal change. state.yaml is reducer-generated and carries NO
top-level `active_change_id` field — "active" is a derived notion computed at
read time. This helper is shared by the click-less PreToolUse hook entry-point
(`daemon.hook_entry`) and the `super-harness gate check` CLI so both resolve
the same change when no explicit `--change-id` / env override is supplied.

Returns None if state.yaml is missing/unparseable or has no non-terminal
change.
"""
from __future__ import annotations

from pathlib import Path


def read_active_change_id(root: Path) -> str | None:
    """Resolve the active change_id from state.yaml's derived `changes` map.

    The active change is the first non-terminal change in
    ``root/.harness/state.yaml``'s `changes` map. Returns None if state.yaml is
    missing/unparseable or has no non-terminal change. Intentionally reads
    state.yaml directly (NOT via HotState — that's daemon-side); callers that
    talk to the daemon only need a change_id to pass in the request.
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
    from super_harness.core.state import TERMINAL_STATES

    for change_id, record in changes.items():
        if isinstance(record, dict) and record.get("current_state") not in TERMINAL_STATES:
            return str(change_id)
    return None
