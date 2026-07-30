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

The rule above is a workaround, and it is not settled that it is the right one. Two
candidate resolutions, and this record does not pick between them:

1. **Keep it a discipline.** The author declares the decision documents up front. Cheap
   to state, but it is knowledge the tool already has and the human must remember —
   the shape that reliably gets forgotten, as it was here.
2. **Make the tooling absorb it.** Treat a reconcile stamp on an already-ratified
   decision as an implied in-scope artifact, the way `plan_artifacts` already carves
   out plan documents, or have `plan ready` warn when the declared scope touches an
   anchored file without declaring the anchoring decision.

Option 2 is more likely correct — it removes the class rather than documenting it — but
it changes merge-gate semantics and deserves its own cut. Until that is decided, this
record exists so the next person does not rediscover the blocker at the merge gate.
Ratify it if the discipline is chosen; retire it if the tooling absorbs the case.
