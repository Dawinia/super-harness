---
id: d-events-append-only
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:08.963090Z'
ratified_text_hash: sha256:8db7275f6ea859c6e8ada8b4e77ec8a954024d45bffcc6a822ede1d62f69254b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-02T09:30:30.377328Z'
last_reconcile_kind: self
last_reconcile_justification: 'P0 gate-soundness (F3): parse_event_line gains a timestamp
  str-ness SHAPE check (read-side reject->tolerant skip) and EventWriter.emit gains
  a pre-append type-only guard. Both reject BEFORE append; no path mutates/truncates
  existing events; state remains a derived fold.'
reconciled_anchors:
  src/super_harness/core/events.py: sha256:f81798343c634adad7a8aa0333b7b5fb5cfd17378eed45d48fae3d11ec60e565
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
