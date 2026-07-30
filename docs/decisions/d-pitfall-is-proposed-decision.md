---
id: d-pitfall-is-proposed-decision
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-30T04:10:34.797598Z'
ratified_text_hash: sha256:31f7297ec814269ec4c92ff450088af2973162e5fbb864918be018ffa9849fe8
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-30T04:10:55.690625Z'
last_reconcile_kind: self
last_reconcile_justification: "Anchor established at the status filter in core/decision_check.py\
  \ that keeps proposed records out of the gate \u2014 the load-bearing precondition\
  \ for this decision."
reconciled_anchors:
  src/super_harness/core/decision_check.py: sha256:43afb115bdffaa7c6dec24a19b676bc06c80e9c4cda36a3704205eb5c64b2ce8
---
Negative knowledge is recorded as a proposed decision record; super-harness grows no parallel pitfall corpus.

"Don't do this / here be dragons / doing X also requires Y" is recorded with
`decision new`, which creates a `status: proposed` record. That state is the vessel:
it gates nothing (`decision check` skips every record that is not ratified), it stays
out of the tier tally, and unlike a note it has an exit — `ratify` once the rule can
be stated, `retire` once it stops being true. There is no `docs/pitfalls/`, no pitfall
record type, and no hand-maintained `maturity` / `ref_count` metadata.

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
Negative knowledge is recorded as a `proposed` decision record; there is no parallel
pitfall corpus (no `docs/pitfalls/`, no pitfall record type, no hand-maintained usage
metadata). The load-bearing precondition is that a proposed record costs nothing:
`core/decision_check.py` skips any record whose status is not `ratified`, so recording
a trap as a proposed decision can neither fail `decision check` nor block CI.

On any change to `core/decision_check.py`, confirm that filter still holds — that a
`proposed` record is still skipped and still cannot gate. If it no longer does, this
decision has become advice that breaks CI, which is worse than no advice.

Still holds -> `decision reconcile d-pitfall-is-proposed-decision`; broken ->
`decision betray d-pitfall-is-proposed-decision` with a justification.
```
