"""F4 regression: EventWriter validate→append must be atomic across writers.

Before the flock fix, `EventWriter.emit` read-validated the stream OUTSIDE the
critical section and only the append was under the per-instance `threading.Lock`.
Two writers racing to emit the SAME single-fire transition both validate against
the same (stale) stream, both pass, and both append — landing a second event
that is illegal on replay. The `lifecycle-ordering` verification baseline is
must_pass and treats that as tamper, so a benign race permanently dirties a
change (append-only, no repair path).

Coverage is layered:
- A deterministic lock-scope test proves the flock is actually held across
  validate AND append, with no sleeps/threads (a probe inside the patched
  validate asserts a non-blocking re-acquire fails) — the timing-independent
  proof of the invariant.
- Two race tests reproduce the end-to-end bug (one thread, one cross-process) by
  widening the post-validate/pre-append window (a sleep injected into
  `validate_preconditions`) and starting the racers together at a barrier.

The race scenario SEEDS `intent_declared` (the only event legal from an empty
stream; it self-loops so it cannot itself be the raced event — see
core/transitions.py), then races two `plan_ready`:
`(INTENT_DECLARED, plan_ready) -> AWAITING_PLAN_REVIEW` is legal, but a second
`plan_ready` from `AWAITING_PLAN_REVIEW` is INVALID (absent from the transition
table). So the correct serialized outcome is exactly one appended `plan_ready`
plus one `EmitPreconditionError`; two appends is the bug.

The cross-process test uses `subprocess.Popen` with a generated worker script (the
same idiom as `test_writer.py::test_writer_multi_process_append_no_loss`) rather
than `multiprocessing.spawn`: it needs no importable-by-qualname worker (so no new
package `__init__.py`), and matches the repo's established real-process test style.
"""
import fcntl
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.events import Actor, Event
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter

# Window (seconds) injected after the real validation returns, to make the
# post-validate/pre-append race deterministic regardless of scheduling skew.
_WIDEN = 0.15


def _event(change_id: str, event_type: str) -> Event:
    return Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="race-test"),
        framework="plain",
        payload={"description": "x"},
    )


def _seed_intent_declared(events_file: Path, change_id: str) -> None:
    """Serially write the seed event that reaches INTENT_DECLARED."""
    EventWriter(events_file).emit(_event(change_id, "intent_declared"))


def _lines_for(events_file: Path, change_id: str) -> list[dict]:
    return [
        obj
        for line in events_file.read_text().splitlines()
        if line.strip()
        for obj in (json.loads(line),)
        if obj["change_id"] == change_id
    ]


def test_emit_holds_events_lock_across_validate_and_append(tmp_path, monkeypatch):
    """Deterministic (timing-independent) proof that emit holds the
    `.events.lock` flock across the WHOLE validate→append critical section.

    The race tests above reproduce the end-to-end bug but depend on a widened
    window; this one proves the invariant directly with no sleeps or threads:
    a probe that runs INSIDE `validate_preconditions` (already under emit's lock)
    tries a non-blocking `LOCK_EX` on the same sentinel via a fresh fd — flock
    conflicts across open-file-descriptions even within one process, so it MUST
    fail while emit holds the lock. A second probe after the append confirms the
    lock has been released. If emit ever validated or appended outside the flock,
    a probe would succeed and this fails — no scheduling luck involved.
    """
    events_file = tmp_path / ".harness" / "events.jsonl"
    change_id = "lock-scope"
    _seed_intent_declared(events_file, change_id)
    sentinel = events_file.parent / ".events.lock"

    def _lock_is_held() -> bool:
        with open(sentinel) as probe:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True

    import super_harness.core.writer as writer_mod

    real_validate = writer_mod.validate_preconditions
    observed: dict[str, bool] = {}

    def probing_validate(path, event):
        observed["held_during_validate"] = _lock_is_held()
        real_validate(path, event)

    monkeypatch.setattr(writer_mod, "validate_preconditions", probing_validate)

    EventWriter(events_file).emit(_event(change_id, "plan_ready"))

    assert observed.get("held_during_validate") is True, observed
    # lock released once emit returns (no lingering hold)
    assert _lock_is_held() is False


