"""state.yaml atomic serialization per lifecycle-event-model §3.8.5 invariant 2.

Writes the reducer's `dict[change_id, ChangeState]` output to disk via a
write-temp-then-rename dance (atomic on POSIX). v0.1 always rewrites the whole
file — no incremental / no snapshots. See reducer.py module docstring for
rationale; v0.2 may revisit if rewrite cost becomes a bottleneck for large
event logs.

`read_state_yaml` is the inverse direction, returning a raw dict (no dataclass
reconstruction in v0.1 — that's just `ChangeState(**inner_dict)` at the call
site if needed).
"""
from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from super_harness.core.state import ChangeState

_HEADER = (
    "# .harness/state.yaml\n"
    "# AUTO-GENERATED from events.jsonl. DO NOT EDIT.\n"
    "# Regenerate: `super-harness state rebuild`.\n"
)


def write_state_yaml(
    path: Path,
    changes: dict[str, ChangeState],
    *,
    last_reduced_event_id: str,
) -> None:
    """Atomically write reducer output to `path`.

    Atomicity: writes to `{path}.tmp` then `os.replace()`s into place. POSIX
    `rename(2)` guarantees the target either points at the old file or the new
    one — no partial-content window. `os.replace` (not `os.rename`) is required
    because `os.rename` may fail when the target exists on some platforms.

    `last_reduced_event_id` is the event_id of the last event the reducer
    consumed; used by daemon (Phase 2) to short-circuit when events.jsonl tail
    hasn't advanced past this point.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "last_reduced_event_id": last_reduced_event_id,
        "last_reduced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "changes": {cid: asdict(cs) for cid, cs in changes.items()},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write(_HEADER)
        yaml.safe_dump(body, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)  # atomic on POSIX (Linux ext4, macOS APFS)


def read_state_yaml(path: Path) -> dict[str, Any]:
    """Load state.yaml and return its parsed dict (empty dict for empty file)."""
    result = yaml.safe_load(path.read_text()) or {}
    assert isinstance(result, dict), f"state.yaml root must be a mapping, got {type(result)}"
    return result
