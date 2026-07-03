---
id: d-single-gate-policy
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-02T19:24:14.868643Z'
ratified_text_hash: sha256:99748d9db112f5b8a9b5949d97852d32a008e01405bb6d03247793faf670fd7b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-02T19:24:14.702852Z'
last_reconcile_kind: self
last_reconcile_justification: 'Demoted daemon reader; gate policy now has a single
  in-process reader (PreToolUseGate, shared by super-harness-hook + gate check CLI).
  Invariant holds: no reader forks the table.'
reconciled_anchors:
  src/super_harness/gates/decisions.py: sha256:e30b97614511fb7b495c6da41a1acbcfb9813cfe7b00786f967e9a915c06eb0b
---
Gate policy lives in one literal (gates.decisions); the in-process gate reads it, neither invents nor forks policy.

```review
The gate decision policy lives in one literal (`gates.decisions`,
PRE_TOOL_USE_DECISIONS); the in-process gate (`PreToolUseGate`, shared by the
`super-harness-hook` decision path and the `gate check` CLI) reads this single
table — it does not invent, hardcode, or fork its own per-state allow/block
policy, and no future gate may fork it. On any change to the gate paths, confirm
the reader still defers to this single SSOT. Still holds -> `decision reconcile
d-single-gate-policy`; broken -> `decision betray d-single-gate-policy` with a
justification.
```
