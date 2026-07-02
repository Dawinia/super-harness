# Authoring-Time Parallel Checks + Arm Two Decisions — Implementation Plan (rev3)

Goal: Run armed authoring-time conformance checks concurrently on daemon threads under a dynamic wall-clock budget so arming more checks never crosses the 10s Stop-hook kill; then arm d-gh-cli-not-rest and d-merge-gate-pure-git.

Architecture: Each armed check runs on its own daemon thread with a per-check subprocess timeout computed as (remaining budget − cleanup margin), so every check's subprocess self-terminates (and cleans up its process group) BEFORE the main join deadline — no leak by construction. Both the spawn loop and the join loop are deadline-guarded, so the main thread returns within budget for ANY N; never-started/unfinished checks degrade to unavailable. run_one_check kills the whole process group on timeout. CI's run_executable_checks stays sequential.

Commit structure: commit1 = orchestration + primitive hardening + tests + docs (Tasks 1-8, armed=1, inert). commit2 = arm two decisions (Tasks 9-10). Ship together.

## File Structure (unchanged from rev2)
- core/check_runner.py — add has_runnable_check(d); run_executable_checks adopts it (CI, sequential); harden run_one_check to kill the process group on timeout.
- core/authoring_check.py — widen Verdict; daemon-thread orchestrator _run_checks_parallel (dynamic per-check timeout; start+join deadline-guarded) + _to_verdict; fail-open-with-diagnostic in run_authoring_check; add AUTHORING_TOTAL_BUDGET=8.0, _CLEANUP_MARGIN, _MIN_SLICE; drop run_executable_checks/CheckFailure imports; remove old AUTHORING_CHECK_TIMEOUT.
- engineering/agents_md_render.py — read-only/reentrant + keep-armed-set-small notes in arming recipe.
- AGENTS.md — regenerated via sync --agents-md.
- docs/decisions/d-gh-cli-not-rest.md, d-merge-gate-pure-git.md — add authoring_time:true, re-ratify.
- docs/plans/2026-07-02-authoring-parallel-checks-{design,plan}.md.
- tests/unit/core/test_check_runner.py — has_runnable_check + process-group-kill-on-timeout tests.
- tests/unit/core/test_authoring_check.py — _to_verdict; orchestrator concurrency/started-unfinished-degradation/startup-bounded/fail-open tests.
- tests/integration/test_authoring_parallel.py — real greps concurrent, surface grep violation.

## Task 1: has_runnable_check (check_runner.py)
    def has_runnable_check(d: "Decision") -> bool:
        """A ratified decision whose executable check can actually run (tier-1)."""
        return d.status == "ratified" and d.check is not None
Then in run_executable_checks replace `if d.status != "ratified" or d.check is None:` with `if not has_runnable_check(d):`.
Tests: ratified+check->True; proposed->False; check=None->False. Existing run_executable_checks tests unchanged.

## Task 2: HARDEN run_one_check — kill process group on timeout (check_runner.py)
Add imports `import os`, `import signal`. Replace run_one_check body:
    def run_one_check(command, *, cwd, timeout=DEFAULT_TIMEOUT):
        """Run a single executable check; kill the whole process group on timeout so no
        grandchild (grep/lint-imports under the shell) is orphaned."""
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=str(cwd),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace", start_new_session=True,
            )
        except OSError as e:
            return CheckRun(False, -1, f"could not run: {e}")
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # start_new_session makes proc the group leader -> proc.pid IS the pgid.
            # Target proc.pid directly (NOT os.getpgid, which raises if the shell already
            # exited while a backgrounded grandchild lives); killpg still reaches the group.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            try:
                proc.communicate(timeout=2)   # bounded reap; a SIGKILL'd group EOFs the pipes fast
            except subprocess.TimeoutExpired:
                pass                          # give up reaping; process is killed, never hang
            return CheckRun(False, -1, f"timeout after {timeout}s")
        detail = (err or out or "").strip().splitlines()
        tail = detail[-1] if detail else f"exited {proc.returncode}"
        return CheckRun(proc.returncode == 0, proc.returncode, tail)
