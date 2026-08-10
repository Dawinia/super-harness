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

### How the two cuts divide that case

Cut 1 keeps a change out of the livelock: at `READY_TO_MERGE`, folding the two minor
findings in no longer requires the `plan redeclare` that started the sequence. Cut 2 gets a
change that is already in `PLAN_REJECTED` out of it, through a disclosed
`review skip --override`. Neither cut alone covers both ends, and Cut 1 deliberately
does not reach into `PLAN_REJECTED` — see the state restriction below for why.

## Cut 1 — `implementation reopen`

A new verb in the existing `implementation` group emits `implementation_invalidated`,
landing the change in `IMPLEMENTATION_IN_PROGRESS`. `--reason` is required and is recorded
on the event payload: this verb voids a review the change already passed, which puts it
in the consequence class of `review authorize`, whose reason is also required.

`implementation_invalidated` rather than `implementation_restarted` because the semantics
fit — the implementation is no longer valid, go back to implementing — and because the
`PLAN_APPROVED` landing would add an `implementation start` step that is pure ceremony for
a change that has already implemented once. The proposed decision names the same event.

**It is allowed from `READY_TO_MERGE` and `AWAITING_CODE_REVIEW`, and nowhere else.** Those
are the two states that mean "the code is written and under, or past, code review", which
is the whole population of the case this change exists for: a finding to fold in. Every
other state either already permits edits (`PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`,
`CODE_REVIEW_REJECTED`), has nothing to reopen (`INTENT_DECLARED`,
`AWAITING_PLAN_REVIEW`), or is terminal.