def test_thread_race_single_slot_stays_ordered(tmp_path, monkeypatch):
    """Two threads, two SEPARATE EventWriter instances (so flock — not the
    per-instance threading.Lock — is the layer under test) race one plan_ready.

    Pre-fix: both validate the seeded INTENT_DECLARED stream outside any shared
    lock, both append -> 0 rejections + an ordering violation (RED).
    Post-fix: the flock loser validates the appended AWAITING_PLAN_REVIEW stream
    and raises EmitPreconditionError (GREEN).
    """
    events_file = tmp_path / ".harness" / "events.jsonl"
    change_id = "race-thread"
    _seed_intent_declared(events_file, change_id)

    import super_harness.core.writer as writer_mod

    real_validate = writer_mod.validate_preconditions

    def slow_validate(path, event):
        real_validate(path, event)
        time.sleep(_WIDEN)  # widen the post-validate/pre-append window

    monkeypatch.setattr(writer_mod, "validate_preconditions", slow_validate)

    barrier = threading.Barrier(2)
    results: list[str] = []
    results_lock = threading.Lock()

    def worker() -> None:
        writer = EventWriter(events_file)  # OWN instance -> own threading.Lock
        barrier.wait()
        try:
            writer.emit(_event(change_id, "plan_ready"))
            outcome = "ok"
        except EmitPreconditionError:
            outcome = "rejected"
        with results_lock:
            results.append(outcome)

    # daemon=True so a lock regression that wedges a thread cannot hang the
    # interpreter at exit; we also assert liveness below rather than trusting join.
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "a worker thread is stuck (lock regression?)"

    assert results.count("ok") == 1, results
    assert results.count("rejected") == 1, results
    assert find_ordering_violations(events_file, change_id) == []
    # seed intent_declared + exactly one raced plan_ready
    assert len(_lines_for(events_file, change_id)) == 2


_WORKER_SRC = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path

    events_file, change_id, ready_dir, wid = sys.argv[1:5]
    ready_dir = Path(ready_dir)

    import super_harness.core.writer as writer_mod
    from super_harness.core.events import Actor, Event
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EmitPreconditionError, EventWriter

    _real = writer_mod.validate_preconditions
    def _slow(path, event):
        _real(path, event)
        time.sleep({widen})           # widen post-validate/pre-append window
    writer_mod.validate_preconditions = _slow

    # Filesystem spin-barrier: signal ready, wait until BOTH workers are ready
    # before calling emit (removes process-startup skew). Combined with the
    # widened validate window below, both racers reach validate close together;
    # pre-fix that lets both validate the stale stream and append, post-fix the
    # flock serializes them so the loser validates the advanced state and raises.
    (ready_dir / ("ready-" + wid)).touch()
    deadline = time.time() + 30
    while len(list(ready_dir.glob("ready-*"))) < 2:
        if time.time() > deadline:
            sys.exit(9)               # barrier timeout -> test fails loudly
        time.sleep(0.005)

    ev = Event(
        event_id=new_event_id(), type="plan_ready", change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="race-worker-" + wid),
        framework="plain", payload={{"description": "x"}},
    )
    try:
        EventWriter(events_file).emit(ev)
        sys.exit(0)                   # appended
    except EmitPreconditionError:
        sys.exit(3)                   # correctly rejected (serialized loser)
    """
).format(widen=_WIDEN)


def test_process_race_single_slot_stays_ordered(tmp_path):
    """Real cross-process corroborator (subprocess.Popen, repo idiom).

    Two processes race one plan_ready on a seeded INTENT_DECLARED stream,
    synchronized by a filesystem barrier and the widened validate window.
    Pre-fix: both append -> ordering violation + two exit-0 (RED).
    Post-fix: flock serializes -> one exit-0, one exit-3, clean stream (GREEN).
    """
    events_file = tmp_path / ".harness" / "events.jsonl"
    change_id = "race-proc"
    _seed_intent_declared(events_file, change_id)

    worker_script = tmp_path / "race_worker.py"
    worker_script.write_text(_WORKER_SRC)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()

    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(events_file), change_id,
             str(ready_dir), str(wid)],
            env={**os.environ},
            stderr=subprocess.PIPE,
        )
        for wid in range(2)
    ]
    codes = []
    for p in procs:
        p.wait(timeout=60)
        if p.returncode == 9:
            err = p.stderr.read().decode() if p.stderr else ""
            raise AssertionError(f"worker hit the barrier timeout: {err}")
        if p.stderr:
            err = p.stderr.read().decode()
            if err.strip():
                raise AssertionError(f"worker stderr: {err}")
        codes.append(p.returncode)

    assert sorted(codes) == [0, 3], f"expected one append (0) + one reject (3), got {codes}"
    assert find_ordering_violations(events_file, change_id) == []
    # seed intent_declared + exactly one raced plan_ready
    assert len(_lines_for(events_file, change_id)) == 2