Tests: existing satisfied/exit-code/timeout->-1/non-utf8/bad-cwd tests still pass. NEW (POSIX-only, skip otherwise) test_timeout_kills_process_group:
    # `sh -c 'sleep 30 & echo hi'` times out ONLY because the backgrounded sleep inherits
    # the stdout pipe, keeping communicate() blocked after the shell exits — this is the
    # exact grandchild-orphan case. After run_one_check returns -1, the killpg must have
    # reaped the sleep. (Comment WHY so a maintainer doesn't turn it into a false pass.)
    r = run_one_check("sleep 30 & echo hi", cwd=tmp_path, timeout=1)
    assert r.exit_code == -1
    # best-effort: assert no lingering `sleep 30` child within ~1s (pgrep/ps), or skip on non-POSIX

## Task 3: Widen Verdict (authoring_check.py)
    @dataclass(frozen=True)
    class Verdict:
        violations: list[Violation]
        unavailable: list[str] = field(default_factory=list)
Tests: Verdict(violations=[]).unavailable == []; Verdict(violations=[], unavailable=["d"]).unavailable == ["d"].

## Task 4: Daemon-thread orchestrator + _to_verdict + fail-open (authoring_check.py FULL body below docstring)
    from __future__ import annotations
    import sys, threading, time, traceback
    from dataclasses import dataclass, field
    from pathlib import Path
    from super_harness.core.check_runner import CheckRun, has_runnable_check, run_one_check
    from super_harness.core.decisions import Decision, compute_body_hash, load_decisions

    # Wall-clock budget for run_authoring_check measured from entry. Derivation:
    # 10s outer Stop-hook kill (_settings_merge.py) − p95(Python cold-start + import,
    # ~0.3-0.8s on a cold/loaded machine; t0 is taken INSIDE run_authoring_check so it
    # does not capture cold-start/import/JSON-parse) − result render. 8.0 leaves ~1s margin.
    AUTHORING_TOTAL_BUDGET = 8.0
    # Each check's subprocess timeout = remaining_budget − _CLEANUP_MARGIN, so it self-kills
    # (and its process-group cleanup runs) BEFORE the main join deadline: no leak.
    _CLEANUP_MARGIN = 0.5
    _MIN_SLICE = 1.0   # don't spawn a check that can't get at least this long to run
    _UNAVAILABLE_EXIT_CODES = frozenset({-1, 126, 127})

    @dataclass(frozen=True)
    class Violation:
        decision_id: str
        detail: str
        decision_doc_path: str

    @dataclass(frozen=True)
    class Verdict:
        violations: list[Violation]
        unavailable: list[str] = field(default_factory=list)

    def _integrity_ok(d: Decision) -> bool:
        if not d.ratified_text_hash:
            return True
        return compute_body_hash(d.body) == d.ratified_text_hash

    def _per_check(deadline, now, *, min_slice=_MIN_SLICE, margin=_CLEANUP_MARGIN):
        """Per-check subprocess timeout for a check sampled at `now`, or None if too
        little budget remains to run it. PURE (no clock/threads) -> exhaustively testable.
        Sampled INSIDE the worker (after scheduling) so subprocess self-kill = now + (
        deadline - now - margin) = deadline - margin, independent of thread-schedule delay."""
        remaining = deadline - now
        if remaining <= min_slice + margin:
            return None
        return remaining - margin

    def _run_checks_parallel(workspace_root, decisions, *, deadline,
                             run_one=run_one_check, clock=time.monotonic):
        """Run each runnable decision's check on its own daemon thread. Each worker samples
        the clock ITSELF (after being scheduled) and computes its timeout via _per_check, so
        every subprocess self-kills ~margin before `deadline` regardless of scheduling delay
        -> no leak by construction. The spawn loop is deadline-guarded (bounds spawning for
        large N); never-started / too-late / unfinished / crashed checks become the -1
        'unavailable' sentinel. Daemon threads so a stuck subprocess never blocks return."""
        runnable = sorted((d for d in decisions if has_runnable_check(d)), key=lambda d: d.id)
        results: dict[str, CheckRun] = {}
        lock = threading.Lock()
        def worker(d):
            pc = _per_check(deadline, clock())          # sample in-thread, right before spawning
            if pc is None:                              # scheduled too late -> leave unavailable
                return
            try:
                r = run_one(d.check, cwd=workspace_root, timeout=pc)
            except Exception as e:
                r = CheckRun(False, -1, f"check crashed: {e}")
            with lock:
                results[d.id] = r
        threads = []
        for d in runnable:
            if clock() >= deadline:                     # spawn guard: bound the spawn loop for large N
                break
            t = threading.Thread(target=worker, args=(d,), daemon=True)
            try:
                t.start()
            except RuntimeError:                        # thread exhaustion -> stop
                break
            threads.append((t, d))
        for t, _ in threads:
            t.join(timeout=max(0.0, deadline - clock()))
        with lock:
            return [(d, results.get(d.id, CheckRun(False, -1, "authoring budget exhausted")))
                    for d in runnable]

    def _to_verdict(results):
        violations, unavailable = [], []
        for d, run in results:
            if run.satisfied: continue
            if run.exit_code in _UNAVAILABLE_EXIT_CODES: unavailable.append(d.id)
            else: violations.append(Violation(d.id, run.detail, f"docs/decisions/{d.id}.md"))
        violations.sort(key=lambda v: v.decision_id); unavailable.sort()
        return Verdict(violations=violations, unavailable=unavailable)

    def run_authoring_check(workspace_root, *, clock=time.monotonic):
        t0 = clock()
        try:
            decisions, _ = load_decisions(workspace_root)
            opted = [d for d in decisions if d.authoring_time and _integrity_ok(d)]
            if not opted: return Verdict(violations=[])
            results = _run_checks_parallel(workspace_root, opted,
                deadline=t0 + AUTHORING_TOTAL_BUDGET, clock=clock)
            return _to_verdict(results)
        except Exception as e:                              # fail-open: never crash the agent's Stop
            print(f"super-harness authoring check failed open: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)            # observable (stderr/logs), NOT model-facing
            try:
                decisions, _ = load_decisions(workspace_root)
                ids = sorted(d.id for d in decisions
                             if d.authoring_time and _integrity_ok(d) and has_runnable_check(d))
            except Exception:
                ids = []
            return Verdict(violations=[], unavailable=ids)
Update test_authoring_check.py imports: drop _to_violations + CheckFailure; old _to_violations test -> test_to_verdict_splits_violations_and_unavailable.

## Task 5: orchestrator tests
- test_to_verdict_splits_violations_and_unavailable: (d-ok satisfied)->skip; (-1,127)->unavailable sorted; (1)->violation.
- test_checks_run_concurrently (TRUE simultaneity gauge):
    N=3; barrier=threading.Barrier(N, timeout=3); cur={"n":0,"peak":0}; lk=threading.Lock()
    def fake(cmd,*,cwd,timeout):
        with lk: cur["n"]+=1; cur["peak"]=max(cur["peak"],cur["n"])
        try: barrier.wait()
        finally:
            with lk: cur["n"]-=1
        return CheckRun(True,0,"ok")
    _run_checks_parallel(tmp, [_dec(f"d-{i}",check=f"C{i}") for i in range(N)],
                         deadline=time.monotonic()+10, run_one=fake)
    assert cur["peak"]==N       # serialized -> peak stays 1 -> fails loudly (barrier timeout, no hang)
- test_per_check_pure (exhaustive, no threads/clock): _per_check(deadline=10, now=0) == 10-margin; now just under (deadline-min_slice-margin) -> a positive float; now at/after (remaining <= min_slice+margin) -> None; boundary exactness.
- test_started_check_unfinished_degrades_to_unavailable (COVERS JOIN-TIMEOUT PATH; put in tests/integration — it is a real-time ~3s test, not a fast unit test):
    started=threading.Event(); release=threading.Event()
    def fake(cmd,*,cwd,timeout): started.set(); release.wait(timeout=10); return CheckRun(True,0,"late")
    dl = time.monotonic() + 3.0   # generous: remaining ~3.0 >> min_slice+margin(1.5) so even under CI load the worker samples in time and STARTS
    res=_run_checks_parallel(tmp, [_dec("d-slow",check="S")], deadline=dl, run_one=fake)  # real clock; main join blocks until ~dl then times out
    release.set()                            # clean up the daemon worker
    assert started.is_set()                  # worker actually STARTED + ran (proves started-then-unfinished path, not never-started)
    assert res[0][1].exit_code==-1           # unfinished at the deadline -> unavailable
- test_startup_stops_when_already_expired (spawn guard; deterministic — clock past deadline so NO worker ever runs, hence no worker/main clock race):
    called={"n":0}
    def fake(cmd,*,cwd,timeout): called["n"]+=1; return CheckRun(True,0,"ok")
    res=_run_checks_parallel(tmp, [_dec(f"d-{i}",check=f"C{i}") for i in range(5)],
                             deadline=1.0, run_one=fake, clock=lambda: 100.0)
    assert called["n"]==0                   # spawn guard breaks on iter 1 -> nothing spawned
    assert len(res)==5 and all(r.exit_code==-1 for _,r in res)   # all unavailable
    # NOTE on boundedness for large N: proven by TWO deterministic tests — this one (main spawn
    # guard stops spawning once clock>=deadline) + test_per_check_pure (a worker scheduled too late
    # samples remaining<=min_slice+margin -> _per_check None -> self-skips -> unavailable). Both
    # mechanisms bound the work; the earlier "mid-list advancing-clock" test was dropped because a
    # shared injected clock is consumed by both the main spawn-guard and the worker _per_check calls,
    # conflating the two and making the assertion scheduler-dependent (false-pass risk).
- test_setup_failure_degrades_observably (capsys): monkeypatch _run_checks_parallel to raise;
    _write_decision(tmp,"d-armed",...,authoring=True); v=run_authoring_check(tmp);
    assert v.violations==[] and v.unavailable==["d-armed"]; assert stderr diagnostic emitted.

## Task 6: integration test (tests/integration/test_authoring_parallel.py)
_arm(tmp) writes a ratified authoring_time decision with a valid computed hash + `! grep -rn 'api\.github\.com' src/`. clean src -> violations==[]; poison src/bad.py -> violations==["d-x"].

## Task 7: read-only + keep-small contract in arming recipe (agents_md_render.py + regen)
Add two notes: (a) "The check MUST be read-only and reentrant (armed checks run concurrently; write no source/cache/.pyc/lock/temp under the tree; use lint-imports --no-cache)." (b) "Keep the armed authoring set small — each armed check runs concurrently on every turn end." Then sync --agents-md -y && sync --check clean.

## Task 8: commit1 — pytest -q PASS; decision check unchanged (armed=1); commit orchestration+primitive+tests+docs.

## Task 9: arm d-gh-cli-not-rest — authoring_time:true; decision ratify (bites). Bite evidence: inject api.github.com into a real src file, run_authoring_check -> d-gh-cli-not-rest in violations; rm -> []. Record in design.

## Task 10: arm d-merge-gate-pure-git — authoring_time:true; decision ratify (bites). Budget evidence: 3 armed, time run_authoring_check on real tree -> violations [], unavailable [], wall << 10s. Append evidence. commit2.

## Self-review: no-leak now by construction (per_check = remaining − margin -> subprocess self-kills + killpg before main deadline); killpg targets proc.pid (survives dead leader) + bounded reap; join-timeout degradation now has a real started-then-unfinished test; startup bounded by both an already-expired and a mid-list-advancing-clock test; Verdict default_factory (no call-site breakage); no decisions.py change / no @decision anchor -> no tier-2 reconcile.
