---
id: d-decision-records
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:29.090702Z'
ratified_text_hash: sha256:b2ee29066411206363cb90de2251e44e17d4f53bca0c4d4018fc9dd538f4badd
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-01T06:36:43.994419Z'
last_reconcile_kind: self
last_reconcile_justification: "Added an orthogonal optional authoring_time bool field\
  \ (frontmatter parse + serialize); one-file-per-record model and four-state lifecycle\
  \ are untouched \u2014 no multi-record file, no fifth state."
reconciled_anchors:
  src/super_harness/core/decisions.py: sha256:a3405ab59928bdf0f6c768f029dbd6fd76bea576428d9c7abee983cb1c8d0c98
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
