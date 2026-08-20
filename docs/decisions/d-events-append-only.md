---
id: d-events-append-only
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:08.963090Z'
ratified_text_hash: sha256:8db7275f6ea859c6e8ada8b4e77ec8a954024d45bffcc6a822ede1d62f69254b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-08-20T18:04:10.758833Z'
last_reconcile_kind: self
last_reconcile_justification: EventWriter still appends exactly one serialized line
  with O_APPEND and fsync; the new cross-platform process lock continues to cover
  validate plus append and never mutates, truncates, or reorders existing events.
reconciled_anchors:
  src/super_harness/core/events.py: sha256:1546604b3ce6f19963b31458ac87e8ed10910259279de11e9c8f962b132266b1
  src/super_harness/core/writer.py: sha256:17198fe97d1808af54b6617e60581fefe1f2cc3e46d8d88c758d6483c001586d
---
Events are append-only; the log is the source of truth, state is derived.

```review
The Event dataclass is frozen and events.jsonl is append-only: events are appended
and never edited, reordered, or truncated in place; the log stays the single source
of truth and all state is a derived fold over it (see d-state-pure-fold), never
persisted as the authority. On any change to the Event model or the event writer,
confirm no path mutates/truncates an existing event and that state remains
log-derived. Still holds -> `decision reconcile d-events-append-only`; broken ->
`decision betray d-events-append-only` with a justification.
```