`PLAN_REJECTED` is deliberately excluded, and that is a correction to an earlier draft of
this plan. Reopening out of a rejection discards the rejection: the change returns to
`IMPLEMENTATION_IN_PROGRESS`, `done` and code review carry it to `READY_TO_MERGE`, and the
merge gate is satisfied by the stale `plan_approved` from the earlier epoch
(`engineering/attestation.py:28,151`). The mechanism cannot tell "the reviewer's findings
were code-level" — the case that motivated this change — from "the reviewer rejected the
plan", so a `PLAN_REJECTED` reopen would make plan rejection advisory for any change that
has implemented once. The exit from a rejection is Cut 2's `review skip --override
--reason`, which emits the same `plan_approved` but stamps `skipped: true`, and is
therefore visible to `report` and the merge attestation. Escaping a rejection should cost
a disclosure; reopening a passed review should not.

That restriction also makes the earlier draft's milestone precondition — the change must
carry `plan_approved` and `implementation_complete` — unnecessary, so it is dropped rather
than kept as belt-and-braces. Both states already imply both events through the transition
table: `AWAITING_CODE_REVIEW` is reachable only by `implementation_complete` from
`IMPLEMENTATION_IN_PROGRESS`, which is reachable only from `PLAN_APPROVED`, which requires
`plan_approved`. A guard that restates what the state already proves is the added structure
this repository keeps paying for.

No one is stranded by the exclusion. `PLAN_REJECTED` is only reachable by a round closing
as rejected, so a rejected change always has a frozen round in its current epoch, which is
exactly what Cut 2's guard asks for.

### What the verb does not do

It does not touch the frozen review round, which is the second open question the proposed
decision raises. It does not need to: `implementation_complete` is the code-reviewer epoch
boundary (`engineering/review_runs.py:13`), so the following `done` opens a fresh epoch and
any round left open by the reopen falls out of `execution.rounds` on its own. Adding
machinery to invalidate it would duplicate a reset the fold already performs.

### It leaves a countable trace

`report` counts human authorizations one record each, because after the TTY check came out
of `review authorize` that count *is* the mechanism — a human who remembers authorizing
twice can falsify a `5` (`engineering/value_report.py:63-68`). A verb that voids a passed
code review needs the same treatment for the same reason, so `report` grows a reopen count
rendering each event's reason. Shipping the required `--reason` without a surface that
displays it would copy the half of `review authorize` that costs something and none of the
half that buys something.

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
rejection").

### The rule the new predicate follows

**A role's skip evidence resets when there is genuinely new material for that role to look
at, and not when the same material is merely re-submitted.** Everything below is that one
sentence applied per role; it is stated first so a later reader can extend the table
without re-deriving it.

| role | resets on | note |
|---|---|---|
| `plan-reviewer` | `plan_redeclared`, `intent_redeclared` | **not** `plan_ready`, its epoch boundary — a `plan_ready` after a rejection is the same plan revised, and the earlier rounds are still evidence the reviewer was asked |
| `code-reviewer` | `implementation_complete`, `plan_redeclared`, `intent_redeclared` | its epoch boundary is kept, because a new `implementation_complete` genuinely is code nobody has reviewed |

Two earlier drafts of this predicate were wrong, both recorded because each looked right:

*Whole change, no boundary at all.* A change re-declared with a wider scope and never sent
to any reviewer would be passed by `review skip`, because its first plan cycle's rounds
still count. Nothing downstream catches that: `verify_attestations` blocks only a skipped
*code* review lacking `--override` (`engineering/attestation.py:283-287`) and
`derive_independence` discloses code review alone, so a bare plan skip emits
`plan_approved` and merges silently.

*Re-declaration alone, for every role.* This one is worse, because Cut 1 creates its
victim. `READY_TO_MERGE` → `implementation reopen` → edit → `done` →
`review skip --reviewer code-reviewer --override` would pass the guard on code-review
rounds frozen **before** the reopen, and land `code_review_passed` on an implementation no
reviewer ever saw. It merges as pass-with-disclosure. The two cuts have to be checked
against each other, not only against the state machine.

**The predicate is scoped to the reviewer role**, filtering `payload["reviewer"]` exactly
as `derive_review_execution` does. Without that filter, plan-review rounds would satisfy a
`review skip --reviewer code-reviewer` on a change where no code-review round ever ran —
the guard's original purpose, defeated by the thing meant to repair it. The role scoping is
free today because it comes from a fold that already filters; it is not free in a predicate
written fresh over the raw stream, which is what this plan describes.

**It counts every frozen round for the role, automatic or human-authorized.** Not inherited
from `count_automatic_rounds`, which filters `payload.get("automatic", True)`: an
implementer mirroring that neighbour would exclude authorized rounds, and a change whose
only rounds since the boundary were authorized is exactly the change that has already hit
the round budget — precisely when a wedged producer needs the escape hatch. The question
this guard asks is "was anyone asked", not "what did it cost".

**All of which is deliberately asymmetric with the round budget, which keeps counting
across every boundary above.** The budget asks what this change has cost, and money spent
stays spent — resetting it on `plan redeclare` would hand back a laundering path PR#98
closed on purpose. Reading one ledger for both questions is what produced this defect.

The new predicate belongs in `engineering/review_runs.py` beside `count_automatic_rounds`,
not inlined in the CLI, so the two folds sit together and their difference is visible at
the point where someone might otherwise unify them.

**The trap this opens.** The guard's second arm reads `execution.rounds[-1]`, and today
relies on the first arm's `not execution.rounds` early return for non-emptiness. Once the
first arm keys on the boundary above, the combination "rounds since the boundary, none in
the current epoch" — which for `plan-reviewer` is precisely the rejected-then-re-submitted
change this cut exists to unblock — reaches the second arm with an empty tuple and raises
`IndexError`. The second arm must carry its own emptiness check. Written down here because
introducing a new hole while closing one is the failure mode this repository has recorded
five times.

## Surfaces that state the old rule

The gate's `SUGGESTIONS` for `READY_TO_MERGE` and `AWAITING_CODE_REVIEW`
(`gates/decisions.py`) tell a blocked agent what happened and name no recovery. They are
the first text an agent reads at the moment it is stuck, and they should name the verb.
The agent guidance rendered into `AGENTS.md` from the two agent adapters states the
code-only rule without naming a mechanism for it; `docs/getting-started.md` documents the
code-review-rejection path but not the `READY_TO_MERGE` fold-in.

`docs/cli-reference.md` is derived and is regenerated, not hand-edited.

## Test anchors

- `implementation reopen` from `READY_TO_MERGE` and from `AWAITING_CODE_REVIEW` reaches
  `IMPLEMENTATION_IN_PROGRESS`; the reason lands on the payload.
- It refuses, appending nothing, from `PLAN_REJECTED` — the state whose exclusion is the
  whole of F3 — and from `INTENT_DECLARED`.
- The full loop closes: reopen → edit → `done` → code review → `READY_TO_MERGE`, proving
  the escape does not launder code review.
- `report` renders the reopen count and each reason.
- The livelock reproduction becomes a test: rounds frozen in an earlier plan epoch, a
  rejection, a re-submit, and `review skip` passes.
- A change re-declared after an earlier plan cycle, with no round frozen since, is still
  refused.
- Reopen → `done` → `review skip --reviewer code-reviewer` is still refused on the fresh
  `implementation_complete`, so Cut 1 cannot walk code past Cut 2's guard.
- Plan-review rounds present and zero code-reviewer rounds: a code-reviewer skip is still
  refused, pinning the role filter — the anchor that distinguishes the two readings a
  reviewer-blind predicate would collapse.
- A human-authorized round, and nothing else, since the boundary still counts as evidence.
- A change with no round ever frozen is still refused — the guard's original purpose.
- The second arm is exercised with rounds since the boundary and an empty current epoch,
  which is the `IndexError` regression.
- The budget keeps counting across a `plan_redeclared`, pinning the asymmetry so a later
  reader cannot unify the two folds without a test turning red.

## Scope

`src/super_harness/cli/implementation.py`, `src/super_harness/cli/review.py`,
`src/super_harness/cli/report.py`, `src/super_harness/engineering/review_runs.py`,
`src/super_harness/engineering/value_report.py`, `src/super_harness/gates/decisions.py`,
`src/super_harness/adapters/agent/claude_code.py`,
`src/super_harness/adapters/agent/codex.py`, `AGENTS.md`, `docs/cli-reference.md`,
`docs/getting-started.md`, `docs/decisions/d-no-recovery-from-awaiting-code-review.md`
(status → `retired`), `docs/plans/2026-08-11-code-only-recovery.md`,
`tests/unit/cli/test_implementation.py`, `tests/unit/cli/test_review.py`,
`tests/unit/cli/test_report.py`, `tests/unit/engineering/test_review_runs.py`,
`tests/unit/engineering/test_value_report.py`, `tests/unit/daemon/test_hook_entry.py`,
`tests/integration/daemon/test_hook_entry.py`, `tests/unit/gates/test_decisions.py`.

## Out of scope

A plan-review verdict may name files outside its assigned inspection target. The
plan-reviewer's assignment scope is the plan documents (`engineering/review_contract.py`),
its prompt says to review only the assigned target delta, and nothing validates that a
finding's `file` falls inside that scope — which is how a plan review returned three
source-code findings. Real, and a separate cut: a mechanical refusal would silence
legitimate findings near a fuzzy boundary, so it needs its own measurement. Filed as a
GitHub issue.
