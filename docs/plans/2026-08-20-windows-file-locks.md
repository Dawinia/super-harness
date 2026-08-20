---
change: 2026-08-20-windows-file-locks
stage: plan
scope:
  files:
    - src/super_harness/core/file_lock.py
    - src/super_harness/core/writer.py
    - src/super_harness/core/post_emit.py
    - src/super_harness/core/paths.py
    - src/super_harness/core/scope_match.py
    - tests/unit/core/test_file_lock.py
    - tests/unit/core/test_writer.py
    - tests/unit/core/test_post_emit.py
    - tests/unit/core/test_scope_match.py
    - tests/integration/core/test_writer_concurrency.py
    - tests/integration/cli/test_windows_lifecycle_entrypoint.py
    - docs/getting-started.md
    - pyproject.toml
    - docs/decisions/d-events-append-only.md
    - docs/plans/2026-08-20-windows-file-locks.md
tier_hint: Normal
---

# Native Windows lifecycle file locks

## Defect and feedback loop

On native Windows, resolving any lifecycle command that imports the event writer or
post-emit state refresh fails before Click can run it:

```text
ModuleNotFoundError: No module named 'fcntl'
```

The confirmed red command is:

```powershell
.\.venv\Scripts\super-harness.exe change start 2026-08-20-windows-file-locks
```

`change start`, `plan ready`, `review prepare`, and `review begin` all reach the same
unconditional imports in `core.writer` or `core.post_emit`. The failure occurs before an
event is appended.

After the lock bootstrap made those imports reachable, native Windows exposed a second
blocker in `review prepare`: `scope_match` launched Git with `text=True` but no explicit
encoding, so Python decoded a UTF-8 diff using the machine's GBK locale. A non-ASCII plan
character crashed the subprocess reader and left `stdout=None`. The same change must pin
Git's text decoding to UTF-8 so review bundle construction is deterministic across hosts.

## Design

Add one deep module, `core.file_lock`, whose interface is a blocking context manager for
an exclusive lock on a sentinel path. Callers do not know which operating-system adapter
is selected or how its lock region is prepared.

- POSIX uses `fcntl.flock(LOCK_EX)` and preserves the current semantics.
- Windows uses the standard-library `msvcrt` byte-range lock. The implementation owns a
  stable byte in the sentinel, positions every handle on that byte, retries an immediate
  lock request while it is contended, and always unlocks in `finally`.
- `EventWriter.emit` keeps its existing per-instance thread lock and places validation plus
  append inside the cross-process lock context. Append-only ordering and fsync stay intact.
- `refresh_state_after_emit` places derive plus state write inside the same interface.
- Imports are conditional inside `core.file_lock`; importing lifecycle modules on Windows
  must never attempt to import `fcntl`.

The optional observer host is out of scope. Its daemonization also depends on POSIX
`fork`, sessions, and signals, so replacing only its `fcntl` calls would create a false
claim of Windows support. This change supports native Windows lifecycle commands, not
`observe start` or `super-harness-daemon`.

## Test-first anchors

1. Add a native-Windows installed-entrypoint test that initializes a temporary external
   repository and drives `change start` then `plan ready`, asserting both persisted events
   and rebuilt state. Before the fix it fails with the user's exact missing-`fcntl` error.
2. Test the lock interface directly for repeated acquire/release and real cross-process
   exclusion. These tests run on both Windows and POSIX.
3. Adapt the existing writer lock-scope and race tests to assert through the lock interface
   rather than importing `fcntl` in the test. Keep the thread and subprocess race outcomes:
   exactly one legal append and one rejected transition.
4. Run the focused tests first, then the full unit/integration suite, ruff, mypy, decision
   checks, documentation checks, and `super-harness verify`.
5. Re-run the original command in native Windows PowerShell, then run `change start`,
   `plan ready`, `review prepare`, and `review begin` far enough in a disposable external
   repository to prove command reachability without disturbing a real project.
6. Add a scope-digest regression containing non-ASCII committed content and force the
   process preferred encoding away from UTF-8; bundle hashing must still receive decoded
   Git output rather than `None` or a locale exception.

## Documentation and decision conformance

Update the getting-started support boundary and package classifiers to state that lifecycle
commands are supported natively on Windows while the optional observer daemon remains
POSIX-only. Reconcile `d-events-append-only` because its anchored writer changes; the
invariant remains true because the new lock continues to cover validate and append and
does not mutate, truncate, or reorder existing events.
