# Implementation plan: 2026-07-02-unified-shell-runner

Design: `2026-07-02-unified-shell-runner-design.md`. TDD throughout (red →
green per task). Tier hint: Normal. Source: fresh-eyes review F5+F10 +
HG-AUTHORING-ENV (registered trigger: "next time check_runner is touched").
Incorporates two-actor plan review to convergence — Codex r1 (spawn-failure
capture matrix, tail-extraction behavior pins, marker-style kill tests, quoted
shell paths), Codex r2 (bounded-return assert on the verification kill test;
dropped the racy partial-output assert — best-effort, not a contract), Claude
subagent (unused-import F401s in check_runner + the test import block, ruff/
mypy added to the verification gate list, stale check_runner module docstring,
fixture-name nits).

Environment note (self-host): `export PATH="$PWD/.venv/bin:$PATH"` before any
pytest run (integration tests spawn `super-harness-hook`).

## Task 1 — `core/shell_runner.py`: the single subprocess primitive

**Files**: create `src/super_harness/core/shell_runner.py`,
create `tests/unit/core/test_shell_runner.py`.

1. **Test (red)** — `tests/unit/core/test_shell_runner.py`:
   - `run_shell("echo hi", cwd=tmp_path, timeout=10)` → `exit_code == 0`,
     `stdout == "hi\n"`, `timed_out is False`, `spawn_error is None`,
     `duration_ms >= 0`.
   - `run_shell("echo err >&2; exit 3", ...)` → `exit_code == 3`,
     `stderr == "err\n"`.
   - **Group kill + bounded return** (the F5 payload) — marker-style, mirroring
     `test_check_runner.py::test_timeout_kills_process_group` (no pid probes:
     `os.kill(pid, 0)` counts zombies as alive and the failure-branch cleanup
     kill risks a reused pid — round-1 Codex catch):
     ```python
     @pytest.mark.skipif(os.name != "posix", reason="process-group kill is POSIX-only")
     def test_timeout_kills_process_group(tmp_path):
         marker = tmp_path / "marker"
         q = shlex.quote(str(marker))
         t0 = time.monotonic()
         res = run_shell(f"(sleep 1; touch {q}) & echo started", cwd=tmp_path, timeout=0.4)
         assert time.monotonic() - t0 < 10  # generous elapsed bound (external clock,
         assert res.timed_out and res.exit_code == -1  # not load-sensitive duration_ms)
         time.sleep(1.3)  # wait past the grandchild's delay
         assert not marker.exists()  # group kill got the grandchild before its touch
     ```
     No partial-output assertion: under CI load the shell may not have emitted
     `started` within 0.4s (round-2 Codex catch), and neither wrapper consumes
     output on timeout — the primitive's docstring says "best-effort", not a
     contract.
   - Plain timeout: `run_shell("sleep 5", cwd=tmp_path, timeout=1)` →
     `timed_out is True`, `exit_code == -1`, generous elapsed bound (< 10s).
   - `env` dict **replaces** (`run_shell("echo $PROBE", cwd=tmp_path,
     timeout=10, env={"PATH": os.environ["PATH"], "PROBE": "x"})` → `"x\n"`);
     `env=None` **inherits** (monkeypatch `SHELLRUNNER_PROBE`, see it in
     `echo`).
   - Spawn failure: `cwd=tmp_path / "missing"` → `spawn_error` non-None,
     `exit_code == -1`, `timed_out is False`, no raise.
   - Non-UTF-8 output: `run_shell("printf '\\377'", ...)` → no raise,
     `"�" in res.stdout` (octal escape: POSIX-portable, unlike `\xff` which
     dash's printf on Linux CI prints literally).
   - `scrubbed_environ()`: monkeypatch `SUPER_HARNESS_X=1` → absent from
     result; `PATH` present; `os.environ` not mutated. (Port of the existing
     test in `test_verification_runner.py:531`, which moves here.)
2. **Run** `pytest tests/unit/core/test_shell_runner.py -v` → all fail with
   `ModuleNotFoundError`.
3. **Implement (green)** — `src/super_harness/core/shell_runner.py`:
   ```python
   """One leaf primitive for running a shell check: timeout, group kill, reap.

   Both check runners (`core.check_runner.run_one_check`, decision checks;
   `sensors.verification_runner.run_check`, verification checks) wrap this so
   the timeout/kill/reap/env semantics have a single point of truth (F10).
   `shell=True` is intentional on both paths: commands are repo-owner-trusted
   (ratified hash-locked checks / verification.yaml); the trust boundary is
   upstream of this primitive.

   Lives in core because sensors→core is the legal import direction
   (d-core-is-base forbids core→sensors); imports stdlib only.
   """
   from __future__ import annotations

   import os
   import signal
   import subprocess
   import time
   from dataclasses import dataclass
   from pathlib import Path

   _HARNESS_ENV_PREFIX = "SUPER_HARNESS_"

   @dataclass(frozen=True)
   class ShellResult:
       exit_code: int          # -1 sentinel when timed_out or spawn_error
       stdout: str
       stderr: str
       timed_out: bool
       duration_ms: int
       spawn_error: str | None  # OSError text when the shell could not launch

   def scrubbed_environ() -> dict[str, str]:
       # moved verbatim from verification_runner._scrubbed_environ (#60);
       # keep that docstring's rationale (clean-room wrt SUPER_HARNESS_*)
       return {k: v for k, v in os.environ.items()
               if not k.startswith(_HARNESS_ENV_PREFIX)}

   def run_shell(command: str, *, cwd: Path, timeout: float,
                 env: dict[str, str] | None = None) -> ShellResult:
       """Never raises. `env=None` inherits ambient; a dict REPLACES it
       (must include PATH). On timeout the whole process GROUP is killed
       (start_new_session → child is group leader), then reaped with a
       bounded 2s communicate so a stuck grandchild can never hang the
       caller. On timeout any collected output is returned BEST-EFFORT
       (may be empty; not a contract — both wrappers ignore it)."""
       t0 = time.perf_counter()
       try:
           proc = subprocess.Popen(
               command, shell=True, cwd=str(cwd), env=env,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, errors="replace", start_new_session=True,
           )
       except OSError as e:
           return ShellResult(-1, "", "", False,
                              int((time.perf_counter() - t0) * 1000), str(e))
       timed_out = False
       try:
           out, err = proc.communicate(timeout=timeout)
       except subprocess.TimeoutExpired:
           timed_out = True
           try:
               os.killpg(proc.pid, signal.SIGKILL)
           except (ProcessLookupError, PermissionError):
               proc.kill()  # best effort: at least the direct child
           try:
               out, err = proc.communicate(timeout=2)  # bounded reap
           except subprocess.TimeoutExpired:
               out, err = "", ""  # give up reaping; never hang
       duration_ms = int((time.perf_counter() - t0) * 1000)
       return ShellResult(
           exit_code=-1 if timed_out else proc.returncode,
           stdout=out or "", stderr=err or "",
           timed_out=timed_out, duration_ms=duration_ms, spawn_error=None,
       )
   ```
   (Comment style: constraints only, per repo norm. `__all__ = ["ShellResult",
   "run_shell", "scrubbed_environ"]`.)
4. **Run** the new test file → PASS; commit
   (`feat: add core.shell_runner — single subprocess check primitive`).

## Task 2 — `run_one_check` becomes a wrapper + env scrub (HG-AUTHORING-ENV)

**Files**: modify `src/super_harness/core/check_runner.py:32-70`,
modify `tests/unit/core/test_check_runner.py`.

1. **Test (pin, green today)** — the existing suite does NOT pin the tail
   extraction details (round-1 Codex catch: only broad shape is covered), so
   first add behavior pins that must pass BEFORE and AFTER the rewrite:
   ```python
   def test_detail_prefers_stderr_tail(tmp_path):
       r = run_one_check("echo out; echo err1 >&2; echo err2 >&2; exit 1", cwd=tmp_path)
       assert not r.satisfied and r.detail == "err2"

   def test_detail_falls_back_to_stdout_tail(tmp_path):
       r = run_one_check("echo o1; echo o2; exit 1", cwd=tmp_path)
       assert r.detail == "o2"

   def test_detail_empty_output_reports_exit_code(tmp_path):
       r = run_one_check("exit 7", cwd=tmp_path)
       assert not r.satisfied and r.exit_code == 7 and r.detail == "exited 7"

   def test_timeout_message_text(tmp_path):
       r = run_one_check("sleep 5", cwd=tmp_path, timeout=1)
       assert r.detail == "timeout after 1s"

   def test_bad_cwd_message_text(tmp_path):
       r = run_one_check("true", cwd=tmp_path / "nope")
       assert r.detail.startswith("could not run: ")
   ```
   **Test (red)**:
   ```python
   def test_check_env_is_scrubbed_of_harness_knobs(tmp_path, monkeypatch):
       monkeypatch.setenv("SUPER_HARNESS_CHECK_PROBE", "1")
       run = run_one_check('test -z "$SUPER_HARNESS_CHECK_PROBE"', cwd=tmp_path)
       assert run.satisfied
   ```
   Red today: the knob is inherited, `test -z` fails.
2. **Implement (green)** — replace `run_one_check`'s subprocess body with:
   ```python
   def run_one_check(command: str, *, cwd: Path, timeout: float = DEFAULT_TIMEOUT) -> CheckRun:
       res = run_shell(command, cwd=cwd, timeout=timeout, env=scrubbed_environ())
       if res.spawn_error is not None:
           return CheckRun(False, -1, f"could not run: {res.spawn_error}")
       if res.timed_out:
           return CheckRun(False, -1, f"timeout after {timeout}s")
       detail = (res.stderr or res.stdout or "").strip().splitlines()
       tail = detail[-1] if detail else f"exited {res.exit_code}"
       return CheckRun(res.exit_code == 0, res.exit_code, tail)
   ```
   Docstring: keep the trust-boundary + group-kill paragraphs (point at
   `core.shell_runner` for the semantics), add the env contract: checks run
   against `scrubbed_environ()` on EVERY path (authoring Stop-hook, CI
   `decision check`, ratify `bite_test`) so the authoring-time verdict and the
   merge-gate verdict agree by construction; a check needing an env knob
   inlines it in its ratified snippet. Drop the now-unused imports: `os`,
   `signal` (only uses were in the replaced body — leaving them is a
   deterministic ruff F401 failure); `subprocess` STAYS (`changed_files`).
   Module docstring: "ALL subprocess ... machinery lives here" is stale once
   the primitive moves — reword to "the sandbox / git-diff machinery lives
   here; the subprocess primitive is `core.shell_runner`".
3. **Run** `pytest tests/unit/core/test_check_runner.py -v` → all pass (the
   new pins + existing timeout/group-kill tests together prove wrapper
   parity). Commit.

## Task 3 — verification `run_check` becomes a wrapper (F5 port)

**Files**: modify `src/super_harness/sensors/verification_runner.py:128-223`
(+ `_scrubbed_environ` block ~554-574), modify
`tests/unit/sensors/test_verification_runner.py`.

1. **Test (red)** — `tests/unit/sensors/test_verification_runner.py`:
   - **Group kill** — marker-style (same rationale as Task 1; `timeout_seconds`
     is `int`, so the grandchild delay is 2s against a 1s timeout):
     ```python
     @pytest.mark.skipif(os.name != "posix", reason="process-group kill is POSIX-only")
     def test_timeout_kills_process_group(tmp_path):
         marker = tmp_path / "marker"
         q = shlex.quote(str(marker))
         spec = _spec(  # helper mirroring existing CheckSpec fixtures
             command=f"(sleep 2; touch {q}) & echo started",
             timeout_seconds=1,
         )
         t0 = time.monotonic()
         result = run_check(spec, workdir=tmp_path, env=_ENV,
                            archive_dir=tmp_path / "a", variables={})
         assert time.monotonic() - t0 < 10  # bounded return, external clock
         assert result.status == "timeout" and result.exit_code == -1
         assert result.output_path is None  # timeout stays the no-archive case
         time.sleep(2.3)  # wait past the grandchild's delay
         assert not marker.exists()
     ```
     Red today: `subprocess.run` kills only the shell; the orphaned grandchild
     touches the marker.
   - **Spawn failure → fail, not crash**: `workdir=tmp_path / "missing"`,
     `capture="both"` → `status == "fail"`, `exit_code == -1`, no raise
     (red: `OSError` propagates today), and the NORMAL capture matrix applies
     (round-1 Codex BLOCKER — only timeout is the no-archive exception):
     `<id>.stdout` exists and is empty, `<id>.stderr` contains
     `could not run: `, `output_path == str(archive_dir)`.
   - **Non-UTF-8 output**: command `printf '\377'`, `capture="stdout"` → no
     raise, archived file contains `�` (red: strict decode raises).
   - Move `test_scrubbed_environ_strips_harness_prefix` to
     `test_shell_runner.py` (Task 1); DELETE `_scrubbed_environ` from this
     file's import block (the moved test was its only user — an "updated"
     unused import is another F401). Add the `shlex` / `time` imports the new
     tests need (the file currently lacks both).
