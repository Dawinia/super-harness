---
id: d-state-pure-fold
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-22T19:25:38.517675Z'
ratified_text_hash: sha256:8a106295b2f00df8c9f657c22e02af8cf6b37cee29ee005e772647d62be48ed0
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-07-03T07:50:18.082504Z'
last_reconcile_kind: self
last_reconcile_justification: "F11b: drift-detection timestamp parse extracted to\
  \ the shared core.parse_ts primitive; derive_state remains a pure left-fold \u2014\
  \ constructs and returns a fresh dict, no in-place mutation of inputs/globals, same\
  \ events -> same state (referentially transparent)."
reconciled_anchors:
  src/super_harness/core/reducer.py: sha256:5c63f7f1ff8e28b0f3262d9e67665676895ca7df0a28bc5fb12cb7357c713d67
---
State is a pure left-fold over the event log; never mutated in place.

```review
reducer.derive_state is a pure left-fold over the event log: it constructs and
returns a fresh state and never mutates its inputs or any module-level global in
place (logging is permitted; referential transparency is about the returned value).
On any change to reducer.py, re-review the anchored fold: confirm no in-place
mutation of the accumulator or inputs was introduced and that it stays referentially
transparent (same events -> same state). If it still holds, `decision reconcile
d-state-pure-fold`. If purity was broken (in-place mutation or non-determinism
introduced), do NOT reconcile -- `decision betray d-state-pure-fold` with a
justification instead.
```
