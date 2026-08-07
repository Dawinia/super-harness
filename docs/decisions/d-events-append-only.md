---
id: d-events-append-only
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:08.963090Z'
ratified_text_hash: sha256:8db7275f6ea859c6e8ada8b4e77ec8a954024d45bffcc6a822ede1d62f69254b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-08-07T08:13:42.404431Z'
last_reconcile_kind: self
last_reconcile_justification: "Added review_budget_exceeded to EXTENSION_EVENT_TYPES:\
  \ a new append-only extension event type, state-preserving. The append-only invariant\
  \ is untouched \u2014 no event is mutated, deleted or reordered by this change."
reconciled_anchors:
  src/super_harness/core/events.py: sha256:5a143f7e90c9ecac9cd92d7f0ed9b82859862e0c58d37a3c1dfeacca49d41b6e
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
