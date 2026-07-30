---
id: d-pitfall-is-proposed-decision
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-30T07:05:06.458059Z'
ratified_text_hash: sha256:d18656cdb3b6adddba986e3c531f6b662ebf9bbc7483f2fd02e28bb10f58a75d
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-30T07:05:06.680046Z'
last_reconcile_kind: self
last_reconcile_justification: "Re-ratified after the first code review. Anchor moved\
  \ to the ratified set comprehension in core/decision_check.py \u2014 the line whose\
  \ result drives dangling_down, effective_ratified and the tier-2 suspect/unreconciled\
  \ loops, and therefore the line that makes a proposed record free to file. The body-hash\
  \ integrity filter it previously sat on is redundant for a proposed record (no ratified_text_hash)\
  \ and carries no invariant. Body also drops the unresolved backticked identifier\
  \ that tripped doc refs --gate, and qualifies the gates-nothing claim: filing is\
  \ free, anchoring is dangling-up."
reconciled_anchors:
  src/super_harness/core/decision_check.py: sha256:dae62495ed71c1ca3bb45c2da107ba78a12241b0cef14880c4ff15660abc4754
---
Negative knowledge is recorded as a proposed decision record; super-harness grows no parallel pitfall corpus.

"Don't do this / here be dragons / doing X also requires Y" is recorded with
`decision new`, which creates a `status: proposed` record. That state is the vessel:
filing one is free — it is absent from the `ratified` set comprehension in
`core/decision_check.py` that drives every verdict, and it stays out of the tier
tally — and unlike a note it has an exit: `ratify` once the rule can be stated,
`retire` once it stops being true. There is no parallel pitfall directory, no pitfall
record type, and no hand-maintained maturity labels or usage counters.

**Filing is free; anchoring is not.** `dangling_up` is computed against the ratified set,
so a `@decision:` sentinel naming a proposed id is a dangling-up reference and a hard CI
failure. Do not anchor a record until it is ratified. The unqualified form of the claim
above — "a proposed record cannot fail CI" — is false, and stating it that way sends an
agent that files a trap and then anchors it at the site it describes, which is standing
practice here, into a failure the guidance called impossible.

The field evidence is why: standalone lessons-learned repositories have a documented
failure record (NASA LLIS, OIG IG-12-012), while every surviving form imposes an entry
condition — an executable detector (Clippy), a subject stable enough not to move
(PostgreSQL's "Don't Do This"), or a named replacement (AntiPatterns). A decision
record can carry all three and adds two they lack: a lifecycle that retires it, and
anchors that mark it suspect when the code it describes changes.

**Ceilings, recorded deliberately.**

(a) *The mechanism ceiling.* This record cannot be armed with a check, and that is a
property of the tooling rather than a shortfall here. A `counterexample` can only
*add* a file, so a check can only ever assert "no file contains or creates X". The
invariant that actually matters — that a `proposed` record does not gate — is
behavioural, cannot be bite-tested by adding a file, and an un-bite-tested check is
exactly what `ratify` refuses. Hence tier-2, whose anchor catches the failure a check
could not.

(b) *`core/decisions.py` is deliberately un-anchored.* It holds the four-state
lifecycle and the `decision_tier` ladder, but it is already anchored by
`d-decision-records`, whose subject *is* that shape. A second anchor on the same file
would spend reconcile budget without raising a signal the first one does not.

(c) *`cli/decision.py` is deliberately un-anchored.* Its `ratify` accepts `proposed`
(the exit path out of proposed), which is a genuine precondition, but the file churns
for unrelated reasons and anchoring it would spend the reconcile budget on noise.

```review
Negative knowledge is recorded as a proposed decision record; there is no parallel
pitfall directory, no pitfall record type, no hand-maintained usage metadata. The
load-bearing precondition is that filing one is free: the `ratified` set comprehension in
`core/decision_check.py` excludes it, and that set is what drives dangling-down,
the effective-ratified set, and the tier-2 suspect and unreconciled loops.

On any change to `core/decision_check.py`, confirm a proposed record is still absent from
that set and therefore still free to file. If it is not, this decision has become advice
that breaks CI, which is worse than no advice. Confirm too that the anchor sentinel still
sits on that set comprehension and not on the body-hash integrity filter below it — the
latter is redundant for a proposed record, so a sentinel there would point a re-reviewer
at a line carrying no invariant.

Note what this criterion does *not* claim: a proposed record can still fail CI if someone
anchors it, because dangling-up is computed against the ratified set. Filing is free;
anchoring is not.

Still holds -> `decision reconcile d-pitfall-is-proposed-decision`; broken ->
`decision betray d-pitfall-is-proposed-decision` with a justification.
```
