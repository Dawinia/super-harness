# Implementation plan: 2026-07-03-writer-flock-toctou (v3)

Tier hint: Normal. TDD throughout (red → green). Fixes REVIEW-FINDINGS-2026-07-02
§F4: `EventWriter` validate→append is not atomic across processes (nor across
threads — the process-internal lock only wraps the append, not the validate).

v2 incorporated round-1 plan review (Codex `--sandbox read-only` + Claude
subagent, both REVISE, independently converging): (a) the multiprocessing test's
"both events on disk" invariant was incoherent — the correct post-fix outcome of
a race for a single legal slot is one append + one `EmitPreconditionError`, not
two appends; (b) a start-barrier + N rounds is a probabilistic red — widen the
window with an in-child monkeypatch instead; (c) the `.events.lock` ignore should
be product behavior (injector `_CANONICAL_PATHS`), not a dogfood-only hand-edit;
(d) tighten the `threading.Lock` wording (it is per-instance, not a process-wide
mutex — flock is the real cross-instance/cross-process correctness layer).

v3 incorporates round-2 delta review (both actors again independently converging):
(a) [BLOCKER] `intent_declared` is NOT non-repeatable — `compute_target_state`
returns `current` (self-loop "description update"), not `INVALID`, from any active
non-terminal state (`transitions.py:80-87`); and from an empty stream ONLY
`intent_declared` is legal. So there is no non-repeatable FIRST event — the test
must SEED `intent_declared` then race a genuinely single-fire FORWARD transition
(`plan_ready`: `INTENT_DECLARED→AWAITING_PLAN_REVIEW` legal, but
`(AWAITING_PLAN_REVIEW, plan_ready)` is absent from the table / not informational
/ not universal → `INVALID`, verified `transitions.py:31,116-120`). (b) [IMPORTANT]
the mp test must use context-created primitives (`ctx.Barrier`/`ctx.Queue` from
`ctx = mp.get_context("spawn")`), not default-context `mp.Barrier`/`mp.Queue`.
(c) [IMPORTANT] `test_gitignore_injector.py` keeps its OWN mirror copy of
`_CANONICAL_PATHS` (line 30) with an exact ordered-equality assertion (line 281),
so the mirror tuple must gain `.harness/.*.lock` at the SAME position; also add an
assertion that the committed `.gitignore` has no standalone `.harness/.state.lock`
outside the managed markers (the drift test only checks the marker-bounded block).

## Design (approved)

### The bug

`EventWriter.emit` (`core/writer.py`) is the single choke point to `events.jsonl`
(verified: all 13 emit sites — 10 CLI + dispatcher/framework_observer/hook_entry
— go through it). Today it does:

1. `validate_preconditions(self.path, event)` — reads the WHOLE stream (via
   `_current_state` / `_change_event_types`) to decide if the new event is a
   legal next step. **Outside** `self._lock`.
2. `with self._lock:` open `O_APPEND` fd, `write`, `fsync`, `close` — the
   process-internal `threading.Lock` wraps **only the append**.

So read-validate and append are two separate steps and nothing serializes the
pair across writers. Two emitters (two processes, or two threads sharing one
writer — the intra-process lock does not cover validate either) both validate
against the same old stream, both pass, both append → an event that is illegal
on replay lands on disk.

**Amplifier (why it is worth fixing):** the `lifecycle-ordering` baseline
(`sensors/verification_runner.py:346`) is `must_pass=True` and treats ANY
`find_ordering_violations` result as a tamper/corruption signal. events.jsonl is
append-only with no repair path, so one benign concurrent race permanently
dirties the change. v0.1 single-actor makes this rare; the project direction is
cross-actor, so this is a real soundness hole.

### The fix — a named resource lock

