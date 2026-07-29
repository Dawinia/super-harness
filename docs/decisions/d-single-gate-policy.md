---
id: d-single-gate-policy
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-29T13:13:19.966360Z'
ratified_text_hash: sha256:4439bb9c58e597db9970c1757b75c567d644fe8d38270cefedb882cf873c1f79
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-29T13:13:20.212866Z'
last_reconcile_kind: self
last_reconcile_justification: 'Gate policy is now four literals in gates.decisions
  (PRE_TOOL_USE_DECISIONS, PLAN_ARTIFACT_ALLOW_STATES, PLAN_PATH_ALLOW_STATES, SCRATCH_ROOT).
  PreToolUseGate reads all four and forks none; cli/gate.py and hook_entry both go
  through the shared patterns_for_state helper so the two readers cannot disagree.
  Each narrowing turns a block into a specific allow and none widens to source: plan-path
  requires a {slug}-bearing pattern AND a resolved .md suffix, scratch is a per-change
  prefix that is gitignored and never reviewed. Allow-state sets verified disjoint
  by test.'
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
stay disjoint, so branch ordering cannot change a verdict — asserted by
`test_allow_state_sets_are_disjoint`; (b) allowances are hard-coded path whitelists and
are NEVER derived from gitignore status — see `d-gate-governs-git-product`.

Confirm the reader still defers to this single SSOT module. Still holds ->
`decision reconcile d-single-gate-policy`; broken -> `decision betray
d-single-gate-policy` with a justification.
```
