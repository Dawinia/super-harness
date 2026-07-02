# Design — Run authoring-time conformance checks in parallel, then arm two more decisions

**Date:** 2026-07-02
**Scope:** `core/authoring_check.py`, `core/check_runner.py`, `engineering/agents_md_render.py` + regenerated `AGENTS.md`, and their tests; plus arming `docs/decisions/d-gh-cli-not-rest.md` and `docs/decisions/d-merge-gate-pure-git.md`. Extends the authoring-time conformance feedforward so the interactive nudge covers the rules that matter, starting with the only decision whose CI check has ever failed against a real change.

## Problem

The Claude Code / Codex `Stop` hook that produces the authoring-time conformance
verdict runs under a hard **10s** per-hook wall-clock kill (`_STOP_TIMEOUT = 10`,
`adapters/agent/_settings_merge.py`). If the hook exceeds it, the agent kills the
**whole hook process** — every check's result is lost silently, violating the
invariant at `authoring_check.py` ("a slow graph MUST degrade to `unavailable`
(silent), never a hard kill").

Today `run_authoring_check` runs the armed checks **sequentially** via
`run_executable_checks(timeout=8)`, which hands each check its own `timeout=8`.
With one armed decision the worst case is 8s < 10s, but the per-check timeout does
**not** compose into a bounded total: arming a second check makes the worst case
2×8 = 16s > 10s → hard kill → the whole feedforward silently dropped. So the
sequential model caps us at one armed check, and arming more (the point of this
work) requires changing the execution model.

Empirically all three armable checks run in well under a second on this repo
(`lint-imports --no-cache` ≈ 0.2–0.5s; the two greps ≈ 0.01–0.12s), so N×8s is a
*worst-case* bound, not the typical runtime. The real hazard is an adopter repo
with a large import graph where `lint-imports` takes several seconds: under
sequential execution its runtime adds to the others' and can cross 10s. The fix
must make "the hook returns before 10s" hold **by construction**, independent of
how many checks are armed or how slow any one is.

## Core idea

The armed authoring checks are **independent assertions**. Run them **concurrently
inside the one hook process**, each on its own daemon thread, and have the main
thread collect results under a single **dynamic wall-clock budget** (`t0 +
AUTHORING_TOTAL_BUDGET`). Wall-clock then tracks the *slowest* check rather than
their *sum*, and — because the workers are daemon threads and the collecting joins
have a real deadline — the main thread returns within the budget regardless of how
slow or numerous the checks are.

### Rejected alternatives

- **Even split of a shared budget (sequential):** `budget // N` per check. Bounded
  and simple, but arming the Kth check shrinks every peer's slice, regressing the
  already-working `d-core-is-base` on large repos. Rejected: arming should not
  regress peers.
- **Priority-ordered running-deadline (sequential):** important-and-fast checks
  first, the tail going `unavailable` when the budget is spent. Bounds total
  wall-clock without concurrency and keeps the important check first. Rejected
  because its correctness depends on **ordering slow checks last**, and which check
  is slow is **not statically knowable** (`lint-imports` is fast here, seconds on a
  large adopter graph). A mis-ordered slow check starves the important grep — the
  exact failure this work exists to prevent. Parallel execution is
  *order-independent*: the fast greps always complete regardless of a co-armed slow
  check.
- **N separate OS-level `Stop` hooks:** each gets its own 10s, but this relocates
  complexity into the adapter boundary — the armed set (a control plane) would have
  to be re-synced into `settings.json` on every arm/disarm, and multiple blocking
  `Stop` hooks would each return their own feedback string with merge semantics the
  hook protocol does not define. Rejected: dominated by in-process parallelism.

The deciding factor: we *know* one armed check (`lint-imports`) can be slow and the
armed set is expected to grow, both of which favour an order-independent model
where a known-slow check never gates a fast, important one.

## Boundary decisions

### 1. Parallelism is authoring-only; CI stays sequential

`run_executable_checks` is shared by the authoring path (latency-bound) and the CI
`decision check` (`cli/decision.py`, correctness-bound, per-check 30s, no total
cap, never skips). **CI stays sequential and untouched** — it wants every check run
to completion for soundness and is not latency-bound. Concurrency exists only to
protect interactive latency against the 10s kill, so it lives only on the authoring
path.

### 2. `has_runnable_check(d)` — one shared predicate

Extract a pure predicate `has_runnable_check(d)` (`d.status == "ratified" and
d.check is not None`) into `check_runner.py`. Both the CI runner's filter and the
authoring fan-out use it, so "a decision whose check can actually run" cannot drift
between the two paths. `run_one_check` (the single-check primitive) is otherwise
unchanged except for the process-group hardening below.

### 3. Concurrency safety — proven where provable, contracted where not

- **Orchestrator has zero shared mutable state.** Each worker touches only its own
  arguments and writes only its own result slot; `run_one_check` holds no module
  global, does no `os.chdir`, builds no tempdir on this path, and gives each
  subprocess its own pipes. The "no shared mutable state" claim is scoped to the
  Python orchestrator.
- **Result collection uses an explicit `threading.Lock`** — no reliance on
  CPython dict-atomicity arguments. The main thread reads the dict after the join
  phase.
- **The executed checks must be read-only / reentrant — this is the meaning of
  `authoring_time: true`, stated as a contract.** Concurrency makes it load-bearing:
  N checks running at once is safe only if none writes a path another reads/writes
  (tool caches, lock files, `__pycache__`/`.pyc`, temp under cwd). `shell=True`
  cannot enforce this, so opting a decision into the authoring loop asserts its
  check is read-only, reentrant, and writes no cache/pyc/temp under the tree. The
  AGENTS.md "arming a decision" recipe states this. Our checks satisfy it (two
  greps; `lint-imports --no-cache`, the flag chosen deliberately so no cache file is
  written).
- **Daemon threads + a dynamic budget join → the main thread returns within the
  budget.** Each worker is a `threading.Thread(daemon=True)`; the main thread joins
  each with `timeout = deadline - now`. A check that overruns or hangs does **not**
  block the main thread's return or interpreter exit — it is recorded `unavailable`,
  the others survive. This is strictly better than a `ThreadPoolExecutor` (whose
  `__exit__` → `shutdown(wait=True)` would block on a hung worker until the outer
  10s kill, dropping everything).
- **No subprocess leak by construction.** Each worker samples the clock *itself*
  (after being scheduled, immediately before spawning) and sets its subprocess
  timeout = `remaining − _CLEANUP_MARGIN` (0.5s). So the subprocess self-kill time
  ≈ `deadline − margin` plus a small bounded setup delay (the sample→Popen
  statements, ~µs-ms) that is far under the 0.5s margin. Sampling *in the worker*
  removes the **unbounded** thread-scheduling delay from the equation (computing the
  timeout in the main thread before `Thread.start` would put that unbounded delay
  inside it). A worker scheduled too late (remaining ≤ `_MIN_SLICE + _CLEANUP_MARGIN`)
  self-skips → `unavailable`. On timeout, `run_one_check` fires
  `os.killpg(proc.pid, SIGKILL)` — `proc.pid` is the pgid under
  `start_new_session=True`, so targeting it (not `os.getpgid`, which raises if the
  shell leader already exited) reaches backgrounded grandchildren — followed by a
  **bounded** reap (`communicate(timeout=2)`). Thus the process-group cleanup runs
  before the main thread returns at `deadline`; no orphan. A child that ignores
  SIGKILL is the only residual, and it cannot hard-kill the hook (daemon threads).
- **Budget is real and measured.** Fixed overhead (process launch + import +
  `load_decisions`) measured ~0.05–0.1s here. Derivation:
  `AUTHORING_TOTAL_BUDGET = 10s outer kill − p95(cold-start + import, ~0.3–0.8s on a
  cold/loaded machine; `t0` is taken inside `run_authoring_check` so it does not
  capture cold-start) − render`. `AUTHORING_TOTAL_BUDGET = 8.0` gives ≈ 8.85s total,
  ~1s margin.
- **Check-execution time bounded for any N: both loops are deadline-guarded.** The
  spawn loop stops starting threads once `clock() >= deadline` (and on `Thread.start`
  `RuntimeError`); the join loop is bounded by the deadline. The surrounding O(N)
  bookkeeping — `load_decisions`, sorting the runnable set, building the result list —
  is *not* itself deadline-guarded, but it is cheap non-subprocess work (`load_decisions`
  is pre-existing and shared with the CI path). Resource (threads/subprocesses) scales
  with N; the armed authoring set is expected to be single-digit (only ratified tier-1
  decisions with checks are armable) — a documented product expectation, not an enforced
  cap (capping below N would re-serialize the overflow and re-sum wall-clock, breaking
  the time bound).
- **Fail-open, observably.** A worker body raising → that decision `unavailable`.
  A truly unexpected exception in `run_authoring_check` is caught fail-open (never
  crash the agent's `Stop`) **but emits a stderr diagnostic + traceback** (visible
  in logs, not model-facing) so bugs are not silently swallowed; the fallback
  recompute also applies `_integrity_ok`. Unit/integration tests exercise the
  orchestrator directly (bypassing the fail-open) so real bugs are caught pre-ship.
- **Deterministic output:** results re-sorted by `id` after the join.

### 4. `Verdict` gains an optional `unavailable` list

`run_authoring_check` returns `Verdict(violations=[...], unavailable=[...])`, where
`unavailable: list[str] = field(default_factory=list)` lists decision ids that could
not be evaluated this turn. The `default_factory` keeps existing
`Verdict(violations=...)` sites working. **The Stop-hook advisory still renders only
`violations`** — `unavailable` is test/CLI observability only today (adapters render
only violations), not hook-surfaced; it is not oversold.

### 5. Honest scope of "never a false nudge"

`_to_verdict` maps a non-satisfied check to a violation unless its exit code is in
`{-1, 126, 127}` (timeout / not-executable / not-found). For the `!`-prefixed greps
this is fail-safe. But `lint-imports` can exit non-zero for a *tooling/config* error
(not a real violation), which this classifier maps to a violation → a possibly
spurious nudge. This is **pre-existing** (that decision is already armed),
conservative, and identical to the CI classifier. This design does not change the
classifier; it narrows the claim: the greps never false-nudge, and
"unexpected non-zero → violation" is a deliberate, CI-consistent choice.

### 6. Testability — prove concurrency without flaky timing

The orchestrator takes an injectable `run_one` (default `run_one_check`) and `clock`
(default `time.monotonic`); the per-check timeout policy is a pure function
`_per_check(deadline, now)`. Tests: `_per_check` pure (exhaustive, no threads);
concurrency via a `max_concurrent` counter with a `Barrier(N, timeout)` and a
`finally` decrement (a serialized regression keeps peak == 1 and fails loudly, never
hangs); the started-then-unfinished degradation path via a real generous deadline
(`monotonic()+3.0`) and a blocking fake (an integration-tier ~3s test); startup
boundedness via an already-expired clock (spawn guard breaks, nothing spawned) plus
the `_per_check` pure test (worker self-skip); the process-group kill via a
backgrounded child that inherits the pipe; and the primary dogfood bite (inject a
raw `api.github.com` into a real src file → `run_authoring_check` surfaces the
violation; clean tree silent).

## Arming the two decisions

`d-gh-cli-not-rest` and `d-merge-gate-pure-git`. For each: add `authoring_time: true`
to the frontmatter, then `super-harness decision ratify <id>`. Ratify re-runs the
bite-test (pass side + bite side) — confirming the check still bites before it is
armed into the interactive loop — and re-stamps. Because `authoring_time` is a
frontmatter key and `ratified_text_hash` covers only the body
(`compute_body_hash(d.body)`), the hash is unchanged and `_integrity_ok` still
passes; the check text lives inside the hashed body, so arming cannot silently alter
it. The re-ratify is the trust gate. Both checks are fast, read-only greps that
satisfy the §3 contract.

## Non-goals / lifecycle notes

- No `core/decisions.py` change → no tier-2 `d-decision-records` reconcile tax.
  `check_runner.py` / `authoring_check.py` / `agents_md_render.py` carry no
  `@decision:` anchor → no tier-2 reconcile from editing them.
- No new `core → adapters/sensors` import edge → the `d-core-is-base` fitness
  contract is unaffected.
- **Doc impact:** the AGENTS.md "arming a decision" recipe gains (a) the
  read-only/reentrant/no-cache contract and (b) "keep the armed authoring set small
  — each runs concurrently every turn end"; it is generated, so the edit is in
  `agents_md_render.py` and `AGENTS.md` is regenerated via `sync --agents-md`.
- **Env cross-reference (out of scope):** `run_one_check` inherits the full
  `os.environ`, unlike the scrubbed verification-runner env. Irrelevant for the two
  greps; `lint-imports`'s check sets `PYTHONPATH=src` inline. Noted, not fixed.
- **One PR, two commits.** commit1 = orchestration + primitive hardening + tests +
  docs (armed=1, inert). commit2 = arm the two decisions. They must ship together:
  commit2 alone would break the 10s invariant, commit1 alone is safe but inert.
- **Honest ceiling.** Arming stages the rule for an early nudge; a real in-anger
  trip still requires a future genuine violation in an unrelated change, which
  cannot be manufactured without becoming a bite-test.

## Review provenance

Converged after five rounds of Claude-subagent + Codex cross-review (design → plan →
rev2 → rev3 → rev4 → rev5). Codex reproduced the process-group kill and the
worker-side-sampling timing. Findings resolved along the way: the "bounded for any
N" claim (spawn loop was unbounded → both loops now deadline-guarded); the
subprocess-outlives-deadline leak (per-check timeout computed in the main thread →
now sampled in the worker); the `killpg` race on a dead shell leader (target
`proc.pid`, bounded reap); a concurrency test that could pass while serialized (true
simultaneity gauge); and an untested started-then-unfinished degradation path (now
covered).

## Manual acceptance (bite evidence, 2026-07-02)

**Ratify-time bite-test (both armed decisions).** `decision ratify` re-ran the
bite-test on each before arming; both reported `bite-test: bites`. The re-stamped
`ratified_text_hash` is byte-identical to the pre-arm value
(`sha256:8ee413d6…` / `sha256:23cd016f…`), confirming that adding
`authoring_time: true` (frontmatter) does not alter the hashed body.

**Primary bite — `d-gh-cli-not-rest`, end to end through the parallel path.**
With a raw `api.github.com` reference injected into a real `src/` file,
`run_authoring_check(".")` on the live tree returned
`violations: ['d-gh-cli-not-rest']`, `unavailable: []`. After removing the poison,
the tree is silent (`violations: []`).

**Budget — three armed checks, concurrent, on the clean real tree.**
`run_authoring_check(".")` with `d-core-is-base` + `d-gh-cli-not-rest` +
`d-merge-gate-pure-git` all armed returned `violations: []`, `unavailable: []` in
**0.157s** wall-clock — far under the 10s hook kill (and the ≈slowest-check, not the
sum: three checks in ~one check's time).
