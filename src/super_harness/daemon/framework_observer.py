"""Daemon-hosted framework file-watchers (delivers OI-7).

The long-running daemon hosts watchdog `Observer` threads, one per existing
`watch_path` of each active `FrameworkAdapter`. When a framework artifact under
a watched path changes (e.g. an OpenSpec `proposal.md` lands), the observer
re-runs the adapter's read-only `observe()` scan and emits any UNSEEN lifecycle
events — turning "scan once via `adapter scan-once`" into automatic, continuous
observation while the daemon is up.

Design contract (mirrors `cli/adapter.py::adapter_scan_once`'s emit idiom):
- `_observe_and_emit(adapter, workspace_root)` is the shared scan-and-emit core:
  one `EventWriter`, emit each yielded event in YIELDED order (observe yields
  `intent_declared` before `plan_ready` so the plan_ready emit precondition —
  a preceding intent_declared — holds), then `refresh_state_after_emit` so the
  decision plane's next `state.yaml` read observes the update (the gate decides
  in-process — no shared memory needed; the two planes meet only through the
  files on disk).
- CRITICAL difference from `scan-once`: `scan-once` is a one-shot CLI that EXITS
  on an `EmitPreconditionError` (e.g. a malformed change with `tasks.md` but no
  `proposal.md` → a lone `plan_ready`). The daemon is LONG-RUNNING — a single
  malformed change must NOT take the watcher thread (and thereby the live
  framework-observation feature) down. So per-event emit precondition errors are
  LOGGED (JSON-line, via the daemon's `super_harness.daemon` logger) and
  SWALLOWED; the scan continues to the next event.

Idempotency: `observe()` dedups against events.jsonl (`(change_id, type)` seen
set), so coalesced / duplicate FSEvents are safe to re-process — re-running the
scan on a spurious event simply yields nothing new. This is why debounce is NOT
implemented (the dedup makes it unnecessary; see Task 10.6).

Lifecycle: `FrameworkObserverManager.start()` spawns one `Observer` per existing
watch path; `stop()` stops + joins them all with a BOUNDED timeout so a wedged
Observer cannot hang daemon shutdown past its ~2s budget. Both are idempotent
(`shutdown()` and `serve_forever()`'s `finally` may both call `stop()`).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from super_harness.adapters import FrameworkAdapter
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.writer import EmitPreconditionError, EventWriter

__all__ = ["FrameworkObserverManager", "build_manager_failsafe"]

_log = logging.getLogger(__name__)

# Bounded join per Observer on shutdown. The daemon's graceful-shutdown budget
# is ~2s (AC-8 SIGTERM → shutdown within 2s); a wedged Observer must not blow
# past it, so each join is capped. With several Observers we still finish well
# inside the budget (joins run sequentially but each watch path's worker exits
# near-instantly once `stop()` signals it).
_OBSERVER_JOIN_TIMEOUT_S = 1.0


def _observe_and_emit(adapter: FrameworkAdapter, workspace_root: Path) -> None:
    """Run ONE read-only `observe()` pass for `adapter`, emitting unseen events.

    Shared scan-and-emit core for every watcher thread. Mirrors
    `cli/adapter.py::adapter_scan_once`'s `EventWriter` + `refresh_state_after_emit`
    idiom, with ONE behavioural difference (see module docstring): a per-event
    `EmitPreconditionError` is LOGGED and swallowed (the daemon is long-running)
    instead of exiting. Emits in YIELDED order so `plan_ready`'s precondition
    (a preceding `intent_declared`) holds.

    Never raises on a malformed change: the precondition error is contained so a
    single bad change cannot crash the watcher thread.
    """
    writer = EventWriter(events_path(workspace_root))
    for ev in adapter.observe(workspace_root):
        try:
            writer.emit(ev)
        except EmitPreconditionError as exc:
            # Long-running daemon: a malformed change (e.g. a lone plan_ready) must
            # NOT take the watcher thread down. Log (JSON-line) + continue scanning.
            _log.warning(
                "framework observe: emit precondition failed; skipping event",
                extra={
                    "adapter": adapter.name,
                    "change_id": ev.change_id,
                    "event_type": ev.type,
                    "log_reason": "emit_precondition_failed",
                    "detail": str(exc),
                },
            )
            continue
        # Keep state.yaml current after every emit so the decision plane's next
        # in-process state.yaml read serves an up-to-date gate decision (mirrors
        # scan-once).
        refresh_state_after_emit(workspace_root)


class _ObserveHandler(FileSystemEventHandler):
    """Re-run `_observe_and_emit` on ANY filesystem event under a watch path.

    We do not discriminate by event type / path: `observe()` is a cheap,
    idempotent full re-scan (dedup against events.jsonl), so reacting to every
    event — created / modified / moved / deleted — is both correct and simplest.
    A per-handler `threading.Lock` serializes concurrent dispatches from the same
    Observer's worker so two near-simultaneous FSEvents don't race the
    scan-and-emit (EventWriter + refresh are themselves locked, but serializing
    here avoids redundant duplicate scans interleaving).
    """

    def __init__(self, adapter: FrameworkAdapter, workspace_root: Path) -> None:
        self._adapter = adapter
        self._workspace_root = workspace_root
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        with self._lock:
            try:
                _observe_and_emit(self._adapter, self._workspace_root)
            except Exception:
                # Defense in depth: _observe_and_emit already contains
                # EmitPreconditionError; any OTHER unexpected error (e.g. a
                # transient OSError reading events.jsonl) must NOT propagate into
                # the watchdog worker thread (which would silently die). Log and
                # keep the watcher alive for the next event.
                _log.exception(
                    "framework observe: unexpected error in watch callback",
                    extra={"adapter": self._adapter.name},
                )


class FrameworkObserverManager:
    """Own the watchdog Observers for a workspace's active framework adapters.

    Construct with the (post-fork, absolute) workspace root + the active
    framework adapters (typically `activate_with_fallback(...)` output). One
    `Observer` is started per EXISTING `watch_path` of each adapter; a watch path
    that does not exist on disk is skipped (a brand-new workspace may not have
    `openspec/changes/` yet — but the plain fallback has no watch paths at all,
    so the common no-framework case is a clean no-op).
    """

    def __init__(
        self, workspace_root: Path, adapters: list[FrameworkAdapter]
    ) -> None:
        self._workspace_root = workspace_root
        self._adapters = adapters
        self._observers: list[BaseObserver] = []
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start one Observer per existing watch path. Idempotent no-op if started.

        No adapters / no watch paths / no existing watch path → starts nothing
        (clean no-op): `_observers` stays empty and `stop()` joins nothing.
        """
        with self._lock:
            if self._started:
                return
            self._started = True
            for adapter in self._adapters:
                handler = _ObserveHandler(adapter, self._workspace_root)
                for watch_path in adapter.watch_paths(self._workspace_root):
                    if not watch_path.exists():
                        # Brand-new workspace may lack openspec/changes/ — skip
                        # (recursive watch on a missing dir would error). The dir
                        # appears via `init`/first artifact; a daemon restart (or
                        # parent-dir watch, deferred) picks it up.
                        _log.info(
                            "framework observe: watch path absent; skipping",
                            extra={
                                "adapter": adapter.name,
                                "watch_path": str(watch_path),
                            },
                        )
                        continue
                    observer: BaseObserver = Observer()
                    observer.schedule(handler, str(watch_path), recursive=True)
                    observer.start()
                    self._observers.append(observer)
                    _log.info(
                        "framework observe: watching",
                        extra={
                            "adapter": adapter.name,
                            "watch_path": str(watch_path),
                        },
                    )

    def stop(self) -> None:
        """Stop + join all Observers (bounded). Idempotent.

        Safe to call from the observer host's `run_observer_host` shutdown path
        (SIGTERM → stop event set → manager.stop()). Each join is capped at
        `_OBSERVER_JOIN_TIMEOUT_S` so a wedged Observer cannot hang shutdown past
        the host's ~2s budget; a still-alive Observer after the bounded join is
        logged (it's a daemon thread — process exit reaps it).
        """
        with self._lock:
            observers, self._observers = self._observers, []
            self._started = False
        for observer in observers:
            try:
                observer.stop()
            except Exception:
                _log.exception("framework observe: observer.stop() failed")
            try:
                observer.join(timeout=_OBSERVER_JOIN_TIMEOUT_S)
            except Exception:
                _log.exception("framework observe: observer.join() failed")
            if observer.is_alive():
                _log.warning(
                    "framework observe: observer did not join within %.1fs",
                    _OBSERVER_JOIN_TIMEOUT_S,
                )


