"""Lifecycle + fail-safe supervisor for the super-harness daemon.

Layer 2 of the client/supervisor split per daemon-architecture §3.5:
this module wraps the pure protocol client (`client.query`) with the
two reconciliation paths that the spec calls out as the iteration-2
critical decision:

- `gate_pre_tool_use()` — HOT PATH. PreToolUse hook calls this. When
  the daemon is unreachable it spawns one fire-and-forget, writes an
  audit line, and returns ALLOW. It MUST NOT block on socket appearance
  (would violate AC-2 <50ms cold-start budget).

- `ensure_running()` — CLI PATH. `super-harness daemon start` calls this.
  Users invoking the foreground command expect "command returns →
  daemon is ready", so this path DOES block (up to `wait_seconds`)
  until the socket exists and `ping` succeeds.

Concurrent first-spawn races are deduplicated by the daemon's own PID
flock (per §2.4) — both `gate_pre_tool_use` and `ensure_running` may
spawn, but only one daemon survives the `fcntl.flock(LOCK_EX | LOCK_NB)`
check inside `daemonize()`; losers `sys.exit(1)` silently.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from super_harness.daemon._uds_path import resolve_socket_path
from super_harness.daemon.client import (
    DaemonTimeout,
    DaemonUnreachable,
    query,
)


def _sock_path(workspace_root: Path) -> Path:
    return resolve_socket_path(workspace_root)


def _pid_path(workspace_root: Path) -> Path:
    # No fallback: the PID file path has no sun_path length limit (unlike the
    # socket, which _sock_path resolves via _uds_path.resolve_socket_path).
    return workspace_root / ".harness" / "daemon.pid"


def _fallback_log_path(workspace_root: Path) -> Path:
    date = datetime.now(timezone.utc).date().isoformat()
    return (
        workspace_root
        / ".harness"
        / "operation-logs"
        / f"daemon-fallback-{date}.log"
    )


def _write_fallback_audit_log(
    workspace_root: Path,
    method: str,
    params: dict[str, Any],
    reason: str,
) -> None:
    """Append one JSON line to the daily fallback audit log (AC-10).

    Uses `os.write` on an `O_APPEND` fd: POSIX requires the seek-to-end + write
    under O_APPEND to be atomic per write() call for regular files (regardless
    of size — the PIPE_BUF bound applies only to pipes/FIFOs), so concurrent
    supervisors won't interleave bytes.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "params": params,
        "reason": reason,
    }
    line = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
    log_path = _fallback_log_path(workspace_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(log_path),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _spawn_daemon_fire_and_forget(workspace_root: Path) -> None:
    """Spawn `super-harness-daemon --workspace <root>` and immediately return.

    Hot-path companion: does NOT wait for the socket to appear; does NOT
    acquire any lock. Concurrent callers are deduplicated by the daemon's
    PID flock (§2.4): losers exit silently.
    """
    try:
        subprocess.Popen(
            ["super-harness-daemon", "--workspace", str(workspace_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        # PATH missing super-harness-daemon → can't spawn; supervisor still
        # returns fail-safe ALLOW. User sees install issue next CLI invocation.
        pass


def _signal_stale_daemon(workspace_root: Path, sig: int) -> bool:
    """Best-effort signal to the daemon recorded in `.harness/daemon.pid`.

    Used by the UC-6 version-mismatch path: SIGTERM the stale daemon so its
    signal handler runs `server.shutdown()`, unbinding socket + releasing PID
    flock, before the supervisor fires off a replacement.

    Returns True if the signal was delivered, False otherwise. Idempotent and
    safe to call on stale / nonexistent PID files (returns False silently).
    """
    pid_path = workspace_root / ".harness" / "daemon.pid"
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        # ProcessLookupError: daemon already dead (PID file stale).
        # PermissionError: PID belongs to a different user — security concern
        # outside v0.1 scope; treat as "not our problem" and let supervisor
        # fail-safe ALLOW + audit.
        return False


def gate_pre_tool_use(
    workspace_root: Path,
    *,
    tool: str,
    file: str | None,
    change_id: str | None,
) -> tuple[Literal["allow", "block"], str]:
    """HOT PATH. Resolve a PreToolUse gate decision.

    On success: returns `(decision, reason)` from the daemon's response.
    On daemon-unreachable: spawns daemon fire-and-forget, writes one
    audit line, returns `("allow", "daemon starting; first call permissive")`.

    Total budget on the unreachable path is <50ms per AC-2 — measured by
    `test_gate_pre_tool_use_returns_immediately_when_daemon_down`.
    """
    params: dict[str, Any] = {"tool": tool, "file": file, "change_id": change_id}
    sock = _sock_path(workspace_root)
    try:
        resp = query(sock, method="gate.pre_tool_use", params=params, timeout=0.2)
    except (DaemonUnreachable, DaemonTimeout) as exc:
        # v0.2 GAP (tracked): a real protocol-version bump lands HERE, not in
        # the 400-envelope branch below. decode_response version-gates the
        # error envelope itself (protocol.py), so a stale daemon's 400 reply
        # is undecodable by the newer client → ProtocolVersionMismatch →
        # DaemonUnreachable → this branch. That means the elaborate
        # SIGTERM-before-respawn logic below is unreachable in a real upgrade,
        # and this branch does a naive spawn (no SIGTERM) — the very respawn
        # loop UC-6 was meant to prevent. Harmless in v0.1 (only one protocol
        # version exists); must be resolved before v0.2 bumps PROTOCOL_VERSION.
        reason = f"daemon starting; first call permissive ({exc})"
        _spawn_daemon_fire_and_forget(workspace_root)
        _write_fallback_audit_log(
            workspace_root, "gate.pre_tool_use", params, reason
        )
        return "allow", reason

    # Protocol-level error envelope → treat as fallback (server reachable but
    # returned 4xx/5xx — e.g. UC-6 version mismatch). Still write audit line.
    if resp["error"] is not None:
        err = resp["error"]
        # UC-6 explicit branch: version mismatch means we're talking to a stale
        # daemon (older `pipx install` left a long-running process; user upgraded
        # super-harness; new client now mismatches).
        #
        # CRITICAL: a naive fire-and-forget Popen here is a no-op against a live
        # old daemon — the new daemon's grandchild tries fcntl.flock(LOCK_NB),
        # the old daemon still holds the PID-file flock, new grandchild exits 1,
        # NEXT call hits the SAME stale daemon → infinite "respawning" loop.
        #
        # Fix: SIGTERM the old daemon first (idempotent; safe on
        # ProcessLookupError if it's already gone). The old daemon's signal
        # handler runs server.shutdown() → unbinds socket + unlinks socket file
        # → exits → kernel releases the flock. THEN fire-and-forget the new
        # spawn. The new daemon may still race against the not-yet-exited old
        # daemon and exit 1, but the NEXT hook call (typically seconds later)
        # hits "no daemon" and ensure_running's path spawns cleanly.
        # Convergence within 1-2 hook calls.
        #
        # We do NOT sleep here — even <=100ms would burn 2x the 50ms hot-path
        # budget. Accept the 1-2-call convergence; the alternative race-free
        # design (synchronous wait-for-old-daemon-exit + new-daemon-up) violates
        # AC-2.
        if (err.get("code") == 400 and
                "version" in str(err.get("message", "")).lower()):
            reason = (
                f"daemon protocol version mismatch ({err.get('message')}); "
                "SIGTERM stale daemon + respawning"
            )
            # Step 1: SIGTERM the stale daemon (best-effort; idempotent)
            _signal_stale_daemon(workspace_root, signal.SIGTERM)
            # Step 2: fire-and-forget respawn (may exit-1 on this call if old
            # daemon hasn't fully exited; next call will succeed)
            _spawn_daemon_fire_and_forget(workspace_root)
            _write_fallback_audit_log(
                workspace_root, "gate.pre_tool_use", params, reason
            )
            return "allow", reason
        # Other 4xx/5xx → fail-safe ALLOW + audit, no respawn (no reason to
        # believe a new daemon would behave differently).
        reason = f"daemon error {err.get('code')}: {err.get('message')}"
        _write_fallback_audit_log(
            workspace_root, "gate.pre_tool_use", params, reason
        )
        return "allow", reason

    result = resp["result"] or {}
    decision = result.get("decision", "allow")
    reason = result.get("reason", "no reason supplied")
    if decision not in ("allow", "block"):
        # Defensive: unknown decision value → fail-safe ALLOW + audit.
        _write_fallback_audit_log(
            workspace_root,
            "gate.pre_tool_use",
            params,
            f"unknown decision from daemon: {decision!r}",
        )
        return "allow", f"unknown decision {decision!r}; fail-safe"
    return decision, reason


def ensure_running(
    workspace_root: Path,
    *,
    wait_for_socket: bool = True,
    wait_seconds: float = 5.0,
) -> int:
    """CLI PATH. Ensure the daemon is running; return its PID.

    Idempotent: if a daemon is already serving requests, returns its PID
    without spawning a new one.

    When `wait_for_socket=True` (default), blocks up to `wait_seconds`
    polling for socket appearance + ping success. This is the path used
    by `super-harness daemon start`: user expects "command returns →
    daemon ready". The 50ms hot-path budget does NOT apply.

    Raises:
        RuntimeError: if `wait_seconds` elapses without the daemon becoming
        reachable (likely spawn failure or PID flock loss).
    """
    sock = _sock_path(workspace_root)
    # Fast path: daemon already up.
    try:
        resp = query(sock, method="ping", params={}, timeout=0.5)
        if resp["error"] is None:
            return _read_pid(workspace_root)
    except (DaemonUnreachable, DaemonTimeout):
        pass

    _spawn_daemon_fire_and_forget(workspace_root)

    if not wait_for_socket:
        # Caller doesn't want to block; PID may not be readable yet.
        return _read_pid(workspace_root, default=0)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if sock.exists():
            try:
                resp = query(sock, method="ping", params={}, timeout=0.5)
                if resp["error"] is None:
                    return _read_pid(workspace_root)
            except (DaemonUnreachable, DaemonTimeout):
                pass
        time.sleep(0.05)

    raise RuntimeError(
        f"daemon did not become reachable within {wait_seconds:.1f}s at {sock}"
    )


def is_running(workspace_root: Path) -> bool:
    """Best-effort liveness probe (PID file + process + ping).

    Used by `super-harness daemon status`. See §2.4 / §3.7.
    """
    pid_path = _pid_path(workspace_root)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists, owned by someone else — unusual but treat as not-ours.
        return False
    sock = _sock_path(workspace_root)
    if not sock.exists():
        return False
    try:
        resp = query(sock, method="ping", params={}, timeout=0.5)
        return resp["error"] is None
    except (DaemonUnreachable, DaemonTimeout):
        return False


def _read_pid(workspace_root: Path, *, default: int = 0) -> int:
    pid_path = _pid_path(workspace_root)
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError, FileNotFoundError):
        return default
