---
id: d-events-append-only
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:08.963090Z'
ratified_text_hash: sha256:8db7275f6ea859c6e8ada8b4e77ec8a954024d45bffcc6a822ede1d62f69254b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-08-21T15:50:43.696735Z'
last_reconcile_kind: self
last_reconcile_justification: EventWriter still appends one serialized line with O_APPEND
  and fsync; the cross-platform process lock covers validation plus append, and the
  Windows typing-only adjustment does not mutate, truncate, or reorder events.
reconciled_anchors:
  src/super_harness/core/events.py: sha256:5a143f7e90c9ecac9cd92d7f0ed9b82859862e0c58d37a3c1dfeacca49d41b6e
  src/super_harness/core/writer.py: sha256:90a61715d0daac863c53229bdc9b0cc715f54f990ae3848dacf4cb0874c35bba
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
