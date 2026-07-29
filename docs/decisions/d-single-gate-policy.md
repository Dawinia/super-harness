---
id: d-single-gate-policy
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-29T13:19:06.561089Z'
ratified_text_hash: sha256:c70ba32e4314d0da59b5a5bc844c5995d4b592b7240d81bf6afea764a5eec92b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-29T13:19:07.312252Z'
last_reconcile_kind: self
last_reconcile_justification: 'Re-ratified after rewording only: the body cited a
  test function in backticks, which doc refs --gate correctly flagged as a dead reference
  (tests/ is outside source scope). No policy change from the previous ratification.'
reconciled_anchors:
  src/super_harness/gates/decisions.py: sha256:34ca148dfd266609f5b17a2f03e1475e22e7994716575215725c5d9b8f0a312b
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
