"""EventWriter — append-only writer for .harness/events.jsonl.

Atomicity guarantees (per lifecycle-event-model §2 + §3.9 #1):
- Single line per event (newline-terminated JSON)
- POSIX O_APPEND ensures kernel-atomic append across processes
- threading.Lock serializes multi-thread same-process writes (defense in depth;
  O_APPEND alone is enough but the lock makes the failure mode obvious)
- fsync after each write — durable on power loss (cost: ~ms per emit; v0.1
  accepts this; v0.2 may add batch-fsync option)

Supported filesystems (spec §3.9 #1): Linux ext4, macOS APFS. NOT supported:
NFS / SMB / FUSE — these break atomic append semantics. README must call this
out before users try to put .harness/ on a network drive.

emit-time validation: this writer accepts a `skip_validation: bool = False`
kwarg. When False (default) emit calls `emit_validation.validate_preconditions`
BEFORE writing — illegal transitions raise `EmitPreconditionError` and nothing
hits disk (strict per spec §3.8.1). Pass `skip_validation=True` to bypass (used
by replay/import tooling that already vetted the stream).
"""
import os
import threading
from pathlib import Path

from super_harness.core.emit_validation import (
    EmitPreconditionError,
    validate_preconditions,
)
from super_harness.core.events import Event, serialize_event

__all__ = ["EmitPreconditionError", "EventWriter"]


class EventWriter:
    """Append-only writer to events.jsonl.

    Thread-safe within a process (internal lock). Multi-process safe via
    POSIX O_APPEND. NOT safe on network filesystems (see module docstring).
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
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
        if not skip_validation:
            validate_preconditions(self.path, event)
        line = serialize_event(event) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            # O_APPEND is the critical flag — kernel guarantees atomicity of
            # each write() call on regular files (within PIPE_BUF on some FSes;
            # events should always fit well under 4KB after JSON encoding).
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
