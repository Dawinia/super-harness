---
id: d-events-append-only
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:08.963090Z'
ratified_text_hash: sha256:8db7275f6ea859c6e8ada8b4e77ec8a954024d45bffcc6a822ede1d62f69254b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-09T07:46:13.022148Z'
last_reconcile_kind: independent
last_reconcile_justification: Multi-independent reviewer gate adds a new event type
  only; events remain append-only and state remains derived from the log.
reconciled_anchors:
  src/super_harness/core/events.py: sha256:f54e5c014c1523e74a39de05f90b89ecdae1e4749f16fc819953eb9fb24f142f
  src/super_harness/core/writer.py: sha256:b2c1cf24862e9473fe14d50e70cfc675189f4c61679579f506a55e6975d3b8bf
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
