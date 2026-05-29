"""DaemonServer — AF_UNIX accept loop + per-connection gate dispatch.

Implements daemon-architecture §3.2 (threading model) + §3.4 (request flow) +
§3.6 #8 (socket path length fallback) + UC-8 (oversized request) + UC-9
(BrokenPipe survival) + AC-9 (0o600 socket).

The `_PRE_TOOL_USE_DECISIONS` table mirrors lifecycle-event-model §3.7's
"Gate 矩阵" verbatim: each of the 11 states maps to an `(decision, reason)`
pair. The daemon does NOT invent gate policy — it only executes the table.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from super_harness.daemon._uds_path import UDS_PATH_MAX, resolve_socket_path
from super_harness.daemon.framework_observer import (
    FrameworkObserverManager,
    build_manager_failsafe,
)
from super_harness.daemon.hot_state import HotState
from super_harness.daemon.protocol import (
    MAX_REQUEST_BYTES,
    PROTOCOL_VERSION,
    GateQueryRequest,
    GateQueryResponse,
    ProtocolError,
    ProtocolVersionMismatch,
    decode_request,
    encode_response,
)
from super_harness.gates.decisions import PRE_TOOL_USE_DECISIONS

__all__ = ["DaemonServer", "daemonize", "main"]

_log = logging.getLogger(__name__)


# -- JSON-lines logging per spec §3.6 -------------------------------------

_LOGRECORD_STANDARD = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
    # Python 3.12+ added `taskName` for asyncio task name on each LogRecord.
    # Include here so it's filtered out of the JSON-extras payload (otherwise
    # every line would carry a redundant `"taskName": null` for sync code).
    "taskName",
})


class _JsonLineFormatter(logging.Formatter):
    """Emit one JSON object per log record, with stable schema for AI parsing.

    Schema (always present): ts (UTC ISO 8601), level, name, msg.
    Optional: exc (formatted traceback), plus any JSON-serializable extras
    passed via `logger.info(..., extra={...})` (e.g. method, change_id, pid).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Promote `extra=` kwargs to top-level keys so AI grep/jq is easy.
        for k, v in record.__dict__.items():
            if k in _LOGRECORD_STANDARD or k.startswith("_"):
                continue
            try:
                json.dumps(v)  # only include JSON-serializable extras
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(log_path: Path) -> None:
    """Wire root `super_harness.daemon` logger to JSON-lines file handler.

    Per spec §3.6: logs are for AI self-diagnosis, NOT human ops, so the
    format is machine-readable (one JSON object per line) and the file
    sits at `.harness/daemon.log` (alongside state.yaml). No rotation
    in v0.1 — daemon lifetime is bounded by user session (deferred).
    """
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(_JsonLineFormatter())
    root = logging.getLogger("super_harness.daemon")
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers if main() is called twice in the same process
    # (defensive — daemonize() should prevent this, but tests may call
    # _configure_logging directly).
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.propagate = False


