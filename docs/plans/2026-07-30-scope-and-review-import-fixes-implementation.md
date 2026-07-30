---
change: 2026-07-30-scope-and-review-import-fixes
stage: plan
---

# Scope + review-import fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix four defects hit while shipping `d-pitfall-is-proposed-decision`, and record
the one deliberately-not-fixed constraint as a `proposed` decision.

**Architecture:** Four independent single-file fixes plus their tests. No shared
abstraction, no new module, no new CLI verb. Rationale for each in the design document.

**Tech Stack:** Python 3.10+, pytest, click.

---

## Declared scope

```yaml
- docs/plans/2026-07-30-scope-and-review-import-fixes-design.md
- docs/plans/2026-07-30-scope-and-review-import-fixes-implementation.md
- docs/decisions/d-no-backticked-nonexistent-identifiers.md
- src/super_harness/sensors/verification_runner.py
- src/super_harness/cli/plan.py
- src/super_harness/cli/review.py
- src/super_harness/adapters/reviewer/claude_cli.py
- tests/unit/sensors/test_verification_runner.py
- tests/unit/cli/test_plan.py
- tests/unit/cli/test_review.py
- tests/unit/adapters/reviewer/test_claude_cli.py
- docs/cli-reference.md
- .harness/attestations/2026-07-30-scope-and-review-import-fixes.jsonl
```

**Anchor collateral: none — verified, do not skip this check.** No file above carries a
`@decision:` sentinel and none appears in any ratified tier-2's `reconciled_anchors`
(`d-dangling-check` and `d-pitfall-is-proposed-decision` both anchor
`core/decision_check.py`, untouched here). So no decision document is rewritten by a
reconcile and none needs declaring. The previous cut skipped this check and paid a
`plan redeclare` plus a full review round for it.

`docs/cli-reference.md` is **generated** — Task 4 renames a CLI flag, so it must be
regenerated with `super-harness doc check --fix` (or its generator) and committed, or the
`doc check` CI gate fails.

`private/OPEN-ITEMS.md` is gitignored, so not a scope subject; Task 6 still requires it.

---

### Task 1: Lifecycle preflight — before any source edit

The gate blocks `src/` writes in `INTENT_DECLARED` / `PLAN_REJECTED`. Scope must be
declared and the plan approved first.

1. Commit both plan documents (`review prepare` refuses a dirty in-scope tree).
2. `super-harness plan ready 2026-07-30-scope-and-review-import-fixes --scope @<file> --tier-hint Normal`
   — pass the Declared scope list verbatim, every file individually. Never re-emit
   `plan_ready` without `--scope`.
3. Plan review to convergence: `review prepare` → `review begin --source <complete required set>`
   → run each producer per its `invocation.json` → `review result import`, or
   `review run fail --reason` for one that cannot run. **Do not use `review skip` to retire
   one producer** — that is the defect Task 4 fixes. Commit revisions *inside* the loop.
4. Register the deferred/decided items in `private/OPEN-ITEMS.md` via **Bash** (the `Write`
   tool is blocked in gated states; the file is untracked, so this is not a bypass):
   correct the wrong `is_error` entry (see design §4) and note that the dead-reference
   negative-context item is now tracked in-product as a decision, not a backlog bug.
5. Do not proceed until the state is `PLAN_APPROVED`, then `implementation start`.

---

### Task 2: `verification_runner` uses the merge gate's scope semantics

**Files:** `src/super_harness/sensors/verification_runner.py`,
`tests/unit/sensors/test_verification_runner.py`

**Step 1 — failing test.** A change whose declared scope is `["tests/"]` and whose diff
contains `tests/unit/x.py` must now report drift (previously clean, while the merge gate
blocked it).

```python
# declared scope ["tests/"], changed ["tests/unit/x.py"]
# expect: baseline scope-vs-plan reports the file as out-of-scope
```

Run it; expect FAIL (currently passes as covered).

**Step 2 — implement.** Replace the `covered_by_scope` call at
`verification_runner.py:463` with canonical-path set membership, reusing
`engineering.attestation.canonical_path` on **both** sides so the comparison is
spelling-independent (`./src/x` == `src/x`) exactly as the gate does it. Drop the now-unused
`covered_by_scope` import.

`must_pass=False` is unchanged — this check stays advisory.

**Step 3.** Run the file's tests; then `pytest -q` for the whole suite, since this check's
report text appears in verification fixtures elsewhere.

**Step 4 — do not touch `core/review_bundle.py`.** It calls the same primitive for `.md`
selection into the review bundle, which is a convenience and not a safety property. Add a
one-line comment at its call site recording that the divergence is intentional, so the next
reader does not "unify" it.

**Step 5.** Commit.

---

### Task 3: `plan ready` warns on silent `plan_artifacts` revocation

**Files:** `src/super_harness/cli/plan.py`, `tests/unit/cli/test_plan.py`

**Step 1 — failing test.** With a change whose current state has non-empty
`plan_artifacts`, `plan ready <slug>` **without** `--scope` must emit a warning naming the
artifacts that are about to lose their authorization. Assert on stderr, and assert the exit
code is still success.

