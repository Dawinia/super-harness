"""Authoring-time conformance verdict (design 2026-07-01, Rev 2).

Agent-agnostic: run the ratified, authoring-opted-in tier-1 decision checks once and
return a structured Verdict. Reused by the Stop-hook path. No agent knowledge, no
prose, no daemon. This is deliberately NOT a `Sensor` (no dispatcher / event
emission) — it is a synchronous verdict producer.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from super_harness.core.check_runner import CheckRun, has_runnable_check, run_one_check
from super_harness.core.decisions import Decision, compute_body_hash, load_decisions

# Wall-clock budget for `run_authoring_check`, measured from its entry. Derivation:
# the Claude Code / Codex Stop hook kills the whole hook process at 10s
# (`_STOP_TIMEOUT`), so budget = 10 - p95(Python cold-start + import, ~0.3-0.8s on a
# cold/loaded machine; `t0` is taken inside this module so it does not capture that)
# - result render. 8.0 leaves ~1s of honest headroom.
AUTHORING_TOTAL_BUDGET = 8.0

# Each check's subprocess timeout = remaining_budget - _CLEANUP_MARGIN, sampled INSIDE
# the worker, so the subprocess self-kills (and its process-group cleanup runs) ~margin
# before the main join deadline: no orphaned subprocess when the hook returns.
_CLEANUP_MARGIN = 0.5
# Never spawn a check that cannot get at least this long to actually run.
_MIN_SLICE = 1.0

# Exit codes that mean "the check could not run" (NOT a violation): timeout/spawn
# failure (runner sentinel -1) and tool-not-found / not-executable under shell=True
# (126/127, e.g. `lint-imports` absent). Never emit a false "you violated".
_UNAVAILABLE_EXIT_CODES = frozenset({-1, 126, 127})


@dataclass(frozen=True)
class Violation:
    decision_id: str
    detail: str  # the check's own output (CheckRun.detail)
    decision_doc_path: str


@dataclass(frozen=True)
class Verdict:
    violations: list[Violation]
    # Decision ids that could not be evaluated this turn (timeout / budget exhausted /
    # tool-not-found / crash). Test/CLI observability only — the Stop-hook advisory
    # renders `violations` alone, so `unavailable` stays silent to the model.
    unavailable: list[str] = field(default_factory=list)


def _integrity_ok(d: Decision) -> bool:
    """True if the decision's body still matches its ratified hash. Mirror the CI floor
    (cli/decision.py): a tamper-detected decision must NOT have its arbitrary shell
    check run automatically in the interactive loop (a trust control)."""
    if not d.ratified_text_hash:
        return True
    return compute_body_hash(d.body) == d.ratified_text_hash


def _per_check(deadline: float, now: float, *,
               min_slice: float = _MIN_SLICE, margin: float = _CLEANUP_MARGIN) -> float | None:
    """Per-check subprocess timeout for a check sampled at `now`, or None if too little
    budget remains to run it. PURE (no clock / no threads) → exhaustively testable.

    Sampled INSIDE the worker (after it has been scheduled, right before spawning) so
    the subprocess self-kill time ~ now + (deadline - now - margin) = deadline - margin,
    independent of thread-scheduling delay. Computing this in the main thread before
    `Thread.start` would fold the (unbounded) scheduling delay into the timeout.
    """
    remaining = deadline - now
    if remaining <= min_slice + margin:
        return None
    return remaining - margin


def _run_checks_parallel(
    workspace_root: Path,
    decisions: list[Decision],
    *,
    deadline: float,
    run_one: Callable[..., CheckRun] = run_one_check,
    clock: Callable[[], float] = time.monotonic,
) -> list[tuple[Decision, CheckRun]]:
    """Run each runnable decision's check on its own daemon thread, collecting results
    under `deadline`. Each worker samples the clock itself and derives its timeout via
    `_per_check`, so every subprocess self-kills ~margin before `deadline` regardless
    of scheduling delay. Both the spawn loop and the join loop are deadline-bounded, so
    the CHECK-EXECUTION phase returns by ~`deadline` for any number of armed checks;
    never-started / too-late / unfinished / crashed checks become the -1 'unavailable'
    sentinel. Daemon threads so a stuck subprocess never blocks the return or
    interpreter exit. (The surrounding O(N) bookkeeping — sorting the runnable set here,
    plus `load_decisions` in the caller — is cheap, non-subprocess work and is not
    itself deadline-guarded; the armed/decision set is expected to be small.)
    """
    runnable = sorted((d for d in decisions if has_runnable_check(d)), key=lambda d: d.id)
    results: dict[str, CheckRun] = {}
    lock = threading.Lock()

    def worker(d: Decision) -> None:
        per_check = _per_check(deadline, clock())  # sample in-thread, right before spawning
        if per_check is None:  # scheduled too late → leave it unavailable (default below)
            return
        try:
            run = run_one(d.check, cwd=workspace_root, timeout=per_check)
        except Exception as exc:  # fail-open: a crashing check is unavailable, never a hook crash
            run = CheckRun(False, -1, f"check crashed: {exc}")
        with lock:
            results[d.id] = run

    threads: list[tuple[threading.Thread, Decision]] = []
    for d in runnable:
        if clock() >= deadline:  # spawn guard: bound the spawn loop for large N
            break
        thread = threading.Thread(target=worker, args=(d,), daemon=True)
        try:
            thread.start()
        except RuntimeError:  # thread exhaustion → stop spawning; the rest stay unavailable
            break
        threads.append((thread, d))
    for thread, _ in threads:
        thread.join(timeout=max(0.0, deadline - clock()))
    with lock:
        return [
            (d, results.get(d.id, CheckRun(False, -1, "authoring budget exhausted")))
            for d in runnable
        ]


def _to_verdict(results: list[tuple[Decision, CheckRun]]) -> Verdict:
    """Split (decision, run) pairs into violations and unavailable, dropping satisfied
    checks. An `unavailable` result (could-not-run exit code) is NOT 'you violated X'."""
    violations: list[Violation] = []
    unavailable: list[str] = []
    for d, run in results:
        if run.satisfied:
            continue
        if run.exit_code in _UNAVAILABLE_EXIT_CODES:
            unavailable.append(d.id)
        else:
            violations.append(
                Violation(
                    decision_id=d.id,
                    detail=run.detail,
                    decision_doc_path=f"docs/decisions/{d.id}.md",
                )
            )
    violations.sort(key=lambda v: v.decision_id)
    unavailable.sort()
    return Verdict(violations=violations, unavailable=unavailable)


def run_authoring_check(
    workspace_root: Path, *, clock: Callable[[], float] = time.monotonic
) -> Verdict:
    """Run the ratified, `authoring_time`, integrity-clean tier-1 checks concurrently
    once, finishing within `AUTHORING_TOTAL_BUDGET` of entry.

    Only decisions that opted into the interactive loop (`authoring_time: true`) AND
    whose body still matches their ratified hash run — the safety control. Never raises
    (fail-open): on an unexpected orchestration error it returns an all-`unavailable`
    verdict and emits a stderr diagnostic (observable in logs, not model-facing).
    """
    try:
        t0 = clock()  # inside the try so an injected clock that raises still fails open
        decisions, _errors = load_decisions(workspace_root)
        opted = [d for d in decisions if d.authoring_time and _integrity_ok(d)]
        if not opted:
            return Verdict(violations=[])
        results = _run_checks_parallel(
            workspace_root, opted, deadline=t0 + AUTHORING_TOTAL_BUDGET, clock=clock
        )
        return _to_verdict(results)
    except Exception as exc:  # fail-open: never crash the agent's Stop; surface a diagnostic
        print(f"super-harness authoring check failed open: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        try:
            decisions, _errors = load_decisions(workspace_root)
            ids = sorted(
                d.id for d in decisions
                if d.authoring_time and _integrity_ok(d) and has_runnable_check(d)
            )
        except Exception:
            ids = []
        return Verdict(violations=[], unavailable=ids)
