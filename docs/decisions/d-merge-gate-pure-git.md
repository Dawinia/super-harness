---
id: d-merge-gate-pure-git
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-22T19:17:30.442655Z'
ratified_text_hash: sha256:23cd016fa1a90d3110b13d0d7a4682bae262b8e27b694e10c7b7a57d03a6c911
---
The merge gate verifies committed evidence with pure git — no network, no runtime trust.

The merge gate is `cli/attest.py` (subprocess only for `git diff`) + `engineering/attestation.py`
(pure). Both must stay free of network clients. The check scans exactly those two files —
`gates/` is the PreToolUse policy, not the merge gate, and stays out of scope.

```check
! grep -rnE 'import +(urllib|requests|httpx|socket)|(urllib|requests|httpx|socket)\.[a-zA-Z_]|api\.github\.com' src/super_harness/cli/attest.py src/super_harness/engineering/attestation.py
```

```counterexample path=src/super_harness/cli/attest.py
import urllib.request  # raw network smuggled into the merge gate
```
