---
id: d-identity-resolution-order
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-26T09:45:28.587373Z'
ratified_text_hash: sha256:383d6b73759791371ded3c9f039e6e5d5ada1f50d0bdb0148e814a4fb1f61951
last_reconciled_by: dawinialo@163.com
last_reconciled_at: '2026-06-26T09:45:28.466002Z'
last_reconcile_kind: self
last_reconcile_justification: 'Baseline arming: invariant holds at HEAD.'
reconciled_anchors:
  src/super_harness/core/identity.py: sha256:bc360ac439e8941426f80b196789f4b5758347a971950d3a438ad0e4595491ac
---
Identity resolution order is fixed: --as > env > git config > "cli".

```review
`resolve_identity` resolves in the fixed precedence override(--as) > env
SUPER_HARNESS_ACTOR > git config user.email > "cli"; first non-empty value (after
strip) wins and the result is always non-empty. On any change to identity.py, confirm
this order is unchanged and no source is added or reordered. Still holds -> `decision
reconcile d-identity-resolution-order`; broken -> `decision betray
d-identity-resolution-order` with a justification.
```
