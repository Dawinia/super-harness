---
change: code-only-recovery
---

# A route back for a code-only fix

## The defect

`AGENTS.md` promises: "A code-only finding fix does not trigger plan review unless the
approved plan, scope, or requirements changed." The mechanism does not provide that
route. Every frozen state — `READY_TO_MERGE`, `AWAITING_CODE_REVIEW`, `PLAN_REJECTED` —
reaches an editable state only through `plan redeclare`, which rewinds to
`INTENT_DECLARED` and costs a full plan cycle.

The state machine already defines two exits that would serve:
`implementation_invalidated` → `IMPLEMENTATION_IN_PROGRESS` and
`implementation_restarted` → `PLAN_APPROVED`, both legal from any active state
(`core/transitions.py:117-120`). Nothing under `src/super_harness/cli/` emits either.
This is recorded as the proposed decision `d-no-recovery-from-awaiting-code-review`,
whose stated exit condition is "retire this record when that verb ships".

### Evidence it bites

`.harness/state.yaml` carries two `plan_redeclared` entries on this repository whose
recorded reason is verbatim:

> "READY_TO_MERGE blocks editing, so redeclare is the only route back to an editable
> state; scope is unchanged."

A downstream adopter hit the same wall harder. Folding two minor code-review findings
into a `READY_TO_MERGE` change forced a `plan redeclare`; the return leg's plan review
ran after `implementation_complete` had already fired, and returned three findings of the
form "the code does not match the plan". Satisfying them requires a source edit.
`PLAN_REJECTED` forbids source edits. Every attempted exit costs another human-authorized
round that rejects again for the same three findings, because the source still cannot be
edited. A livelock, not a slow path.

## Cut 1 — `implementation reopen`

A new verb in the existing `implementation` group emits `implementation_invalidated`,
landing the change in `IMPLEMENTATION_IN_PROGRESS`. `--reason` is required and is recorded
on the event payload: this verb voids a review the change already passed, which puts it
in the consequence class of `review authorize`, whose reason is also required.

`implementation_invalidated` rather than `implementation_restarted` because the semantics
fit — the implementation is no longer valid, go back to implementing — and because the
`PLAN_APPROVED` landing would add an `implementation start` step that is pure ceremony for
a change that has already implemented once. The proposed decision names the same event.

**Precondition: the change must already carry both `plan_approved` and
`implementation_complete`.** Read as: this change has been through the plan gate and the
implementation gate once and needs another pass. That is the merge gate's own milestone
set (`engineering/attestation.py:28`) minus `code_review_passed`, which is omitted so a
reopen from `AWAITING_CODE_REVIEW` — where the reviewer has not yet returned — still
works. Reusing the merge gate's rule keeps the CLI refusal and the merge refusal saying
the same thing instead of drifting into two policies.

Without that precondition the verb is a plan-review bypass: declare a change, run
`plan ready`, reopen without waiting, implement anything. The merge gate does catch that
(no `plan_approved` milestone), but only after all the work is done.

Rejected: requiring a `plan_approved` *after* the most recent `plan_redeclared`. It is the
tighter rule and it refuses the exact case this change exists to fix — the adopter's
change had no approval after its redeclare. Recorded because it looked right until it was
tested against the case.

### What the verb does not do

It does not touch the frozen review round, which is the second open question the proposed
decision raises. It does not need to: `implementation_complete` is the code-reviewer epoch
boundary (`engineering/review_runs.py:13`), so the following `done` opens a fresh epoch and
any round left open by the reopen falls out of `execution.rounds` on its own. Adding
machinery to invalidate it would duplicate a reset the fold already performs.

### The hole this leaves, stated rather than guarded

`READY_TO_MERGE` → `plan redeclare` with a materially wider scope → `reopen` puts newly
scoped work into the change without a plan review of that scope. The merge gate does not
catch it, because the old `plan_approved` satisfies the milestone.

This is deliberately not guarded. Reaching it requires deliberately redeclaring and then
deliberately reopening; `plan redeclare` records its reason in `redeclaration_history` and
`report` renders it, so the route is disclosed rather than silent. A fourth guard here
would be structure added to a fix, which is this repository's most repeated failure mode.

