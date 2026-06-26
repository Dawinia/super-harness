---
id: d-single-gate-policy
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:28.283402Z'
ratified_text_hash: sha256:eb0b699fcbf282baf572bca5b20e0c5f79d8a7ae28065401ffd818ba344c034e
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-06-26T09:45:27.960212Z'
last_reconcile_kind: self
last_reconcile_justification: 'Baseline arming: invariant holds at HEAD.'
reconciled_anchors:
  src/super_harness/gates/decisions.py: sha256:419e2f157a97b5f8c7b3c434055f1a68611538d9b3925c00c88137920c429eae
---
Gate policy lives in one literal (gates.decisions); daemon + in-process gate both read it, neither invents policy.

```review
The gate decision policy lives in one literal (`gates.decisions`,
PRE_TOOL_USE_DECISIONS); both the daemon hook path and the in-process gate read this
single table — neither invents, hardcodes, or forks its own per-state allow/block
policy. On any change to the gate paths, confirm both readers still defer to this
single SSOT. Still holds -> `decision reconcile d-single-gate-policy`; broken ->
`decision betray d-single-gate-policy` with a justification.
```
