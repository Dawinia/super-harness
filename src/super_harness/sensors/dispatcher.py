"""SensorDispatcher — parallel sensor execution + lifecycle event auto-emit.

Per sensor-gate-architecture §3.3 (concurrency model I-6) + §3.6 #1
(timeout / crash safety). The dispatcher is the runtime engine that connects
the Sensor base architecture (Tasks 3.1-3.3) to the actual event flow.

Flow per `on_event_emit(event)`:

1. Find all sensors whose `triggers_on_events` matches `event.type`.
2. Submit each to a `ThreadPoolExecutor` (parallel, up to `max_parallelism`).
3. Enforce `timeout_s` as a batch-level wall clock (default 300s) — any
   sensor not yet completed when the timeout fires gets a
   `sensor_timeout_exceeded` lifecycle event. Per-sensor budgets land in
   v0.2 with subprocess isolation (spec §3.6 #6).
4. Stamp `actor=(sensor, name@version)` on any events the sensor returned in
   `SensorResult.emit_events`, then emit them via the shared `EventWriter`.
5. Call `refresh_state_after_emit` once per successful emit batch (B-3 wiring).
6. On batch timeout → auto-emit `sensor_timeout_exceeded` for each
   not-yet-completed sensor.
7. On unhandled exception → auto-emit `sensor_crashed` (system event).

`on_activity(activity)` follows the same pipeline but matches against
`triggers_on_activities` and dispatches `Activity` objects (git hook / file
watcher / CLI invocation, per §3.3 activity-trigger contract).

Threading model:
- One `ThreadPoolExecutor` per `_run_all` invocation (bounded by
  `max_parallelism`, default 4). Sensors must be thread-safe — the same
  instance can be invoked concurrently if it matches multiple triggers
  rapidly, though v0.1's daemon serializes dispatcher entry points.
- `EventWriter.emit` is thread-safe (POSIX `O_APPEND` + internal lock).
- `refresh_state_after_emit` is called from `_handle` which is invoked
  serially from the `as_completed` loop, so there's no race within one
  dispatcher invocation.

Caller contract:
- `on_event_emit` / `on_activity` must be called from a single-writer context.
  v0.1's daemon is single-threaded; v0.2's multi-daemon design requires an
  external mutex on state.yaml.
- Sensor crashes / timeouts emit `sensor_crashed` / `sensor_timeout_exceeded`
  with `skip_validation=True` because these are system events that may
  legitimately precede `intent_declared` (e.g., a sensor fires on activity
  before any change exists).

API stability: **experimental** (v0.1). The dispatcher's constructor and
hook method shapes may shift in v0.2 when the multi-daemon model lands.
Plugin sensors execute arbitrary code in the daemon thread pool;
sandboxing is deferred to v0.2 (sensor-gate-architecture §3.6 #6).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Any

from super_harness.core.events import Actor, Event
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.sensors import (
    Activity,
    Sensor,
    SensorResult,
    WorkspaceContext,
)

__all__ = ["SensorDispatcher"]

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300
DEFAULT_PARALLELISM = 4


class SensorDispatcher:
    """Parallel sensor runner with batch timeout + crash auto-emit.

    Constructed once per daemon process (or per CLI invocation in v0.1
    pre-daemon) with a fixed list of `sensors`, a shared `EventWriter`, and
    a read-only `WorkspaceContext`. Trigger entrypoints:

    - `on_event_emit(event)`: called by the daemon after `EventWriter.emit`
      returns for any lifecycle / extension event. Fans out to every sensor
      whose `triggers_on_events` contains `event.type`.
    - `on_activity(activity)`: called by git hooks / file watchers / CLI
      activity emitters (per spec §3.3). Fans out to every sensor whose
      `triggers_on_activities` contains `activity.type`.

    Threading: each invocation spins up a bounded `ThreadPoolExecutor`
    (default 4 workers). Sensors run in parallel; extension events are
    appended to events.jsonl in **completion order** (per spec §3.3 I-6).

    - **Blocking on runaway sensors**: when the batch timeout fires, in-flight
      sensors cannot be killed (no thread-kill in CPython). `on_event_emit` /
      `on_activity` blocks until they return on their own. A genuinely
      non-terminating sensor will hang the dispatcher (and the daemon's event
      loop) until v0.2 subprocess isolation lands (spec §3.6 #6).
    - **GIL caveat**: real parallelism applies to I/O-bound sensors
      (subprocess, filesystem, network). CPU-bound work (heavy parsing,
      hashing, regex over large strings) is serialized by Python's GIL. Don't
      expect 4x throughput on CPU-bound batches.

    Failure modes — `skip_validation=True` is used for these system events
    because they may legitimately precede `intent_declared` (e.g., a sensor
    on an `activity` fires before any change exists), and we never want a
    transition violation to swallow a crash signal:

    - Batch `timeout_s` exceeded → emit `sensor_timeout_exceeded` for every
      sensor that had not completed by then. (Per-sensor budgets are a v0.2
      task; the batch timeout is the minimum viable v0.1 contract per
      spec §3.6 #1.)
    - Sensor raises any exception → emit `sensor_crashed` with `reason=str(e)`.

    After any successful sensor-emitted event the dispatcher calls
    `refresh_state_after_emit(workspace_root)` so state.yaml never lags the
    event stream (B-3 wiring).

    Return value — both `on_event_emit` and `on_activity` return the
    `list[SensorResult]` collected from every sensor that completed its
    `check()` WITHOUT raising. Crashed sensors (`except Exception` →
    `sensor_crashed`) and timed-out sensors (batch `FuturesTimeout` →
    `sensor_timeout_exceeded`) contribute NO result to the list — they only
    auto-emit their lifecycle event. Results are ordered by sensor COMPLETION
    (the `as_completed` order), and the auto-emit / actor-stamping / state
    refresh side effects are unchanged. This lets callers (e.g. Task 8.7's
    `verify` / `done` CLI driving the VerificationRunner sensor through the
    dispatcher per spec) read the sensors' verdicts directly.
    """

    def __init__(
        self,
        sensors: list[Sensor],
        *,
        writer: EventWriter,
        context: WorkspaceContext,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_parallelism: int = DEFAULT_PARALLELISM,
    ) -> None:
        self.sensors = sensors
        self.writer = writer
        self.context = context
        self.timeout_s = timeout_s
        self.max_parallelism = max_parallelism

    def on_event_emit(self, event: Event) -> list[SensorResult]:
        """Dispatch a lifecycle / extension event to matching sensors.

        Returns the `SensorResult`s from sensors that completed without
        raising (crashed / timed-out sensors are omitted; they only auto-emit
        their `sensor_crashed` / `sensor_timeout_exceeded` lifecycle event).
        """
        matching = [s for s in self.sensors if event.type in s.triggers_on_events]
        return self._run_all(matching, event)

    def on_activity(self, activity: Activity) -> list[SensorResult]:
        """Dispatch a non-event activity (git hook / CLI / watcher) to sensors.

        Returns the `SensorResult`s from sensors that completed without
        raising (crashed / timed-out sensors are omitted; they only auto-emit
        their `sensor_crashed` / `sensor_timeout_exceeded` lifecycle event).
        """
        matching = [
            s for s in self.sensors if activity.type in s.triggers_on_activities
        ]
        return self._run_all(matching, activity)

    # --- internals --------------------------------------------------------

    def _run_all(
        self, sensors: list[Sensor], trigger: Event | Activity
    ) -> list[SensorResult]:
        """Run `sensors` in parallel; return results from those that completed.

        The returned list holds one `SensorResult` per sensor whose `check()`
        returned without raising (in `as_completed` completion order). Sensors
        that crash (`except Exception`) or exceed the batch timeout
        (`FuturesTimeout`) contribute nothing to the list — they only auto-emit
        their `sensor_crashed` / `sensor_timeout_exceeded` lifecycle event. The
        emit / actor-stamp / state-refresh side effects live in `_handle` and
        are unchanged.
        """
        if not sensors:
            return []
        results: list[SensorResult] = []
        # Pending-future cancellation: `ThreadPoolExecutor.__exit__` calls
        # `shutdown(wait=True)` (no cancel_futures). Already-running sensors
        # cannot be killed (CPython has no thread-kill primitive). If the batch
        # timeout fires, we explicitly `future.cancel()` not-yet-started futures
        # inside the `except FuturesTimeout` branch below — running futures
        # still complete on their own time, and `__exit__` blocks until they do.
        # v0.2 subprocess isolation (spec §3.6 #6) is the proper fix.
        with ThreadPoolExecutor(max_workers=self.max_parallelism) as pool:
            futures = {pool.submit(self._safe_run, s, trigger): s for s in sensors}
            pending = set(futures)
            try:
                # `as_completed` honors spec §3.3 I-6: extension events are
                # appended in COMPLETION order, not submission order. The
                # batch-level timeout (`timeout_s`) bounds the slowest
                # sensor; per-sensor budgets land in v0.2 alongside
                # subprocess isolation.
                for future in as_completed(futures, timeout=self.timeout_s):
                    pending.discard(future)
                    sensor = futures[future]
                    try:
                        result = future.result()
                    # Narrow Exception catch is intentional: KeyboardInterrupt
                    # and SystemExit are BaseException-only subclasses that
                    # propagate past this handler so operators can kill the
                    # daemon (Ctrl-C, signal handlers, sys.exit).
                    except Exception as exc:
                        log.exception("sensor %s crashed", sensor.name)
                        self._emit_lifecycle(
                            "sensor_crashed", sensor, trigger, reason=str(exc)
                        )
                    else:
                        self._handle(result, sensor, trigger)
                        results.append(result)
            except FuturesTimeout:
                # Every future that did not complete within `timeout_s` gets
                # a `sensor_timeout_exceeded` lifecycle event. Cancel any
                # not-yet-started futures; running ones will be abandoned
                # when the pool context exits (see class docstring). These
                # sensors produce no `SensorResult` for the returned list.
                for future in pending:
                    future.cancel()
                    sensor = futures[future]
                    self._emit_lifecycle(
                        "sensor_timeout_exceeded", sensor, trigger
                    )
        return results

    def _safe_run(
        self, sensor: Sensor, trigger: Event | Activity
    ) -> SensorResult:
        # Sensor.check is the integration point with contributor / plugin
        # code; this wrapper exists so future v0.2 changes (subprocess
        # isolation, per-sensor budgets) attach in one place.
        return sensor.check(trigger, self.context)

    def _handle(
        self,
        result: SensorResult,
        sensor: Sensor,
        trigger: Event | Activity,
    ) -> None:
        emitted_any = False
        for ev in result.emit_events:
            stamped = Event(
                event_id=ev.event_id or new_event_id(),
                type=ev.type,
                change_id=ev.change_id,
                timestamp=ev.timestamp or _now(),
                actor=Actor(
                    type="sensor",
                    identifier=f"{sensor.name}@{sensor.version}",
                ),
                framework=ev.framework,
                framework_state=ev.framework_state,
                payload=ev.payload,
            )
            try:
                self.writer.emit(stamped)
                emitted_any = True
            except EmitPreconditionError as err:
                # Reject the sensor's bad emit but keep the dispatcher
                # alive — one misbehaving sensor must not poison the batch.
                log.warning(
                    "dispatcher rejected sensor emit %s: %s",
                    stamped.type,
                    err,
                )
        if emitted_any:
            # B-3 wiring: refresh state.yaml after sensor-emitted events.
            # Caller must serialize on_event_emit / on_activity calls (v0.1
            # daemon is single-threaded; v0.2 multi-daemon requires an
            # external mutex on state.yaml).
            refresh_state_after_emit(self.context.workspace_root)

    def _emit_lifecycle(
        self,
        etype: str,
        sensor: Sensor,
        trigger: Event | Activity,
        **payload: Any,
    ) -> None:
        """Emit a `sensor_crashed` / `sensor_timeout_exceeded` system event.

        Uses `skip_validation=True` because these system events may
        legitimately precede `intent_declared` (a sensor on an `activity`
        can fire before any change exists). A transition violation must
        never swallow a crash signal.
        """
        change_id = getattr(trigger, "change_id", None) or ""
        try:
            self.writer.emit(
                Event(
                    event_id=new_event_id(),
                    type=etype,
                    change_id=change_id,
                    timestamp=_now(),
                    actor=Actor(
                        type="sensor",
                        identifier=f"{sensor.name}@{sensor.version}",
                    ),
                    framework="plain",
                    payload={"sensor": sensor.name, **payload},
                ),
                skip_validation=True,
            )
        except Exception:
            log.exception("failed to emit %s", etype)


def _now() -> str:
    """ISO-8601 UTC timestamp matching lifecycle-event-model §2 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