def build_manager_failsafe(workspace_root: Path) -> FrameworkObserverManager | None:
    """Load adapters + activate frameworks + build a started-ready manager.

    FAIL-SAFE (Axiom 3 — the watcher must NEVER take the gate down): the ENTIRE
    load + activate path is wrapped in `try/except Exception` (the broadest catch
    — Axiom 3: the watcher must NEVER take the gate down, so even an unexpected
    error is absorbed). `load_adapters` (v0.1 builtin-only) raises yaml.YAMLError
    / ValueError / OSError for a corrupt or non-builtin `adapters.yaml`; were that
    to propagate to `serve_forever`, `main()`'s bare
    `except Exception` would log "daemon main loop crashed" + return 1, KILLING
    the gate hot-path. Mirrors the advisory-skip contract `cli/sync.py` uses for
    a corrupt adapters.yaml: on ANY failure here we LOG (warning, JSON-line) +
    return None so the daemon keeps serving the accept loop with NO watchers.

    Returns:
        A constructed (NOT yet started) `FrameworkObserverManager`, or None if
        adapter setup failed (caller then serves with no watchers).
    """
    # Imported lazily so a corrupt registry import can't break module import.
    from super_harness.adapters.registry import activate_with_fallback, load_adapters
    from super_harness.core.paths import adapters_yaml_path

    try:
        frameworks, _agents = load_adapters(adapters_yaml_path(workspace_root))
        active = activate_with_fallback(frameworks, workspace_root)
    except Exception as exc:
        # Mirror cli/sync.py's corrupt-adapters.yaml advisory skip: do NOT
        # propagate — gate decisions must stay available even when framework
        # observation can't be set up.
        _log.warning(
            "framework observe: adapter setup failed; serving with no watchers",
            extra={"log_reason": "adapter_setup_failed", "detail": str(exc)},
        )
        return None
    return FrameworkObserverManager(workspace_root, active)