class DaemonServer:
    """Long-running UDS server for PreToolUse gate decisions.

    Per lifecycle-event-model §3.7 Gate 矩阵.
    """

    # 11-state decision table from lifecycle-event-model §3.7. The literal now
    # lives in `super_harness.gates.decisions` (single source of truth, shared
    # with PreToolUseGate); this alias keeps the historical attribute name so
    # the dispatch site below is unchanged.
    _PRE_TOOL_USE_DECISIONS: ClassVar[dict[str, tuple[str, str]]] = (
        PRE_TOOL_USE_DECISIONS
    )

    def __init__(
        self,
        *,
        workspace_root: Path,
        socket_path: Path,
        state_path: Path,
        events_path: Path,
        max_parallelism: int = 4,
    ) -> None:
        # §3.6 #8 path-length fallback now lives in _uds_path.resolve_socket_path.
        # Caller passes the requested socket_path (typically <workspace>/.harness/daemon.sock);
        # we derive workspace_root and let the shared helper apply the same algorithm
        # that the client/supervisor/hook use to discover the socket. Keep the length
        # check + warning log here because the helper is silent (it's used by clients
        # too, where a warning is inappropriate); the daemon-side fall-back is the
        # only place that warrants an audit log line.
        if len(str(socket_path).encode("utf-8")) > UDS_PATH_MAX:
            workspace_root = socket_path.parent.parent  # strip /.harness/daemon.sock
            resolved = resolve_socket_path(workspace_root)
            _log.warning(
                "socket path %s exceeds UDS limit (%d bytes); falling back to %s",
                socket_path,
                UDS_PATH_MAX,
                resolved,
            )
            socket_path = resolved

        self.workspace_root: Path = workspace_root
        self.socket_path: Path = socket_path
        self.state_path: Path = state_path
        self.events_path: Path = events_path
        self.max_parallelism: int = max_parallelism
        self._hot_state: HotState = HotState(state_path)
        self._sock: socket.socket | None = None
        self._stop: threading.Event = threading.Event()
        # Framework file-watcher manager (OI-7). Populated in serve_forever()
        # POST-fork (daemonize asserts single-thread BEFORE fork — Observers
        # must spawn after). None until then / when adapter setup fails.
        self._framework_observers: FrameworkObserverManager | None = None

    def serve_forever(self) -> None:
        """Bind UDS socket + accept loop until `shutdown()`."""
        # §3.9 #9: fork-then-thread invariant — caller (daemonize) must run
        # before any thread is spawned. Here we already past that point; we are
        # only spawning per-conn threads from the main accept thread.
        #
        # Framework watchers (OI-7) spawn HERE (post-fork): the daemonize()
        # `assert active_count()==1` runs BEFORE fork, so Observers must start
        # after. FAIL-SAFE (Axiom 3): build_manager_failsafe swallows a corrupt
        # adapters.yaml; we additionally guard the start() so NO watcher-setup
        # failure can crash main()'s loop (which would log "daemon main loop
        # crashed" + return 1, killing the gate hot-path). On any failure we
        # serve the accept loop with no watchers — gate decisions stay available.
        try:
            self._framework_observers = build_manager_failsafe(self.workspace_root)
            if self._framework_observers is not None:
                self._framework_observers.start()
        except Exception:
            _log.warning(
                "framework observe: watcher start failed; serving with no watchers",
                extra={"log_reason": "watcher_start_failed"},
            )
            self._framework_observers = None
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)  # AC-9
        sock.listen(64)
        sock.settimeout(0.5)  # let _stop break the accept loop
        self._sock = sock
        _log.info("daemon listening on %s", self.socket_path)
        try:
            while not self._stop.is_set():
                try:
                    conn, _addr = sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                threading.Thread(
                    target=self._serve_conn, args=(conn,), daemon=True
                ).start()
        finally:
            # Stop framework watchers so an accept-loop crash never orphans the
            # Observer threads. stop() is idempotent — shutdown() may also call
            # it; whichever fires first wins, the other is a no-op.
            self._stop_framework_observers()
            try:
                sock.close()
            except OSError:
                pass
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def shutdown(self) -> None:
        """Signal the accept loop to exit + stop framework watchers. Idempotent."""
        self._stop.set()
        # Stop + join the framework Observers (bounded) so SIGTERM shutdown
        # leaves no leaked watcher threads. Idempotent with serve_forever()'s
        # finally (both may call it).
        self._stop_framework_observers()
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    def _stop_framework_observers(self) -> None:
        """Stop + join the framework Observer manager (bounded). Idempotent.

        Safe to call multiple times (manager.stop() is itself idempotent) and
        safe when no manager was ever set (adapter setup failed / not yet
        started). Never raises — a watcher-shutdown error must not mask the
        primary shutdown path.
        """
        manager = self._framework_observers
        if manager is None:
            return
        try:
            manager.stop()
        except Exception:
            _log.exception("framework observe: manager.stop() failed during shutdown")

    def _serve_conn(self, conn: socket.socket) -> None:
        """Read newline-delimited requests until EOF; reply per line.

        DoS defense: each `readline()` is capped at `MAX_REQUEST_BYTES + 1`
        bytes. A malicious client streaming a 100MB line without newline
        would otherwise stall this thread for the duration of the full
        read. With the cap, we read at most 1MiB+1 byte before deciding to
        reject. The "+1" is the sentinel that lets us distinguish "line
        ended within cap" from "cap exceeded mid-line" (see UC-8).
        """
        try:
            with conn.makefile("rb") as rf:
                while True:
                    line = rf.readline(MAX_REQUEST_BYTES + 1)
                    if not line:
                        break  # EOF
                    if len(line) > MAX_REQUEST_BYTES:
                        # Reject without parse attempt (UC-8). Close the
                        # connection — the client is misbehaving and any
                        # remaining bytes on the socket cannot be reliably
                        # framed (we may be mid-line).
                        response = encode_response(GateQueryResponse(
                            id=None,
                            result=None,
                            error={"code": 400,
                                   "message": f"request exceeds {MAX_REQUEST_BYTES} bytes"},
                        ))
                        try:
                            conn.sendall(response)
                        except BrokenPipeError:
                            pass
                        break
                    self._handle_line(conn, line)
        except BrokenPipeError:
            _log.warning("client closed connection mid-reply")  # UC-9
        except Exception:
            _log.exception("connection handler crashed")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, conn: socket.socket, line: bytes) -> None:
        if not line:
            return
        try:
            req = decode_request(line)
        except ProtocolVersionMismatch as exc:
            self._send(conn, GateQueryResponse(id=None, result=None,
                                               error={"code": 400, "message": str(exc)}))
            return
        except ProtocolError as exc:
            self._send(conn, GateQueryResponse(id=None, result=None,
                                               error={"code": 400, "message": str(exc)}))
            return
        try:
            if req.method == "gate.pre_tool_use":
                resp = self._gate_pre_tool_use(req)
            elif req.method == "ping":
                resp = GateQueryResponse(
                    id=req.id,
                    result={"version": PROTOCOL_VERSION},
                    error=None,
                )
            else:
                resp = GateQueryResponse(
                    id=req.id,
                    result=None,
                    error={"code": 404, "message": f"unknown method: {req.method}"},
                )
        except Exception as exc:
            _log.exception("method dispatch failed for %s", req.method)
            resp = GateQueryResponse(
                id=req.id,
                result=None,
                error={"code": 500, "message": f"internal error: {exc}"},
            )
        self._send(conn, resp)

    def _gate_pre_tool_use(self, req: GateQueryRequest) -> GateQueryResponse:
        params: dict[str, Any] = req.params
        change_id = params.get("change_id")
        if not change_id:
            # UC-7 framing per spec §3.6: silent ALLOW is anti-pattern.
            # AI agents reading daemon.log must see WHY a permissive decision
            # was issued. No change_id supplied = no policy to apply.
            _log.info(
                "gate.pre_tool_use: no change_id; allowing",
                extra={"log_reason": "no_change_id", "tool": params.get("tool"),
                       "file": params.get("file")},
            )
            return GateQueryResponse(
                id=req.id,
                result={"decision": "allow", "reason": "no active change",
                        "current_state": None},
                error=None,
            )
        record = self._hot_state.get_change(str(change_id))
        if record is None:
            # UC-7 framing per spec §3.6: distinguish "no record found" from
            # "no change_id supplied". HotState resolves to None on both
            # FileNotFoundError and unknown change_id — both worth logging
            # so an AI debugger can correlate with state.yaml contents.
            _log.info(
                "gate.pre_tool_use: no record for change_id; allowing",
                extra={"change_id": change_id, "log_reason": "no_record",
                       "tool": params.get("tool"), "file": params.get("file")},
            )
            return GateQueryResponse(
                id=req.id,
                result={"decision": "allow", "reason": "no active change",
                        "current_state": None},
                error=None,
            )
        current_state = record.get("current_state", "INTENT_DECLARED")
        decision, reason = self._PRE_TOOL_USE_DECISIONS.get(
            current_state, ("block", f"unknown state: {current_state}")
        )
        return GateQueryResponse(
            id=req.id,
            result={"decision": decision, "reason": reason, "current_state": current_state},
            error=None,
        )

    @staticmethod
    def _send(conn: socket.socket, resp: GateQueryResponse) -> None:
        try:
            conn.sendall(encode_response(resp))
        except BrokenPipeError:
            _log.warning("client closed before reply could be written")  # UC-9


