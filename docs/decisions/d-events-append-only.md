---
id: d-events-append-only
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:08.963090Z'
ratified_text_hash: sha256:8db7275f6ea859c6e8ada8b4e77ec8a954024d45bffcc6a822ede1d62f69254b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-09-13T15:51:05.609826Z'
last_reconcile_kind: self
last_reconcile_justification: 'Re-reviewed EventWriter and Event: writes remain append-only
  serialized lines under the exclusive lock with fsync; historical replay is explicit
  and does not mutate or reorder existing events.'
reconciled_anchors:
  src/super_harness/core/events.py: sha256:84a7262fe7689cd2d3f0be78e110eb2699140b74cdc1345748bc6bec5b5eb585
  src/super_harness/core/writer.py: sha256:2ced0979ac6cefdf3c4ff9071532e5fba8149c986048a54f4474cd3c7385c11c
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
