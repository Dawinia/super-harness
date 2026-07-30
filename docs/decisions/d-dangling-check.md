---
id: d-dangling-check
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:28.846809Z'
ratified_text_hash: sha256:d1aa4710844e840396baf69320b33fa30565d79879eb8c0c0ab03d6a7f2273b2
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-30T04:11:21.599187Z'
last_reconcile_kind: self
last_reconcile_justification: 'Re-reviewed after adding a @decision sentinel + explanatory
  comment to decision_check.py for d-pitfall-is-proposed-decision. No logic touched.
  Verified the up=block / down=warn split still holds: cli/decision.py:371 maps dangling_up
  to EXIT_VALIDATION (fail) while :375-376 maps dangling_down to EXIT_OK (warning),
  and CheckResult.ok (core/decision_check.py:67-68) still excludes dangling_down.'
reconciled_anchors:
  src/super_harness/core/decision_check.py: sha256:43afb115bdffaa7c6dec24a19b676bc06c80e9c4cda36a3704205eb5c64b2ce8
---
CI checks referential integrity: dangling-up blocks, dangling-down warns.

```review
The whole-repo referential-integrity check splits by direction: a dangling-up
reference (a decision/doc anchor pointing at code that no longer exists) BLOCKS (exit
2); a dangling-down anchor (code `@decision:` with no live decision) WARNS only. On
any change to decision_check.py, confirm the up=block / down=warn semantics are
preserved. Still holds -> `decision reconcile d-dangling-check`; broken -> `decision
betray d-dangling-check` with a justification.
```
