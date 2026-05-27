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
kwarg whose body is currently a no-op (validation is wired in Task 1.5 via
`emit_validation.validate_preconditions`). Existing callers pass nothing →  get
defaults; Task 1.5 will toggle the behavior without breaking the API.
"""
import os
import threading
from pathlib import Path

from super_harness.core.events import Event, serialize_event


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
            event: the Event to write (already constructed + validated by caller)
            skip_validation: kwarg reserved for Task 1.5 emit-time validation;
                currently a no-op (validation will be wired here). Future-proof
                API so Task 1.5 doesn't break callers.
        """
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