The protected invariant is resource-level ("the read-validate → append critical
section on the event log is atomic across all writers"), not fd-level. Model it
with a dedicated named sentinel lock, mirroring the concurrency architecture the
codebase already established: `post_emit.py` serializes state.yaml rebuilds with
`fcntl.flock` on `.harness/.state.lock`. This change adds the sibling
`.harness/.events.lock` for the emit critical section.

Rejected alternative: flocking the append fd directly (no new file). `emit` opens
a fresh `O_APPEND` fd every call, so fd-locking couples lock identity to a
transient fd, does not compose with future non-append participants (compaction /
consistent-snapshot readers / incremental index), and forces a second locking
idiom next to `.state.lock`. Its only edge — "cannot desync from the data" — is
illusory: both designs rely equally on the single-choke-point discipline (a
writer bypassing `emit` flocks nothing in either design).

Lock ordering is explicit and deadlock-free (both reviewers verified across ALL
emit sites): `emit` takes and RELEASES `.events.lock`, then the caller's
`refresh_state_after_emit` takes `.state.lock`. `emit` never calls `refresh`;
`refresh` never emits. Two named resource locks, events-before-state, never
nested; flock auto-releases on crash.

## Task 1 — concurrency regression tests (red first)

New file `tests/integration/core/test_writer_concurrency.py`. Each round SEEDS a
change with one `intent_declared` (→ `INTENT_DECLARED`) written serially, then
races two writers each emitting `plan_ready` for that `change_id`. `plan_ready` is
genuinely single-fire: `(INTENT_DECLARED, plan_ready) → AWAITING_PLAN_REVIEW` is
legal, but `(AWAITING_PLAN_REVIEW, plan_ready)` falls through to `INVALID`
(`transitions.py:31,116-120`) — so a second `plan_ready` on the advanced state is
illegal. (`intent_declared` itself is NOT usable as the raced event: it self-loops
to `current`, `transitions.py:80-87`; and it is the ONLY event legal from an empty
stream, so the race must seed first.) The correct serialized outcome of the race
is **exactly one `plan_ready` appended + one `EmitPreconditionError`**; two
`plan_ready` appends is the bug (the second is illegal on replay).

Shared invariant (both tests, every round):
`find_ordering_violations(path, change_id) == []` AND raced `successes == 1` AND
raced `EmitPreconditionError == 1` AND the change has exactly 2 events on disk
(the seed `intent_declared` + one `plan_ready`).

1. **Thread test (load-bearing deterministic red)** — seed `intent_declared`
   serially, then two `threading.Thread`s, each with its OWN `EventWriter(path)`
   instance (so flock, not the per-instance `threading.Lock`, is the layer under
   test), each emitting `plan_ready`. Patch
   `super_harness.core.writer.validate_preconditions` (imported by name at
   writer.py) with a wrapper that calls the original then `time.sleep(~20ms)` to
   widen the post-validate/pre-append window. A `threading.Barrier(2)` released
   BEFORE each thread calls `emit` starts them together.
   - Pre-fix: both validate against the seeded `INTENT_DECLARED` stream outside
     any shared lock (both see `plan_ready` legal) → both append → assertion
     "exactly one raises" FAILS (0 raised) and `find_ordering_violations` flags
     the second `plan_ready` (`AWAITING_PLAN_REVIEW → INVALID`) → RED.
   - Post-fix: thread 2 blocks on `.events.lock`, validates against the appended
     `AWAITING_PLAN_REVIEW` stream, raises `EmitPreconditionError` → GREEN.
2. **Multiprocessing test (cross-process corroborator, deterministic)** — seed
   `intent_declared` serially in the parent, then `ctx = mp.get_context("spawn")`
   (macOS default) and spawn 2 workers each emitting `plan_ready`. Each worker, at
   the TOP of its body, monkeypatches its OWN re-imported
   `writer.validate_preconditions` with the sleep-widened wrapper, waits on a
   `ctx.Barrier(2)` placed BEFORE the `emit` call (NOT inside validate — a barrier
   inside the patched validate would deadlock post-fix, since the flock winner
   sleeps while the loser is blocked on the same lock and never reaches the
   barrier), then emits and reports `"ok"`/`"rejected"` via a `ctx.Queue()`.
   Use `ctx`-created primitives throughout (not default-context `mp.Barrier` /
   `mp.Queue`) so spawn-context children and the sync objects match.
   - Pre-fix: both children sleep against the stale `INTENT_DECLARED` stream →
     both append → `find_ordering_violations` flags the second `plan_ready` → RED.
   - Post-fix: flock serializes → one `"ok"`, one `"rejected"`, clean stream →
     GREEN.
   - Run a small bounded number of rounds (fresh `change_id` each, each re-seeded)
     as belt-and-suspenders; the sleep makes a single round already deterministic.
3. **Confirm both RED** on current `writer.py` before implementing (non-vacuous
   gate, ref #61 group-kill lesson).

## Task 2 — flock the validate+append critical section (green)

`core/writer.py`:
- add `import fcntl` (POSIX-only; already the module's documented stance —
  Linux/macOS, NFS/SMB/FUSE unsupported).
- `__init__`: `self._lock_path = self.path.parent / ".events.lock"` (equals
  `paths.lock_path(root, "events")`; writer stays path-only, comment ties the
  two).
- `emit`: keep the `timestamp` isinstance guard and `serialize_event` outside the
  lock (pure; minimize critical section). Restructure the write region:
  ```
  with self._lock:                       # intra-process, per-instance thread guard
      self._lock_path.touch(exist_ok=True)
      with open(self._lock_path) as lf:
          fcntl.flock(lf.fileno(), fcntl.LOCK_EX)   # cross-instance + cross-process; blocks
          try:
              if not skip_validation:
                  validate_preconditions(self.path, event)  # READ under lock
              fd = os.open(self.path, O_WRONLY|O_APPEND|O_CREAT, 0o644)
              try:
                  os.write(fd, data); os.fsync(fd)
              finally:
                  os.close(fd)
          finally:
              fcntl.flock(lf.fileno(), fcntl.LOCK_UN)   # explicit; close would release anyway
  ```
- `skip_validation` path also holds the flock (uniform = strongest: replay/import
  can write real per-`change_id` events with `skip_validation`; a skip append
  must not land between another writer's validate→append window).
- Docstrings (module + `emit`): the flock invariant; that `threading.Lock` is a
  per-instance thread guard while flock is the real cross-`EventWriter`-instance
  AND cross-process correctness layer (two `open()`s → two OFDs → flock still
  contends); events-before-state lock order.

## Task 3 — decision faithfulness + gitignore (product behavior)

1. `core/writer.py`: add `# @decision:d-events-append-only` anchor above the
   `EventWriter` class (the decision text governs "the event writer" but only
   `events.py` was anchored — an existing gap this change closes).
2. `engineering/gitignore_injector.py`: add `".harness/.*.lock"` to
   `_CANONICAL_PATHS` (covers `.state.lock` + the new `.events.lock` + any future
   sentinel). This makes lock-sentinel ignoring PRODUCT behavior — every repo
   that runs `super-harness init` / `sync --gitignore` gets it, not just this
   dogfood repo. Update the `_CANONICAL_PATHS` header comment (the clause saying
   `.state.lock` "is ignored separately, outside this managed block" is now
   false). Regenerate the committed managed block via
   `super-harness sync --gitignore` (the drift test in
   `tests/unit/engineering/test_gitignore_injector.py` enforces committed ==
   canonical).
3. `.gitignore`: remove the now-redundant hand-written `.harness/.state.lock`
   line and its comment block (the managed block now covers it). This CLOSES the
   "init gap" that comment tracked in `private/OPEN-ITEMS.md`.
4. `tests/unit/engineering/test_gitignore_injector.py`:
   - update the test-local MIRROR copy of `_CANONICAL_PATHS` (line 30) — insert
     `".harness/.*.lock"` at the SAME position as in the real injector, so the
     exact ordered-equality guard `test_block_contains_all_canonical_paths`
     (`lines == list(_CANONICAL_PATHS)`, line 281) still passes;
   - add an assertion that lock sentinels (`.harness/.*.lock`) are covered by the
     managed block, AND that the committed repo `.gitignore` has NO standalone
     `.harness/.state.lock` line outside the managed markers (the drift test only
     compares the marker-bounded block, so a leftover hand-written line would slip
     past `sync --check` — this guards the Task 3.3 removal).
5. **LAST**, after ALL code is committed (order-sensitive — `reconcile`
   fingerprints `writer.py` at run time; nothing may touch `writer.py` after):
   `super-harness decision reconcile d-events-append-only --kind self
   --justification "…"` — justification: emit now takes a cross-process flock
   (`.events.lock`) around validate+append; no path mutates/truncates existing
   events; state remains a derived fold; decision still HOLDS. Re-fingerprints
   anchors → `{events.py, writer.py}`.

## Task 4 — full verification

- `.venv/bin/python -m pytest` full suite green (with
  `PATH="$PWD/.venv/bin:$PATH"` so integration tests find `super-harness-hook`).
- `PYTHONPATH=src lint-imports --config .importlinter --no-cache` KEPT.
- `super-harness decision check` clean (d-events-append-only reconciled;
  writer.py fingerprint fresh).
- `super-harness sync --check` clean (managed .gitignore block regenerated to
  match `_CANONICAL_PATHS`; no AGENTS.md / cli-reference surface — no CLI verb or
  exit code changed).

## Declared scope (attest coverage)

- `docs/plans/2026-07-03-writer-flock-toctou-plan.md`
- `src/super_harness/core/writer.py`
- `src/super_harness/engineering/gitignore_injector.py`
- `.gitignore`
- `docs/decisions/d-events-append-only.md` (tier-2 reconcile rewrites its
  frontmatter — reconciled_anchors gains writer.py)
- `tests/integration/core/test_writer_concurrency.py`
- `tests/unit/core/test_writer.py` (add lock-path / skip_validation-still-locks
  unit assertions)
- `tests/unit/engineering/test_gitignore_injector.py`
- `.harness/attestations/2026-07-03-writer-flock-toctou.jsonl`

## Risks / notes

- Holding the flock across `validate_preconditions` serializes emits during its
  O(N) stream read. Emits are rare lifecycle events and the read is the same cost
  as today (just relocated inside the lock); LOCK_EX blocks rather than fails, and
  a crashed holder auto-releases (kernel drops the flock on fd close / process
  death) → no deadlock. Consistent with `post_emit.py`'s LOCK_EX-waits stance.
- flock is advisory + POSIX-only. Correctness relies on every writer going
  through `EventWriter.emit` (already the single choke point) and on
  Linux/macOS + local fs (already the module's documented support matrix;
  NFS/SMB/FUSE explicitly unsupported).
- `threading.Lock` is per-`EventWriter`-instance — it serializes threads sharing
  ONE writer, not the whole process. It is kept as cheap defense-in-depth
  (matching the existing docstring rationale); the flock is what actually
  serializes two writer instances in one process and across processes.
- Pre-existing torn-read note (out of F4 scope): `refresh_state_after_emit`'s
  `derive_state` reads events.jsonl under `.state.lock`, not `.events.lock`. This
  change neither introduces nor worsens that; O_APPEND already keeps each line
  whole for readers.
