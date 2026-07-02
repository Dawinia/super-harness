"""EventWriter — append-only writer for .harness/events.jsonl.

Atomicity guarantees (per lifecycle-event-model §2 + §3.9 #1):
- Single line per event (newline-terminated JSON)
- POSIX O_APPEND ensures kernel-atomic append across processes
- fcntl.flock on a `.harness/.events.lock` sentinel serializes the WHOLE
  validate→append critical section across writers (F4 fix). O_APPEND alone keeps
  each write() atomic, but emit first READS the stream to validate the new event
  against derived state; without a lock spanning read+append, two writers both
  validate the stale stream, both pass, and both append — landing an event that
  is illegal on replay (which the must_pass `lifecycle-ordering` baseline flags
  as tamper on an append-only log with no repair path). The flock closes that
  TOCTOU. Mirrors `post_emit.py`'s `.state.lock` idiom; lock order is
  events-before-state (emit releases `.events.lock` before the caller's
  `refresh_state_after_emit` takes `.state.lock`; never nested).
- threading.Lock is a PER-INSTANCE thread guard (serializes threads sharing ONE
  EventWriter). It is NOT process-wide: two EventWriter instances in one process
  are serialized by the flock, not this lock (kept as cheap defense-in-depth).
- fsync after each write — durable on power loss (cost: ~ms per emit; v0.1
  accepts this; v0.2 may add batch-fsync option)

Supported filesystems (spec §3.9 #1): Linux ext4, macOS APFS. NOT supported:
NFS / SMB / FUSE — these break atomic append AND advisory-lock semantics. flock
is POSIX-only (Linux/macOS), consistent with the module's local-fs stance.
README must call this out before users try to put .harness/ on a network drive.

emit-time validation: this writer accepts a `skip_validation: bool = False`
kwarg. When False (default) emit calls `emit_validation.validate_preconditions`
UNDER the flock, BEFORE writing — illegal transitions raise
`EmitPreconditionError` and nothing hits disk (strict per spec §3.8.1). Pass
`skip_validation=True` to bypass validation (used by replay/import tooling that
already vetted the stream); the append still holds the flock so a skip write
cannot slip into another writer's validate→append window.
"""
import fcntl
import os
import threading
from pathlib import Path

from super_harness.core.emit_validation import (
    EmitPreconditionError,
    validate_preconditions,
)
from super_harness.core.events import Event, serialize_event

__all__ = ["EmitPreconditionError", "EventWriter"]


# @decision:d-events-append-only
class EventWriter:
    """Append-only writer to events.jsonl.

    Thread-safe within a process (per-instance lock) AND across writer instances
    / processes (fcntl.flock on a sentinel). NOT safe on network filesystems
    (see module docstring).
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        # Cross-process critical-section lock sentinel. Sibling of events.jsonl,
        # equal to `paths.lock_path(root, "events")` (`.harness/.events.lock`);
        # derived from self.path so EventWriter stays path-only.
        self._lock_path = self.path.parent / ".events.lock"
        # Ensure parent directory exists — common case: `.harness/` is brand
        # new on first emit. mkdir(parents=True, exist_ok=True) is idempotent.
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Event, *, skip_validation: bool = False) -> None:
        """Append one event to events.jsonl.

        Args:
            event: the Event to write (already constructed by caller)
            skip_validation: when True, bypass emit-time precondition checks
                (illegal transitions + hard prereqs). Default False — emit is
                strict per spec §3.8.1 and raises EmitPreconditionError before
                touching disk. Set True only for replay/import paths where the
                event stream has already been vetted.

        Raises:
            EmitPreconditionError: if `skip_validation=False` and the event
                would create an illegal transition or is missing a hard
                prerequisite (e.g. implementation_complete without prior
                verification_passed on the same change_id). ALSO raised —
                regardless of `skip_validation` — for a non-str `timestamp`:
                the writer is the single choke point to disk and does not
                round-trip `parse_event_line`, so shape is enforced here (a
                widening of EmitPreconditionError's documented transition-only
                meaning; reusing it keeps the 12+ existing "emit rejected, stay
                alive" handlers working). Type-only: `""` stays legal (the
                dispatcher stamps blank timestamps before emit).
        """
        if not isinstance(event.timestamp, str):
            raise EmitPreconditionError(
                f"timestamp must be a string, got {type(event.timestamp).__name__}"
            )
        # Pure prep stays outside the lock to keep the critical section minimal.
        line = serialize_event(event) + "\n"
        data = line.encode("utf-8")
        with self._lock:  # per-instance thread guard (see class docstring)
            # Sentinel must exist before open() (read mode); touch is idempotent.
            self._lock_path.touch(exist_ok=True)
            with open(self._lock_path) as lock_file:
                # LOCK_EX blocks until acquired — spans validate+append so no
                # writer validates a stale stream then appends over another's
                # append (the F4 TOCTOU). Auto-released on close / process death.
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    if not skip_validation:
                        # READ under the lock — this is the whole point of F4.
                        validate_preconditions(self.path, event)
                    # O_APPEND — kernel guarantees atomicity of each write() on
                    # regular files (events fit well under 4KB after encoding).
                    fd = os.open(
                        self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644
                    )
                    try:
                        os.write(fd, data)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                finally:
                    # Explicit UN is belt-and-suspenders (close releases anyway),
                    # matching post_emit.py. Explicit > implicit.
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
