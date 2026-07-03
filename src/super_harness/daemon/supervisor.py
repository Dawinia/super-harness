"""Lifecycle for the OPTIONAL framework-observer host (design 2026-07-03).

Post-demotion the resident process is no longer on the gate hot path (the
PreToolUse gate decides in-process via core.state_snapshot + gates.pre_tool_use).
This module manages only the observer host:

- spawn by ABSOLUTE path resolved from the running interpreter's scripts dir
  (`sysconfig.get_path("scripts")` → the venv/pipx `bin/`), NOT PATH.
  console_scripts install `super-harness` and `super-harness-daemon` side by side
  there, but the hook/CLI environment often has no venv bin/ on PATH — a bare-name
  spawn then raises OSError and the process silently never comes up (the
  month-long fail-open root cause). This realizes the design's "absolute path,
  not bare name" intent while being invocation-independent: unlike `sys.argv[0]`
  (which under `python -m pytest` points at pytest's package dir) and unlike
  `Path(sys.executable).resolve()` (which walks a symlinked `.venv/bin/python`
  out of the venv). If the binary is genuinely absent we RAISE (the explicit
  `observe start` path — a clear error beats a PATH-ambiguous bare-name spawn).
- liveness by pidfile flock: `daemonize()` holds `LOCK_EX` on `.harness/daemon.pid`
  for the process lifetime, so a non-blocking `LOCK_EX` probe that WOULD block
  proves a live host holds it; one that acquires proves nobody does. (flock is
  advisory but conflicts across processes regardless of open mode, on Linux and
  macOS alike; the kernel releases it on process death, so a `kill -9`'d host's
  stale pidfile correctly reads as dead.)

No socket, no protocol, no client, no fail-open, no fallback audit.
"""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sysconfig
import time
from pathlib import Path


def _pid_path(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "daemon.pid"


def _observer_binary() -> str:
    """Absolute path to the `super-harness-daemon` entry-point (observer host),
    resolved from the RUNNING INTERPRETER's scripts dir (`sysconfig.get_path
    ("scripts")` → the venv/pipx `bin/` where console_scripts install side by
    side). Invocation-independent (works under the console script AND under
    `python -m pytest`, unlike `sys.argv[0]`).

    Raises:
        RuntimeError: the binary is absent (unusual install). We do NOT fall back
        to a bare `super-harness-daemon` — that reintroduces the PATH ambiguity
        this whole change exists to kill.
    """
    binary = Path(sysconfig.get_path("scripts")) / "super-harness-daemon"
    if not binary.exists():
        raise RuntimeError(
            f"observer host binary not found in the scripts dir ({binary}); "
            "install super-harness-daemon alongside super-harness"
        )
    return str(binary)


def is_running(workspace_root: Path) -> bool:
    """True iff a live observer host holds the pidfile flock. No ping, no socket.

    Opens the pidfile O_RDONLY (flock is mode-independent — it works on a
    read-only fd — so O_RDONLY avoids the EROFS/EACCES failure modes O_RDWR would
    add on a read-only mount/pidfile) and probes with a SHARED lock. LOCK_SH is
    load-bearing, NOT LOCK_EX: the host holds LOCK_EX for its lifetime, so a
    LOCK_SH probe still conflicts with a live host (→ BlockingIOError → running).
    But two concurrent probe PROCESSES (the real case: parallel `observe
    start`/`status`) using LOCK_EX would conflict with EACH OTHER on a stale/unheld
    pidfile — one acquires, the other gets BlockingIOError and falsely reports
    "running" when nobody holds the lock (→ `observe start` no-ops, host never
    comes up). Shared-lock requests are mutually compatible across processes, so
    concurrent probes on an unheld file all correctly report not-running. Any
    OSError other than BlockingIOError → 'cannot determine → not running' so
    `status`/`start` never raise on a quirk."""
    pid_path = _pid_path(workspace_root)
    if not pid_path.exists():
        return False
    try:
        fd = os.open(str(pid_path), os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True  # held by a live host's LOCK_EX
        except OSError:
            return False  # can't probe → treat as not-running
        # Acquired the shared lock → no host holds LOCK_EX; release and report dead.
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)


def ensure_running(workspace_root: Path, *, wait_seconds: float = 5.0) -> int:
    """Spawn the observer host (idempotent) and block until it holds the pidfile
    flock. Returns the host PID.

    Raises:
        RuntimeError: sibling binary absent, spawn failed, or host did not become
        live in time.
    """
    # Only spawn if not already live. Both paths (already-running and just-spawned)
    # then fall through to the SAME wait loop — the fast path must NOT early-return
    # `_read_pid()` directly, since is_running() flips True the instant the flock is
    # held (in daemonize(), BEFORE ftruncate+write(pid)); a bare _read_pid() there
    # can return 0 from a half-written pidfile.
    if not is_running(workspace_root):
        binary = _observer_binary()  # raises if absent
        try:
            subprocess.Popen(
                [binary, "--workspace", str(workspace_root)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeError(
                f"could not spawn observer host ({binary}): {exc}"
            ) from exc
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        # Poll until the host holds the flock AND the pid is readable, so a caller
        # never gets 0 from a half-written pidfile (already-running or just-spawned).
        if is_running(workspace_root):
            pid = _read_pid(workspace_root)
            if pid > 0:
                return pid
        time.sleep(0.05)
    raise RuntimeError(f"observer host did not become live within {wait_seconds:.1f}s")


def stop(workspace_root: Path, *, wait_seconds: float = 2.0) -> bool:
    """SIGTERM the observer host and wait for it to exit (flock release).

    Returns True if it stopped (or was already stopped), False on timeout.
    """
    if not is_running(workspace_root):
        return True
    pid = _read_pid(workspace_root)
    if pid <= 0:
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not is_running(workspace_root):
            return True
        time.sleep(0.05)
    return not is_running(workspace_root)


def _read_pid(workspace_root: Path, *, default: int = 0) -> int:
    try:
        return int(_pid_path(workspace_root).read_text(encoding="utf-8").strip())
    except (ValueError, OSError):  # FileNotFoundError ⊂ OSError
        return default