**Step 2 — implement.** In `ready()` (`cli/plan.py`), before emitting: when `scope_raw is
None`, derive the change's current state and, if `cs.plan_artifacts` is non-empty, print a
warning via the project's existing warning path. Say what is lost — the `PLAN_REJECTED`
carve-out that authorizes revising those files — and how to keep it (re-pass `--scope`).

Do **not** change the reducer. The always-replace behaviour is a deliberate revocation
property (`core/reducer.py`, commented); only the silence is the defect.

**Step 3.** Tests, then commit.

---

### Task 4: `review skip` gets teeth, and its flag stops lying

**Files:** `src/super_harness/cli/review.py`, `tests/unit/cli/test_review.py`,
`docs/cli-reference.md` (generated)

**Step 1 — failing tests, three of them.**

- **Arm A:** `review skip` with no frozen round in the current epoch → exits validation
  error. *This is the arm that catches the mistake actually made.*
- **Arm B:** `review skip` while the latest round is open with a `pending` run → exits
  validation error, and the message names the pending `run_id`s.
- **Still allowed:** `review skip` after every run is `imported` or `failed` → succeeds
  (this is the disclosed-override path the previous cut relied on twelve times; it must
  keep working).

**Step 2 — implement the guards.** Derive `engineering.review_runs.derive_review_execution`
for the change + reviewer. Refuse when there are no rounds in the epoch, or when the latest
round has `status == "open"` and any run has `status == "pending"`. Use `format_error` with
a `hint` pointing at `review result import` and `review run fail --reason`, per the
project's error contract.

**Step 3 — rename `--source` to `--stuck-source`** on `skip` only. Its help text must say
it is an audit label recording which participant was stuck, **not** a scope selector, and
point at `review run fail` for retiring one producer. Leave `review begin --source` alone —
there it genuinely scopes.

**Step 4 — regenerate `docs/cli-reference.md`** and confirm `super-harness doc check`
exits 0. It is a CI gate.

**Step 5.** Tests, then commit.

---

### Task 5: `is_error` payloads are rejected outright

**Files:** `src/super_harness/adapters/reviewer/claude_cli.py`,
`tests/unit/adapters/reviewer/test_claude_cli.py`

**Step 1 — failing test.** A payload with `is_error: true` **and** a well-formed
`structured_output` must raise `ReviewerProtocolError`. Also assert the existing behaviour
is unchanged: `is_error: true` with no `structured_output` still raises (it already does —
that is the real 529 shape, and this test pins it), and a clean payload still parses.

**Step 2 — implement.** In `parse_result`, before reading `structured_output`, raise
`ReviewerProtocolError` when `raw.get("is_error")` is true, with a message saying the
producer reported failure and that the run should be re-invoked against the same frozen
`invocation.json` (transient) or recorded with `review run fail` (permanently unavailable).

**Step 3.** Tests, then commit.

---

### Task 6: Record the not-fixed constraint, then close out

**Step 1 — file the decision.**

```bash
super-harness decision new d-no-backticked-nonexistent-identifiers \
  --text "PROPOSED (unsettled): do not backtick an identifier that does not resolve in source, even to name an anti-pattern — the dead-reference gate exits 2 on it and has no negative-context detection."
```

Body: what happens (`doc refs --gate` exits 2 on a high-confidence hit; a decision body is
hash-locked at `ratify`, so learning this afterwards costs a re-ratify); the rule (say it in
prose, unbackticked); why it is not fixed (negating-clause detection is prose parsing, which
this engine avoids; an opt-out marker adds an authoring surface and its own rot to save one
word); and the exit (retire if the checker ever grows a sane opt-out).

Leave it `proposed`. **Do not anchor it** — a `@decision:` sentinel naming a proposed id is
dangling-up and fails CI.

**Step 2 — full gate set, by exit code, before `done`.** There is no CLI-reachable recovery
from `AWAITING_CODE_REVIEW` (see `d-no-recovery-from-awaiting-code-review`), so all of these
must be green first: `pytest -q`, `super-harness verify <change>`, `decision check`,
`doc check`, and **`doc refs --gate` — check its exit code, not its output.**

**Step 3.** `done` → code review → `attest write` → PR → `on-merge`.

---

## Definition of done

- `verification_runner`'s scope baseline uses canonical-path set membership, matching the
  merge gate; `must_pass=False` unchanged; `core/review_bundle.py` left on prefix matching
  with a comment recording why.
- `plan ready` warns (exit still success) when `--scope` is omitted while `plan_artifacts`
  is non-empty; reducer unchanged.
- `review skip` refuses with no frozen round, and refuses while the latest round is open
  with pending runs; the post-recording override path still works; the flag is
  `--stuck-source` and its help says "audit label, not a scope selector".
- `parse_result` rejects any `is_error` payload; the no-`structured_output` case is pinned
  by a test.
- `d-no-backticked-nonexistent-identifiers` exists, is `proposed`, and is unanchored.
- `docs/cli-reference.md` regenerated; `doc check`, `doc refs --gate`, `decision check`,
  `verify` and `pytest -q` all exit 0.
- `private/OPEN-ITEMS.md`: the wrong `is_error` entry corrected, the dead-reference item
  moved to in-product tracking.
- Attestation written; change reaches `merged`.