2. **Implement (green)** — in `verification_runner.py`:
   - Delete `_scrubbed_environ` + `_HARNESS_ENV_PREFIX`; import
     `run_shell`, `scrubbed_environ` from `core.shell_runner`;
     `_config_check_task` uses `scrubbed_environ()`.
   - `run_check` body — timeout is the ONLY no-archive case; spawn failure
     flows through the NORMAL capture matrix (empty stdout, `could not run:`
     text on the stderr channel) so the documented `output_path` rules hold for
     every non-timeout outcome (round-1 Codex BLOCKER):
     ```python
     cmd = interpolate(check.command, variables)
     archive_dir.mkdir(parents=True, exist_ok=True)
     res = run_shell(cmd, cwd=workdir, timeout=check.timeout_seconds, env=env)
     if res.timed_out:
         return CheckResult(id=check.id, status="timeout", exit_code=-1,
                            duration_ms=res.duration_ms, must_pass=check.must_pass,
                            command=cmd, output_path=None)
     if res.spawn_error is not None:
         out_text, err_text = "", f"could not run: {res.spawn_error}\n"
         status, exit_code = "fail", -1
     else:
         out_text, err_text = res.stdout, res.stderr
         status = "pass" if res.exit_code == 0 else "fail"
         exit_code = res.exit_code
     # capture matrix + output_path mapping: UNCHANGED from today (write
     # <id>.stdout / <id>.stderr per check.capture; output_path = file for
     # stdout/stderr, archive dir for both, None for none), writing out_text /
     # err_text; duration from res.duration_ms
     return CheckResult(id=check.id, status=status, exit_code=exit_code, ...)
     ```
   - Docstring updates: `run_check` — timeout now kills the process GROUP
     (point at `core.shell_runner`); spawn failure maps to
     `status="fail"`/`exit_code=-1` through the normal capture matrix (empty
     stdout + `could not run:` stderr); env contract text now names
     `core.shell_runner.scrubbed_environ`. `CheckResult` docstring — extend the
     `output_path` rules with the spawn-failure row (timeout stays the only
     no-archive case).
