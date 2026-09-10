---
id: d-dangling-check
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:28.846809Z'
ratified_text_hash: sha256:d1aa4710844e840396baf69320b33fa30565d79879eb8c0c0ab03d6a7f2273b2
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-09-08T16:27:54.708425Z'
last_reconcile_kind: self
last_reconcile_justification: 'Re-reviewed after making tier-2 fingerprints line-ending
  invariant. CheckResult.ok and CLI routing are unchanged: dangling_up blocks and
  dangling_down remains warning-only; the fingerprint change only prevents CRLF/LF
  checkout conversion from creating a false suspect result.'
reconciled_anchors:
  src/super_harness/core/decision_check.py: sha256:cc419ccc59ffd42be58b694cbb16fe9ce07d8660a7759c86d13f48f2e12a2ca4
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
