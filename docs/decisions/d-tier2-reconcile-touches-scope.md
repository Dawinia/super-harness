---
id: d-tier2-reconcile-touches-scope
status: proposed
---
PROPOSED (unsettled): reconciling a tier-2 decision rewrites its own .md, so that file must be in the change's declared scope or the merge gate blocks it.

## What happens

Editing a file anchored by a ratified tier-2 decision makes that decision suspect, and
clearing the suspicion requires `decision reconcile`, which re-stamps
`last_reconciled_*` and `reconciled_anchors` **inside the decision's own `.md`**. That
file is now a changed file. `attest verify` at the merge gate matches changed files
against `scope.files` by **set membership**, so a decision document you never planned
to touch becomes an undeclared change and a merge blocker.

The trap is that the triggering edit can be trivial. Adding one `# @decision:` sentinel
comment to `core/decision_check.py` — no logic touched — made `d-dangling-check`
suspect and forced its `.md` to be rewritten. Hit live while implementing
`d-pitfall-is-proposed-decision`; recovering meant `plan redeclare` plus a full
re-review, because scope cannot be widened from `PLAN_APPROVED`.

## The precondition, stated as a rule

Before declaring scope, ask which ratified tier-2 decisions anchor any file you intend
to edit, and add **those decisions' `.md` files** to `scope.files` alongside the source.
`decision check` names them; `reconciled_anchors` in each decision's frontmatter is the
authoritative list.

## Why this is proposed and not ratified

The rule above is an interim discipline, not the intended end state. The direction is
decided; the implementation is not built yet, so the rule this record states is not the
rule that should be true. Ratifying it would freeze the workaround.

**Decided: `plan ready` warns, naming the exact files.** At `plan ready`, intersect every
ratified tier-2 decision's `reconciled_anchors` keys with the declared `scope.files`; for
each hit whose own `.md` is absent from the scope, warn and name the file(s) to add. Both
sides go through `engineering/attestation.canonical_path`, so the comparison is
spelling-independent. Warning only, never exit 2 — at `plan ready` time the edit that
would trigger the reconcile has not happened yet, so a hard failure would block a
legitimate declaration. The information is already on hand at that moment: the anchors
are authoritative and the scope is declared, so the tool can say precisely what is
missing instead of leaving a human to remember.

**Rejected: have `attest verify` treat a reconcile stamp as implied in-scope.** It reads
as the tidier fix and it is the more dangerous one. Telling a stamp-only change from a
body change requires semantically diffing the decision's frontmatter, which adds a new
trust surface — and with it a laundering vector: disguise a body edit as a stamp edit —
to a merge gate whose entire rule today is "every changed file is in `scope.files`, no
exceptions". That is the same defect family as the symlink-whitewash and forged-state
fail-open holes this project has already had to close. Fail-safe beats fail-open here: a
warning nobody reads leaves today's status quo intact, while an exempted file is a hole.

**Exit:** retire this record when the `plan ready` warning ships.
