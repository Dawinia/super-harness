"""Framework-observer host process — `super-harness-daemon` entry-point.

Demoted from a UDS gate server to a pure observation host (design 2026-07-03):
the PreToolUse gate now decides in-process (core.state_snapshot +
gates.pre_tool_use). This process's ONLY job is to host watchdog Observers that
watch framework artifacts and emit lifecycle events (daemon.framework_observer,
#67 flock on the write path). Liveness is the pidfile flock that `daemonize()`
holds for the process lifetime; there is no socket, no protocol, and no
request/response interface. The decision plane never talks to this process —
they meet only through events.jsonl / state.yaml.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from super_harness.daemon.framework_observer import build_manager_failsafe

__all__ = ["daemonize", "main", "run_observer_host"]

_log = logging.getLogger(__name__)


# -- JSON-lines logging (unchanged) ---------------------------------------

_LOGRECORD_STANDARD = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class _JsonLineFormatter(logging.Formatter):
    """Emit one JSON object per log record (stable schema for AI parsing)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in _LOGRECORD_STANDARD or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(log_path: Path) -> None:
    """Wire the `super_harness.daemon` logger to a JSON-lines file handler."""
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(_JsonLineFormatter())
    root = logging.getLogger("super_harness.daemon")
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.propagate = False


def run_observer_host(workspace_root: Path, stop: threading.Event) -> None:
    """Start framework Observers, block until `stop` is set, then stop them.

    FAIL-SAFE (Axiom 3 — observation must never crash noisily): a corrupt
    adapters.yaml makes build_manager_failsafe return None (host idles until
    SIGTERM); ANY watcher-start error is logged and swallowed. Observers spawn
    HERE — AFTER daemonize()'s single-thread `assert active_count()==1` fork —
    matching the historical post-fork ordering.
    """
    manager = None
    try:
        manager = build_manager_failsafe(workspace_root)
        if manager is not None:
            manager.start()
            _log.info("observer host: watching framework artifacts")
        else:
            _log.info("observer host: no framework watchers configured; idling")
    except Exception:
        _log.warning("observer host: watcher start failed; idling with no watchers")
        # start() may have started SOME observers before raising — stop them now so
        # we don't leak already-started watcher threads (the `finally` below can't,
        # since we drop the reference). manager.stop() is idempotent + never raises
        # on a partial start.
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                _log.exception("observer host: manager.stop() failed after start error")
            manager = None
    try:
        stop.wait()
    finally:
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                _log.exception("observer host: manager.stop() failed during shutdown")


# -- POSIX double-fork daemonize (unchanged: the flock-liveness core) ------

def daemonize(pid_path: Path, log_path: Path) -> None:
    """Self-daemonize via POSIX double-fork; hold an exclusive pidfile flock for
    the process lifetime (single-instance + the liveness signal supervisor
    probes). Unchanged from the pre-demotion server — see git history for the
    Stevens APUE §13.3 rationale of each step."""
    assert threading.active_count() == 1, (
        f"daemonize() called with {threading.active_count()} live threads; "
        "POSIX fork in a multi-threaded process is undefined behavior. "
        "Must run before any thread is spawned."
    )
    if os.fork() != 0:
        os._exit(0)
    os.setsid()
    os.umask(0)
    if os.fork() != 0:
        os._exit(0)
    os.chdir("/")
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    logfd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(devnull, 0)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    os.close(devnull)
    os.close(logfd)
    pid_fd = os.open(str(pid_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)  # another host won the race — single-instance
    os.ftruncate(pid_fd, 0)
    os.write(pid_fd, f"{os.getpid()}\n".encode())
    # KEEP pid_fd open for life of process: the flock auto-releases on death.


def main() -> int:
    """`super-harness-daemon` entry-point (observer host).

    Exit codes: 0 clean SIGTERM · 1 crash / flock loser · 3 no .harness/.
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
        return 3

    pid_path = harness_dir / "daemon.pid"
    log_path = harness_dir / "daemon.log"

    daemonize(pid_path, log_path)  # does not return in parent/first-child

    _configure_logging(log_path)
    log = logging.getLogger("super_harness.daemon")
    log.info(
        "super-harness observer host starting",
        extra={"workspace": str(workspace), "pid": os.getpid()},
    )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    try:
        run_observer_host(workspace, stop)
    except Exception:
        log.exception("observer host crashed")
        return 1

    log.info("super-harness observer host stopped cleanly")
    return 0
