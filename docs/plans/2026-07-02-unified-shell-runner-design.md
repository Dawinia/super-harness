# Unified shell-check primitive: one timeout/kill/env seam for both check runners

**Date**: 2026-07-02 · **Change**: `2026-07-02-unified-shell-runner`
· **Source**: fresh-eyes review 2026-07-02 findings **F5 + F10**, plus the
**HG-AUTHORING-ENV** deferred item (OPEN-ITEMS) whose registered trigger was
"next time check_runner is touched".

## What the empirical check changed about F5

F5 claimed `sensors/verification_runner.run_check`'s
`subprocess.run(shell=True, capture_output=True, timeout=...)` can hang
`verify`/`done` **indefinitely** when a grandchild holds the output pipes.
Measured on the project runtime (CPython 3.13.9, both grandchild shapes:
backgrounded `cmd &` with the shell exited, and a live foreground chain), the
call returns **bounded at the deadline** — modern CPython's POSIX timeout path
kills the shell and `wait()`s it; it does not re-`communicate()` unbounded.

The real, verified defects are:

1. **Orphan leak (the F5 payload that survives)** — on timeout only the direct
   shell is killed. The actual workload (e.g. a hung pytest) keeps running
   unsupervised: burning CPU, holding locks, able to poison later verification
   runs. `core/check_runner.run_one_check` fixed exactly this in #61 with
   `start_new_session` + `killpg` + bounded reap; the verification twin never
   got the port.
2. **Twin-runner semantics divergence (F10)** — same repo, two answers for
   "run one shell check": kill scope (group vs shell-only), env base (scrubbed
   ambient vs pre-merged-by-caller with full-inherit sibling), decode
   robustness (`errors="replace"` vs strict, where strict can crash `run_check`
   on non-UTF-8 check output), spawn-failure handling (structured `-1` result
   vs uncaught `OSError` crashing the whole `verify`).
3. **Shared false-timeout shape (documented, kept)** — a check that leaves a
   background child holding stdout is reported `timeout` even if the shell
   exited 0, because `communicate` waits for pipe EOF, not process exit. Both
   runners already behave this way; the unified primitive keeps the semantics
   (a check leaking background children *is* misbehaving) but now also kills
   the leak instead of orphaning it.

## Design

### New module: `core/shell_runner.py`

One leaf primitive owning the "run one shell command soundly" semantics both
runners currently duplicate/diverge on:

```python
@dataclass(frozen=True)
class ShellResult:
    exit_code: int        # -1 sentinel when timed_out or spawn_error
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    spawn_error: str | None   # OSError text when the shell could not launch

def run_shell(command: str, *, cwd: Path, timeout: float,
              env: dict[str, str] | None = None) -> ShellResult: ...

def scrubbed_environ() -> dict[str, str]: ...   # moved from verification_runner
```

Semantics (single point of truth, lifted from #61's `run_one_check`):

- `Popen(shell=True, start_new_session=True, stdout=PIPE, stderr=PIPE,
  text=True, errors="replace")` — the child is its own process-group leader.
- `communicate(timeout=...)`; on `TimeoutExpired` → `os.killpg(proc.pid,
  SIGKILL)` (fallback `proc.kill()` on `ProcessLookupError`/`PermissionError`)
  → **bounded** 2s reap `communicate(timeout=2)` — a stuck grandchild can never
  hang the caller, and the group kill reaps the workload itself.
- `env=None` inherits the ambient environment; a dict **replaces** it
  (caller-owned policy, same contract `run_check` documents today).
- Never raises: spawn `OSError` → `ShellResult(spawn_error=...)`. Partial
  output collected by the reap is returned as-is; callers decide what to keep.

`scrubbed_environ()` (ambient minus `SUPER_HARNESS_*`) moves here verbatim from
`verification_runner._scrubbed_environ` because after this change it is shared
env *policy* for both runners, and sensors→core is the legal import direction
(core→sensors is forbidden by d-core-is-base; parking the primitive in core is
the only layering that lets both sides use it).

### Both runners become thin wrappers

- **`core/check_runner.run_one_check`** keeps its exact signature and
  `CheckRun` shape (tail extraction, `-1` sentinels, message texts). Its
  subprocess body is replaced by a `run_shell(...)` call. **One deliberate
  behavior change**: the env base becomes `scrubbed_environ()` — see below.
- **`sensors/verification_runner.run_check`** keeps its exact signature, the
  frozen `CheckResult` contract, interpolation, and archive layout. Its
  subprocess body is replaced by `run_shell(...)`:
  - timeout → `status="timeout"`, `exit_code=-1`, `output_path=None`
    (unchanged surface; the process group is now killed underneath);
  - **new**: spawn failure no longer propagates `OSError` up through the sensor
    (crashing `verify`) — it maps to `status="fail"`, `exit_code=-1`, with the
    error text archived per the check's `capture` mode (stderr channel),
    aligned with `run_one_check`'s "could not run" handling.

The three **scheduling** layers (CI sequential loop, authoring daemon threads
with worker-side deadline sampling, verification ThreadPool) are untouched —
F10's own analysis is that their separation is justified; only the leaf
primitive was triple-implemented.

### HG-AUTHORING-ENV: close it by scrubbing all decision-check paths

`run_one_check` today inherits the full `os.environ` on every path (authoring
Stop-hook, CI `decision check`, ratify-time `bite_test`). Decision: use
`scrubbed_environ()` as the base for **all** of them, uniformly:

- The authoring-time verdict and the merge-gate verdict must agree **by
  construction**. A self-host session exports `SUPER_HARNESS_CHANGE_ID`; CI has
  no such knob. Scrubbing only one path would preserve exactly the divergence
  the item warns about; scrubbing both removes the fork entirely.
- Zero behavior risk today: all 3 armed checks read no env (two pure greps;
  `lint-imports` inlines its own `PYTHONPATH=src`). `PATH` etc. survive — only
  the `SUPER_HARNESS_*` prefix is dropped.
- A future check that genuinely needs an env knob can inline it in its shell
  snippet (the ratified, hash-locked command *is* the config surface).

This retires HG-AUTHORING-ENV (registered at #61 as "evaluate next time
check_runner is touched").

### Same-file carry-alongs (no scope creep)

- `VerificationRunner` class docstring still says "baseline stubbed to `[]` in
  Task 8.4" — stale since 8.5 (F11c); rewrite to describe the real 3 layers.
- `write_text(...)` calls in `verification_runner.py` gain
  `encoding="utf-8"` (F11a/d slice, this file only).

F6 (reducer tail-line contract) and the rest of F11 live in other files and are
**not** touched.

## Behavior deltas (reviewer checklist)

| Path | Before | After |
| --- | --- | --- |
| `run_check` timeout | shell killed, grandchildren orphaned | process group killed, bounded reap |
| `run_check` non-UTF-8 output | decode crash (`text=True` strict) | `errors="replace"` |
| `run_check` spawn `OSError` | propagates; `verify` crashes | `status="fail"`, `exit_code=-1`, error archived |
| `run_one_check` env | full `os.environ` inherited | `scrubbed_environ()` (minus `SUPER_HARNESS_*`) |
| false-timeout on leaked bg child | both runners report timeout | unchanged (and the leak is now killed) |

POSIX-only note: `start_new_session`/`killpg` is the pre-existing #61
constraint; this change extends it to the verification path rather than
introducing it.

## Non-goals

F4 (writer TOCTOU), F6 (reducer), F7/F8 (daemon direction/dedup), F9
(`sync_check` relocation — the follow-up change this session), F12 (plugin
surface). No changes to `verification.yaml` schema, check declaration, or any
frozen `--json` contract.