3. **Run** `pytest tests/unit/sensors/test_verification_runner.py -v` → PASS.
   Commit.

## Task 4 — same-file carry-alongs (verification_runner.py only)

1. `VerificationRunner` class docstring: drop "(baseline stubbed to `[]` in
   Task 8.4)" — describe the real three layers (baseline in-process /
   adapter / user). (F11c)
2. Add `encoding="utf-8"` to every `write_text` in this file (`run_check`
   archives ×2, `_make_baseline_result` report, `write_summary_json`). (F11a/d
   slice; no behavior change on macOS/Linux UTF-8 locales, pins it elsewhere.)
3. Full-file grep: no remaining references to `_scrubbed_environ` in code or
   docstrings. Commit.

## Task 5 — full verification

- `export PATH="$PWD/.venv/bin:$PATH"`; `.venv/bin/python -m pytest` full
  suite green.
- `ruff check src tests` and `mypy` (the two CI lint gates in
  `.github/workflows/lint.yml`; note `super_harness.core.*` is under strict
  mypy per pyproject overrides — the new module must pass strict).
- `PYTHONPATH=src lint-imports --config .importlinter --no-cache` → contracts
  KEPT (new core module imports stdlib only).
- `super-harness decision check` clean (no anchored files touched — verified
  pre-plan: no `reconciled_anchors` hit on either runner).
