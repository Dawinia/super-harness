---
id: d-no-recovery-from-awaiting-code-review
status: retired
---
PROPOSED (unsettled): AWAITING_CODE_REVIEW freezes decisions and source, and neither implementation_* exit that reaches an editable state has a CLI verb, so the only recovery is plan redeclare into a full plan cycle.

## What happens

`done` emits `implementation_complete` and lands the change in `AWAITING_CODE_REVIEW`,
where the gate freezes both `src/` and `docs/decisions/`. If the review — or a CI gate you
forgot to run — then finds something that needs an edit to either, there is no cheap way
back.

Be precise about the scope of the claim, because a wider version of it is false.
`AWAITING_CODE_REVIEW` has nine exits in `docs/state-machine.md`, and several do have CLI
verbs: `plan_redeclared` from `plan redeclare`, and `code_review_passed` /
`code_review_failed` from the reviewer verdict map. The gap is narrower. Of the three
`implementation_*` exits, only two reach an editable state — `implementation_invalidated`
→ `IMPLEMENTATION_IN_PROGRESS` and `implementation_restarted` → `PLAN_APPROVED`;
`implementation_withdrawn` goes to `READY_TO_MERGE` and is not a recovery path at all —
and **neither of those two is emitted by anything under `src/super_harness/cli/`**.

So the only CLI-reachable recovery rewinds to `INTENT_DECLARED` and costs a full plan
cycle: revise, re-declare scope, re-review to convergence, re-approve. Editing through a
shell instead would be the self-bypass the gate explicitly names, and is not an option.

## The precondition, stated as a rule

**Finish every edit and run every gate before `done`.** Not just the tests:

- `pytest -q`
- `super-harness verify <change>`
- `super-harness decision check`
- `super-harness doc check`
- `super-harness doc refs --gate` — **check its exit code, not its output.** It prints
  findings that read like warnings, but a high-confidence dead reference exits 2. It is a
  separate CI job that `doc check` does not cover, and it is the one most easily missed.

This record exists because that list was learned by paying the full cycle for a one-word
fix: a backticked identifier in a ratified decision body tripped the dead-reference gate
after `done` had already frozen the file.

## Why this is proposed and not ratified

The rule above is a discipline compensating for a missing verb, not the end state. The
likely fix is a CLI verb emitting `implementation_invalidated` — the transition the state
machine already defines and nothing can trigger — which is its own cut with its own
questions (should it require a reason? should it invalidate the frozen review round?).
Ratifying this record would freeze the workaround as the rule.

**Exit:** retire this record when that verb ships.

## Retired 2026-08-11

`super-harness implementation reopen <change> --reason "<why>"` ships that verb
(`cli/implementation.py`), emitting `implementation_invalidated` from
`AWAITING_CODE_REVIEW` / `READY_TO_MERGE`. The two questions this record left open
were answered: the reason is required and counted by `report`, and the frozen round
needs no explicit invalidation because `implementation_complete` is the code-reviewer
epoch boundary, so the following `done` resets it.

The discipline above is still worth following — a reopen costs another review round —
but it is no longer the only thing standing between a frozen change and an edit.
See `docs/plans/2026-08-11-code-only-recovery.md`.
