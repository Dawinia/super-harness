---
id: d-single-gate-policy
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-16T10:49:45.395351Z'
ratified_text_hash: sha256:1d3bc7329391ae944081317e1adf23be23386c8dd43951715dca60cd3afbc265
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-16T10:49:57.138752Z'
last_reconcile_kind: self
last_reconcile_justification: Gate policy now = PRE_TOOL_USE_DECISIONS + PLAN_ARTIFACT_ALLOW_STATES,
  both in gates.decisions (one module). PreToolUseGate reads both; it does not fork
  or invent policy. The carve-out only narrows PLAN_REJECTED's block to a validated
  marked-.md plan artifact, never widens to source. Invariant (no reader forks the
  SSOT module) holds.
reconciled_anchors:
  src/super_harness/gates/decisions.py: sha256:f5c1ce6ab178b833da19632fbe57fea858186de32730a08e400e5ece8fa31c82
---
Gate policy lives in one module (gates.decisions); the in-process gate reads it, neither invents nor forks policy.

```review
The gate decision policy lives in one module (`gates.decisions`): the per-state
`PRE_TOOL_USE_DECISIONS` matrix plus `PLAN_ARTIFACT_ALLOW_STATES` (the PLAN_REJECTED
plan-artifact narrowing — HG-PLAN-AUTHORING). The in-process gate (`PreToolUseGate`,
shared by the `super-harness-hook` decision path and the `gate check` CLI) reads
these declarations — it does not invent, hardcode, or fork its own per-state or
per-path allow/block policy, and no future gate may fork them. The carve-out only
NARROWS a `block` state to a validated, marked-`.md` plan-artifact allow; it never
widens to source. On any change to the gate paths, confirm the reader still defers
to this single SSOT module. Still holds -> `decision reconcile d-single-gate-policy`;
broken -> `decision betray d-single-gate-policy` with a justification.
```
