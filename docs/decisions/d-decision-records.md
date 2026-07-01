---
id: d-decision-records
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:29.090702Z'
ratified_text_hash: sha256:b2ee29066411206363cb90de2251e44e17d4f53bca0c4d4018fc9dd538f4badd
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-01T07:50:10.176791Z'
last_reconcile_kind: self
last_reconcile_justification: "decisions.py further changed (STRICT authoring_time\
  \ parse: is True). Still one-file-per-record + four-state lifecycle \u2014 no multi-record\
  \ file, no fifth state; the change is orthogonal parse strictness."
reconciled_anchors:
  src/super_harness/core/decisions.py: sha256:46d4cec0cb2293dffde7364fc73cd22714eed9680ea492302c91e77c10af7e4e
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
