---
id: d-gh-cli-not-rest
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-07-02T03:54:40.718739Z'
ratified_text_hash: sha256:8ee413d6344eef54aeeac4cb18772d060d63e0f28e397f7d7b3d9a859199736c
authoring_time: true
---
GitHub access goes through the gh CLI, never raw REST.

`gh api /repos/...` (REST *through* gh, relative paths, never the host) is allowed.
"Raw REST" = bypassing gh to hit the API host directly. The faithful mechanical
signature of that bypass is naming the host `api.github.com` in source.

```check
! grep -rn 'api\.github\.com' src/
```

```counterexample path=src/super_harness/_ce_raw_rest.py
import requests
requests.get("https://api.github.com/repos/owner/repo")
```
