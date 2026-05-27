"""Synchronous state.yaml rebuild after every emit (Task 1.9 / B-3 fix).

Plan promises deterministic gate decisions based on state.yaml, but without a
post-emit refresh the cache lags events.jsonl until someone runs `state rebuild`
manually. This helper closes that gap: every emit site (CLI commands,
SensorDispatcher result handler, adapter observe loop) calls
`refresh_state_after_emit` immediately after `EventWriter.emit` returns.

v0.1 = full rebuild per lifecycle-event-model §3.8.2 (replay every line each
call). v0.2 may add incremental rebuild if the cost becomes a bottleneck.

Concurrency model:
- fcntl.flock on `.harness/.state.lock` serializes concurrent rebuilds across
  processes (matches the events.jsonl POSIX-only stance per spec §3.9).
- LOCK_EX waits — rebuilds are short (<10ms for tiny streams; full rebuild cost
  scales with event count). Acceptable for v0.1; daemon (Phase 2) will batch.
- The lock guards the rebuild + write sequence, not events.jsonl itself —
  EventWriter already provides multi-process atomic O_APPEND for emit.

Note: this module duplicates the 8-line file-tail scan from cli/state.py for
last_reduced_event_id. We have two call sites — per rule-of-three we defer
extraction until the third arrives. The cleanest fix is to have derive_state
return (states, last_event_id) as a tuple; that's a future refactor.
"""
from __future__ import annotations

import fcntl
from pathlib import Path

from super_harness.core.events import EventSchemaError, parse_event_line
from super_harness.core.paths import events_path, lock_path, state_path
from super_harness.core.reducer import derive_state
from super_harness.core.state_yaml import write_state_yaml


def refresh_state_after_emit(workspace_root: Path) -> None:
    """Synchronously rebuild state.yaml from events.jsonl.

    MUST be called after every emit point so state.yaml never lags the event
    stream. Safe to call when events.jsonl does not yet exist — produces an
    empty state.yaml (changes={}, last_reduced_event_id="").

    Args:
        workspace_root: the directory containing `.harness/` (caller resolves
            via find_harness_root; this helper does not walk up).
    """
    lock_file = lock_path(workspace_root, "state")
    # Sentinel must exist before open() (default read mode); touch is idempotent.
    # Do NOT open in "w" mode — that would truncate the (empty) sentinel on every
    # call, which is harmless but pointlessly noisy in inotify/strace output.
    lock_file.touch(exist_ok=True)
    with open(lock_file) as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            events_file = events_path(workspace_root)
            derived = derive_state(events_file)
            # last_reduced_event_id semantics per spec §3.8.2 + §3.8.3: the
            # literal event_id of the last non-blank parseable line in
            # events.jsonl (file-position truth, NOT dict-iteration order).
            # Same fix as cli/state.py state_rebuild — keep these in lockstep.
            last_id = ""
            if events_file.exists():
                for line in reversed(events_file.read_text().splitlines()):
                    if not line.strip():
                        continue
                    try:
                        last_id = parse_event_line(line).event_id
                        break
                    except EventSchemaError:
                        # Mirror reducer's tolerance: skip malformed tail lines
                        # rather than crash. Reducer warns at WARNING; we stay
                        # silent here since the reducer already logged.
                        continue
            write_state_yaml(
                state_path(workspace_root),
                derived,
                last_reduced_event_id=last_id,
            )
        finally:
            # Explicit LOCK_UN is belt-and-suspenders — the `with open()` block
            # closes the fd which releases the lock anyway. Explicit > implicit.
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
