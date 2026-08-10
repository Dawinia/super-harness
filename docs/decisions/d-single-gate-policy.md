---
id: d-single-gate-policy
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-29T13:19:06.561089Z'
ratified_text_hash: sha256:c70ba32e4314d0da59b5a5bc844c5995d4b592b7240d81bf6afea764a5eec92b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-08-10T19:21:47.009027Z'
last_reconcile_kind: self
last_reconcile_justification: "Only SUGGESTIONS changed \u2014 the remediation strings\
  \ for READY_TO_MERGE and AWAITING_CODE_REVIEW now name `implementation reopen`.\
  \ None of the four policy literals moved: PRE_TOOL_USE_DECISIONS, PLAN_ARTIFACT_ALLOW_STATES,\
  \ PLAN_PATH_ALLOW_STATES and SCRATCH_ROOT are byte-identical, so the allow-state\
  \ sets are unchanged and still disjoint, no allowance was widened toward source,\
  \ and nothing was derived from gitignore status. SUGGESTIONS is remediation text\
  \ the gate surfaces after a decision it did not influence; a blocked state that\
  \ names no way back is what sent this repo's own changes through plan redeclare\
  \ twice."
reconciled_anchors:
  src/super_harness/gates/decisions.py: sha256:13abcecf68c950be86129ee267321e88fdd7c9386094f44df5892de52b514819
---
Gate policy lives in one module (gates.decisions); the in-process gate reads it, neither invents nor forks policy.

```review
The gate decision policy lives in one module (`gates.decisions`), now in FOUR
literals: the per-state `PRE_TOOL_USE_DECISIONS` matrix, `PLAN_ARTIFACT_ALLOW_STATES`
(the PLAN_REJECTED plan-artifact narrowing — HG-PLAN-AUTHORING), `PLAN_PATH_ALLOW_STATES`
(the INTENT_DECLARED plan-document narrowing), and `SCRATCH_ROOT` (the per-change
scratch prefix, allowed in every state). The in-process gate (`PreToolUseGate`, shared
by the `super-harness-hook` decision path and the `gate check` CLI) reads these
declarations — it does not invent, hardcode, or fork its own per-state or per-path
allow/block policy, and no future gate may fork them.

Every narrowing only ever turns a `block` into a *specific* allow; none widens to
source:
- plan-artifact: a marked `.md` recorded by the reviewed `plan ready` submission;
- plan-path: a path matching an owner-configured pattern from the tracked
  `.harness/plan-paths.yaml`, where each pattern must contain `{slug}` (binding the
  allowance to the active change) and the RESOLVED path must end in `.md`;
- scratch: the `SCRATCH_ROOT/<change_id>/` prefix only — gitignored, never reviewed,
  never merged.

Two invariants a reviewer must re-confirm on any gate change: (a) the allow-state sets
stay disjoint, so branch ordering cannot change a verdict — there is a unit test in
tests/unit/gates asserting exactly this; (b) allowances are hard-coded path whitelists
and are NEVER derived from gitignore status — see the d-gate-governs-git-product record.

Confirm the reader still defers to this single SSOT module. Still holds ->
`decision reconcile d-single-gate-policy`; broken -> `decision betray
d-single-gate-policy` with a justification.
```
