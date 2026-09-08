---
id: d-dangling-check
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:28.846809Z'
ratified_text_hash: sha256:d1aa4710844e840396baf69320b33fa30565d79879eb8c0c0ab03d6a7f2273b2
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-09-08T15:11:26.726534Z'
last_reconcile_kind: self
last_reconcile_justification: Reviewed the complete Change diff for core/decision_check.py.
  It only renders integrity-violation paths with as_posix(); CheckResult.ok still
  blocks dangling_up and excludes dangling_down, while the CLI continues to report
  dangling_down as warning-only.
reconciled_anchors:
  src/super_harness/core/decision_check.py: sha256:6a3e330fc7094bf5abf5173243ac0458a17c995c18db4bc661c1f5e2a69d66c3
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