# -- Signal handlers ------------------------------------------------------

def _install_signal_handlers(server: DaemonServer) -> None:
    """Install SIGTERM (graceful shutdown), SIGINT, SIGPIPE handlers.

    AC-8: SIGTERM → server.shutdown() → accept loop exits → socket file
    unlink in `finally`, all within the 2-second budget.

    SIGPIPE → SIG_IGN: a client crash mid-write produces EPIPE on the
    daemon's next `sendall`, which the per-connection thread already
    catches as `BrokenPipeError` (UC-9). Without `SIG_IGN`, Python's
    default disposition for SIGPIPE *may* terminate the daemon process
    on `write()` (interpreter version-dependent). Ignoring is the
    canonical daemon idiom (Stevens APUE §10.13).
    """
    signal.signal(signal.SIGTERM, lambda *_: server.shutdown())
    signal.signal(signal.SIGINT, lambda *_: server.shutdown())  # Ctrl-C if foreground
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)


# -- POSIX double-fork daemonize per spec §3.3 + Stevens APUE §13.3 -------

def daemonize(pid_path: Path, log_path: Path) -> None:
    """Self-daemonize via POSIX double-fork.

    Invariants enforced (in order):
    - Spec §3.9 #9: assert `threading.active_count() == 1` before any fork.
      POSIX fork() in a multi-threaded process is undefined behavior;
      mutexes held by non-forking threads remain locked forever in the
      child. This assertion makes the contract loud rather than letting
      the daemon silently deadlock.
    - Stevens APUE §13.3 conventions:
        - `os.umask(0)` after setsid — clear inherited umask so app sets
          explicit file permissions (e.g. socket = 0o600 in serve_forever).
        - `os.chdir("/")` after second fork — daemon must not pin the
          user's cwd (would block unmount of a workspace volume).
        - Close stdio fds and redirect to /dev/null + log file.
    - PID file holds an exclusive `fcntl.flock(LOCK_EX | LOCK_NB)` for
      the lifetime of the process (auto-released by kernel on death).
      Lost-race losers exit 1 silently — supervisor-side deduplication
      (UC-5 / AC-4).

    This function does NOT return in the original or first-child process —
    each calls `os._exit(0)`. Only the final grandchild returns; that
    grandchild IS the daemon and continues into `main()`.
    """
    # Spec §3.9 #9: single-thread invariant
    assert threading.active_count() == 1, (
        f"daemonize() called with {threading.active_count()} live threads; "
        "POSIX fork in a multi-threaded process is undefined behavior. "
        "Must run before any thread is spawned (no atexit handlers either)."
    )

    # First fork: parent exits → child is orphaned + reparented to init,
    # making it eligible to become a session leader (setsid).
    if os.fork() != 0:
        os._exit(0)

    os.setsid()
    os.umask(0)  # APUE: explicit permissions, no inherited mask

    # Second fork: prevents the daemon from re-acquiring a controlling
    # terminal (only a session leader can acquire one; we just made the
    # first child a session leader, so we fork again to demote ourselves).
    if os.fork() != 0:
        os._exit(0)

    os.chdir("/")  # APUE: don't pin user's cwd

    # Redirect stdio: stdin=/dev/null, stdout/stderr=log file. This MUST
    # happen before the next `print`/`sys.std{out,err}.write` call or the
    # daemon would write to the terminal that's about to be detached.
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    logfd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(devnull, 0)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    os.close(devnull)
    os.close(logfd)

    # PID file flock: single-instance enforcement (AC-4 / UC-5).
    pid_fd = os.open(str(pid_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another daemon won the race. Exit 1 silently — supervisor treats
        # losers as no-ops (only one survivor per workspace).
        sys.exit(1)
    os.ftruncate(pid_fd, 0)
    os.write(pid_fd, f"{os.getpid()}\n".encode())
    # KEEP pid_fd OPEN for life of process. The flock auto-releases on
    # process death (kernel-enforced); closing the fd would release it
    # immediately, defeating single-instance enforcement.


# -- Entry-point ----------------------------------------------------------

def main() -> int:
    """`super-harness-daemon` entry-point per pyproject.toml [project.scripts].

    Argparse-only (NO click) to keep cold-start lean. Click pulls in ~12ms
    of import cost; the daemon launcher already pays double-fork + Python
    interpreter startup, so any savings on import-time helps the
    supervisor's "spawn → socket appears" budget (UC-2).

    Exit codes:
        0  daemon exited cleanly (SIGTERM)
        1  daemon main loop crashed (or PID flock loser)
        3  no .harness/ at --workspace (EXIT_NO_CONFIG)
    """
    parser = argparse.ArgumentParser(prog="super-harness-daemon")
    parser.add_argument("--workspace", default=".", type=Path)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    harness_dir = workspace / ".harness"
    if not harness_dir.exists():
        print(
            f"super-harness-daemon: no .harness/ directory at {workspace}",
            file=sys.stderr,
        )
        return 3  # EXIT_NO_CONFIG per cli-command-surface §2.3.X

    pid_path = harness_dir / "daemon.pid"
    log_path = harness_dir / "daemon.log"
    socket_path = harness_dir / "daemon.sock"
    state_path = harness_dir / "state.yaml"
    events_path = harness_dir / "events.jsonl"

    # Self-daemonize. Does NOT return in the original/first-child processes;
    # only the grandchild continues past this call.
    daemonize(pid_path, log_path)

    # We are now the daemon. Configure structured logging BEFORE any
    # log call so the first record is JSON-formatted.
    _configure_logging(log_path)
    log = logging.getLogger("super_harness.daemon")
    log.info(
        "super-harness-daemon starting",
        extra={"workspace": str(workspace), "pid": os.getpid()},
    )

    server = DaemonServer(
        # `workspace` was resolved to an ABSOLUTE path BEFORE daemonize()'s
        # chdir("/"), so framework watch paths (workspace/openspec/changes) stay
        # valid after the daemon detaches from the user's cwd.
        workspace_root=workspace,
        socket_path=socket_path,
        state_path=state_path,
        events_path=events_path,
    )
    _install_signal_handlers(server)

    try:
        server.serve_forever()
    except Exception:
        log.exception("daemon main loop crashed")
        return 1

    log.info("super-harness-daemon stopped cleanly")
    return 0
