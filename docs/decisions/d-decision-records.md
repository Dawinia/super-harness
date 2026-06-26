---
id: d-decision-records
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:29.090702Z'
ratified_text_hash: sha256:b2ee29066411206363cb90de2251e44e17d4f53bca0c4d4018fc9dd538f4badd
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-06-26T09:45:28.988510Z'
last_reconcile_kind: self
last_reconcile_justification: 'Baseline arming: invariant holds at HEAD.'
reconciled_anchors:
  src/super_harness/core/decisions.py: sha256:6ba4253e9448cd2d1bc011863456b6c6fbd350df55cfad1e7e78ec134b7baa5a
---
Decisions are one-file-per-record under docs/decisions/, four-state lifecycle.

```review
Decisions are stored one-file-per-record at docs/decisions/<id>.md with a four-state
lifecycle (proposed -> ratified -> superseded / retired). The loader in decisions.py
enforces this shape; there is no multi-record file and no fifth state. On any change
to decisions.py, confirm the one-file-per-record model and the four-state lifecycle
are preserved. Still holds -> `decision reconcile d-decision-records`; broken ->
`decision betray d-decision-records` with a justification.
```