- `super-harness sync --check` clean (no adapter/CLI doc surfaces changed —
  no regen expected).

## Declared scope (attest coverage)

- `docs/plans/2026-07-02-unified-shell-runner-design.md`
- `docs/plans/2026-07-02-unified-shell-runner-plan.md`
- `src/super_harness/core/shell_runner.py`
- `src/super_harness/core/check_runner.py`
- `src/super_harness/sensors/verification_runner.py`
- `tests/unit/core/test_shell_runner.py`
- `tests/unit/core/test_check_runner.py`
- `tests/unit/sensors/test_verification_runner.py`
- `.harness/attestations/2026-07-02-unified-shell-runner.jsonl`

## Risks / notes

- **`run_one_check` env scrub is a deliberate behavior change** on three call
  paths (authoring hook / CI decision check / bite_test). All 3 armed checks
  are env-free (two greps; `lint-imports` inlines `PYTHONPATH=src`), verified
  in the design. The self-host lifecycle itself is the live probe: `decision
  check` in CI and the Stop hook run with the scrub from this PR onward.
- **False-timeout shape is kept** (bg child holding stdout → `timeout` verdict
  even when the shell exited 0): pre-existing on both paths, now documented in
  the primitive, and the group kill cleans up the leak.
- **Frozen surfaces untouched**: `CheckResult` field set, `verify --json`
  block, `summary.json` schema, event payloads. The only new observable is
  spawn-failure mapping to `fail` instead of a crash.
- Windows: `start_new_session`/`killpg` remain POSIX-only — pre-existing #61
  constraint, now shared, not newly introduced.