## Cut 2 — the skip guard reads the wrong ledger

Independent of the livelock, and still a defect after Cut 1 removes it.

`_guard_skip_round_evidence_or_exit` (`cli/review.py`) refuses a skip when
`not execution.rounds` — no round **in the current epoch** — reading that as "no producer
was ever asked, so there is no stuck reviewer to skip". For `plan-reviewer` the epoch
boundary is `plan_ready` (`review_runs.py:12`), which is the mandatory step out of
`PLAN_REJECTED`. So one plan rejection zeroes the ledger the guard reads, and every later
skip is refused on evidence that a round was never frozen while the change carries several.

Consequence in its own right: after any plan rejection, a genuinely wedged producer — API
down, model unavailable — can no longer be skipped, which is the one situation the escape
hatch exists for.

PR#98 already found and fixed this exact confusion on the budget path and left this caller
behind; `count_automatic_rounds`'s docstring names it ("the per-epoch fold resets on every
rejection"). The fix reuses that primitive rather than writing a second fold: the guard's
first arm keys on `count_automatic_rounds(events, reviewer) == 0`.

**The trap this opens.** The guard's second arm reads `execution.rounds[-1]`, and today
relies on the first arm's `not execution.rounds` early return for non-emptiness. Once the
first arm keys on per-change evidence, the combination "rounds on this change, none in this
epoch" reaches the second arm with an empty tuple and raises `IndexError`. The second arm
must carry its own emptiness check. Written down here because introducing a new hole while
closing one is the failure mode this repository has recorded five times.

## Surfaces that state the old rule

The gate's `SUGGESTIONS` for `READY_TO_MERGE` and `AWAITING_CODE_REVIEW`
(`gates/decisions.py`) tell a blocked agent what happened and name no recovery. They are
the first text an agent reads at the moment it is stuck, and they should name the verb.
The agent guidance rendered into `AGENTS.md` from the two agent adapters states the
code-only rule without naming a mechanism for it; `docs/getting-started.md` documents the
code-review-rejection path but not the `READY_TO_MERGE` fold-in.

`docs/cli-reference.md` is derived and is regenerated, not hand-edited.

## Test anchors

- `implementation reopen` from `READY_TO_MERGE` and from `PLAN_REJECTED` reaches
  `IMPLEMENTATION_IN_PROGRESS`; the reason lands on the payload.
- It refuses, appending nothing, when `plan_approved` is absent, and again when
  `implementation_complete` is absent.
- The full loop closes: reopen → edit → `done` → code review → `READY_TO_MERGE`, proving
  the escape does not launder code review.
- The livelock reproduction becomes a test: rounds frozen in an earlier plan epoch, a
  rejection, a re-submit, and `review skip` passes.
- A change with no round ever frozen still refuses — the guard's original purpose.
- The second arm is exercised with per-change rounds and an empty current epoch, which is
  the `IndexError` regression.

## Scope

`src/super_harness/cli/implementation.py`, `src/super_harness/cli/review.py`,
`src/super_harness/gates/decisions.py`, `src/super_harness/adapters/agent/claude_code.py`,
`src/super_harness/adapters/agent/codex.py`, `AGENTS.md`, `docs/cli-reference.md`,
`docs/getting-started.md`, `docs/decisions/d-no-recovery-from-awaiting-code-review.md`
(status → `retired`), `docs/plans/2026-08-11-code-only-recovery.md`,
`tests/unit/cli/test_implementation.py`, `tests/unit/cli/test_review.py`,
`tests/unit/daemon/test_hook_entry.py`, `tests/integration/daemon/test_hook_entry.py`,
`tests/unit/gates/test_decisions.py`.

## Out of scope

A plan-review verdict may name files outside its assigned inspection target. The
plan-reviewer's assignment scope is the plan documents (`engineering/review_contract.py`),
its prompt says to review only the assigned target delta, and nothing validates that a
finding's `file` falls inside that scope — which is how a plan review returned three
source-code findings. Real, and a separate cut: a mechanical refusal would silence
legitimate findings near a fuzzy boundary, so it needs its own measurement. Filed as a
GitHub issue.
