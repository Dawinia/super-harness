---
id: d-fixed-transition-matrix
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:27.695262Z'
ratified_text_hash: sha256:bd22ae2dcac7f0630529e2d42457b809858d2ca19e33a4bf870b1bc76b1ba52b
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-08-07T08:13:42.486736Z'
last_reconcile_kind: self
last_reconcile_justification: 'Added review_budget_exceeded to _INFORMATIONAL: the
  matrix gains no new (state, event) edge; the event leaves every non-terminal state
  unchanged, like the other informational signals. docs/state-machine.md is regenerated
  from the machine and doc check enforces it.'
reconciled_anchors:
  src/super_harness/core/transitions.py: sha256:49138bc91ada3df165a526eb1faa6f061345ce83dfb0ccedcdc7679a7b4f2e57
---
State transitions come only from the fixed declared matrix; no ad-hoc transition.

```review
Every state transition is looked up in the fixed declared `_TRANSITIONS` matrix (via
compute_target_state); no module computes or assigns a change's state through an
ad-hoc transition outside this table. On any change to transitions.py, confirm the
matrix remains the sole source of transitions and no hardcoded/ad-hoc state change
was introduced elsewhere. Still holds -> `decision reconcile d-fixed-transition-matrix`;
broken -> `decision betray d-fixed-transition-matrix` with a justification.
```
