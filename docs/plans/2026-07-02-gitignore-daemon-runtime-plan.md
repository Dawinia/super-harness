# Implementation plan: 2026-07-02-gitignore-daemon-runtime

Tier hint: **Micro**. Source: #64 dogfood pothole — the daemon's runtime files
under `.harness/` are not in the super-harness managed `.gitignore` block, so
`git add -A` during a lifecycle sweeps them into a commit (#64 attest-verify
caught `.harness/daemon.pid`; fixed there by `git rm --cached` + amend). Root
fix: add the daemon runtime *regular files* to the injector's canonical path
list so every `super-harness init` / `sync --gitignore` ignores them.

v1 (`2026-07-02-gitignore-daemon-pid`) abandoned after plan review: Codex + an
independent Claude subagent BOTH returned REVISE with the same under-fix —
`.harness/daemon.log` is a second daemon regular-file runtime artifact, missing
from the block, surviving in *this* repo only via the incidental `*.log` rule
(`.gitignore` line 24, outside the markers, NOT emitted by `init`/`sync`). This
plan corrects the scope to both files.

TDD throughout. `export PATH="$PWD/.venv/bin:$PATH"` before pytest.

## Scope decision (recorded)

- **Add `.harness/daemon.pid` AND `.harness/daemon.log`.** Both are daemon
  runtime *regular files* under `.harness/` and therefore catchable by
  `git add -A` — the #64 bug class. `daemon.pid`: `cli/daemon.py:102,150`,
  `daemon/server.py:521`, `daemon/supervisor.py:49`. `daemon.log`:
  `daemon/server.py:522`, opened `O_WRONLY|O_CREAT|O_APPEND` at `server.py:471`.
- **Do NOT add `.harness/daemon.sock`.** The UDS socket
  (`daemon/_uds_path.py::resolve_socket_path`, bound at `server.py:193`) is a
  unix-domain socket special file; `git add` never tracks it (git stores only
  regular files + symlinks), so ignoring it solves a problem that cannot occur
  (would be gilding — the project has cut gilding 6× per the ledger).
- Already handled, not touched: `.harness/.state.lock` (`.gitignore:52`,
  explicit, outside markers — a transient flock target); config yamls
  (`policy.yaml`, `verification.yaml`, `derived-docs.yaml`, …) are committed
  config, deliberately absent (existing injector comment).

## Task 1 — add the two daemon runtime files to the canonical path list

**Files:**
- Modify: `tests/unit/engineering/test_gitignore_injector.py` (the test copy of
  `_CANONICAL_PATHS` ~L30–42; add a dedicated regression test)
- Modify: `src/super_harness/engineering/gitignore_injector.py`
  (`_CANONICAL_PATHS` ~L73–85 + the group-1 rationale comment ~L55–72)

**Step 1 — Write the failing test (red).** In
`test_gitignore_injector.py`, add a regression test mirroring the S13
backup-filename test:
```python
def test_block_covers_daemon_runtime_files(tmp_path: Path) -> None:
    """Regression for the #64 dogfood pothole: the daemon runtime regular
    files `.harness/daemon.pid` and `.harness/daemon.log` must be ignored, or a
    `git add -A` during a lifecycle sweeps them into a commit (attest-verify
    then rejects the change as an undeclared file). The UDS socket
    `.harness/daemon.sock` is deliberately NOT listed — git never tracks a
    socket special file, so it cannot be swept."""
    path = tmp_path / ".gitignore"
    inject_gitignore_block(path)
    text = path.read_text()
    assert ".harness/daemon.pid" in text, text
    assert ".harness/daemon.log" in text, text
    assert ".harness/daemon.sock" not in text, "socket is not a git-trackable file"
```
Also add `".harness/daemon.pid"` and `".harness/daemon.log"` to the test-copy
`_CANONICAL_PATHS` tuple, immediately after `.harness/gate-disabled` (end of the
`.harness/` group, before the `.claude/` entries), so the exact-order
`test_block_contains_all_canonical_paths` reflects the intended list.

**Step 2 — Run, verify red.**
Run: `pytest tests/unit/engineering/test_gitignore_injector.py -v`
Expected: `test_block_covers_daemon_runtime_files` FAILS (files not emitted) AND
`test_block_contains_all_canonical_paths` FAILS (test copy now has two extra
lines the source render lacks).

**Step 3 — Implement (green).** In `gitignore_injector.py`, add
`".harness/daemon.pid",` and `".harness/daemon.log",` to `_CANONICAL_PATHS`
after `".harness/gate-disabled",`. Extend the group-1 comment to name the two
daemon runtime files and record why the socket is excluded (regular file vs
unix socket special file).

**Step 4 — Run, verify green.**
Run: `pytest tests/unit/engineering/test_gitignore_injector.py -v`
Expected: all pass EXCEPT `test_committed_repo_gitignore_block_matches_injector`
(still red — the repo's own `.gitignore` block hasn't been re-synced; Task 2).

## Task 2 — re-sync this repo's own managed `.gitignore` block

**Files:** Modify `.gitignore` (regenerated, not hand-edited).

**Step 1 — Regenerate.**
Run: `super-harness sync --gitignore -y`
Re-injects the managed block, adding the two daemon runtime lines.

**Step 2 — Verify.**
Run: `pytest tests/unit/engineering/test_gitignore_injector.py -v` → all green
(`test_committed_repo_gitignore_block_matches_injector` now matches).
Run: `super-harness sync --check` → clean.
Confirm: `.harness/daemon.pid` (and `.harness/daemon.log`, currently caught only
by the generic `*.log`) are now ignored via the managed block; the previously
`?? .harness/daemon.pid` no longer appears in `git status`.

## Task 3 — full verification

- `.venv/bin/python -m pytest` full suite green.
- `ruff check src tests` + `mypy` clean.
- `PYTHONPATH=src lint-imports --config .importlinter --no-cache` KEPT.
- `super-harness decision check` clean (no decisions touched — verified: no
  `reconciled_anchors` on `gitignore_injector.py` or `.gitignore`).
- `super-harness sync --check` clean.

## Declared scope (attest coverage)

- `docs/plans/2026-07-02-gitignore-daemon-runtime-plan.md`
- `src/super_harness/engineering/gitignore_injector.py`
- `tests/unit/engineering/test_gitignore_injector.py`
- `.gitignore`
- `.harness/attestations/2026-07-02-gitignore-daemon-runtime.jsonl`

## Risks / notes

- No behavior change to any runtime path; the injector is only consulted by
  `init` / `sync --gitignore`. Existing repos pick up the new lines on their
  next `sync --gitignore` (idempotent re-inject).
- Block order of the two new lines is cosmetic but must match between source
  `_CANONICAL_PATHS` and the test copy (the exact-equality test).
- `tests/integration/cli/test_init.py::_CANONICAL_GITIGNORE_PATHS` is a
  deliberately partial subset used only for `p in text` presence checks (it
  omits several existing lines too), so it is NOT updated here — adding lines
  keeps its assertions valid, and expanding it to full-parity is out of scope
  (would be an unrelated test-refactor + reslug). Noted per Codex MINOR.
