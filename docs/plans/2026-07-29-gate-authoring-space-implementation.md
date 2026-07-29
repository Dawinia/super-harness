---
change: 2026-07-29-gate-authoring-space-v2
stage: plan
---

# Gate authoring space — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let a change author its plan documents and scratch notes through normal
editing tools inside gated lifecycle states, without widening the gate for source.

**Architecture:** Two narrowings are added to the single gate policy module and read
by the one pure `PreToolUseGate`. (1) A **scratch whitelist** — `.harness/scratch/<slug>/**`
is allowed in every state, because it never enters git. (2) A **plan-path allowance** —
in `INTENT_DECLARED` only, a path matching an owner-configured pattern from the new
tracked `.harness/plan-paths.yaml` is allowed. Every pattern must contain `{slug}` and
resolve to `.md`, so the allowance is structurally bound to the active change and can
never name a product file. The `PLAN_REJECTED` `plan_artifacts` carve-out is untouched.

**Tech Stack:** Python 3.10+, PyYAML, `fnmatch` (matching this repo's existing glob
convention in `core/anchor_scanner.py:45`), pytest.

**Design doc:** `docs/plans/2026-07-29-gate-authoring-space-design.md`

---

## Decisions this plan locks (do not re-open during execution)

| # | Decision | Why |
|---|---|---|
| D1 | Plan-path allowance fires **only in `INTENT_DECLARED`** | `PLAN_REJECTED` already has the `plan_artifacts` mechanism whose "full replacement on each `plan_ready` = revoke" semantics would be diluted by a second, pattern-based source. Two non-overlapping mechanisms keep both existing proofs intact. |
| D2 | **No `change start --plan` flag** | That value would be supplied by the governed agent at `change start` — self-declared identity, the exact thing rejected in design §Design/1. The tracked config file already covers per-repo layout, and editing it is itself a gated edit. |
| D3 | Config loader is **fail-CLOSED** (unlike `core/source_scope.py`) | `source_scope` degrades to permissive defaults because a typo there must not brick doc scanning. Here a corrupt file degrading to the default would *grant* an allowance the owner may have narrowed. Corrupt/missing-key → `[]` → nothing allowed → the state table blocks, i.e. today's behaviour. A **missing file** is different: it means "never configured" → the built-in default applies. |
| D4 | Matching via `fnmatch.fnmatchcase` on the POSIX repo-relative path | Consistent with `anchor_scanner`. **`fnmatch` is not glob**: `*` crosses `/` and `**` carries no recursive meaning. Both consequences are load-bearing — see the measurements below the table. Not worth a bespoke segment-aware matcher (YAGNI). |
| D5 | Scratch dir is `.harness/scratch/<slug>/`, compared **after** `canonical_relpath` | `canonical_relpath` resolves `..` and symlinks before the gate sees the path, so `.harness/scratch/x/../../gate-disabled` resolves to `.harness/gate-disabled`, fails the prefix test, and blocks. Same defence #85 used against symlink laundering. |

### `fnmatch` semantics — measured, because they cut both ways

```
openspec/changes/<slug>/**/*.md  vs  openspec/changes/<slug>/proposal.md   -> False
openspec/changes/<slug>/**/*.md  vs  openspec/changes/<slug>/specs/a.md    -> True
openspec/changes/<slug>/*.md     vs  openspec/changes/<slug>/proposal.md   -> True
openspec/changes/<slug>/*.md     vs  openspec/changes/<slug>/specs/a.md    -> True
```

- **Looser than glob**: `*` spans `/`, so `docs/plans/*{slug}*.md` also matches
  `docs/plans/sub/x-<slug>-y.md`. Harmless — still under `docs/plans/`, still
  contains the slug, still `.md`.
- **Stricter than glob, and this one bites**: a glob-style `**/` segment demands a
  literal extra `/`, so `openspec/changes/{slug}/**/*.md` would **miss
  `proposal.md` and `tasks.md`** — precisely the two files the OpenSpec adapter
  watches. Every pattern in this plan therefore uses a single `*`, never `**`.
  Write `openspec/changes/{slug}/*.md`; because `*` spans `/`, it covers the
  nested `specs/` layout too.

---

## Task 1: `plan-paths.yaml` loader (pure, fail-closed)

**Files:**
- Create: `src/super_harness/core/plan_paths.py`
- Test: `tests/unit/core/test_plan_paths.py`

**Step 1: Write the failing tests**

```python
# tests/unit/core/test_plan_paths.py
from pathlib import Path

from super_harness.core.plan_paths import DEFAULT_PLAN_PATHS, load_plan_paths


def _write(root: Path, body: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "plan-paths.yaml").write_text(body, encoding="utf-8")


def test_missing_file_uses_builtin_default(tmp_path):
    (tmp_path / ".harness").mkdir()
    assert load_plan_paths(tmp_path) == list(DEFAULT_PLAN_PATHS)


def test_valid_patterns_are_returned(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "specs/{slug}/design.md"\n')
    assert load_plan_paths(tmp_path) == ["specs/{slug}/design.md"]


def test_pattern_without_slug_placeholder_is_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "AGENTS.md"\n  - "docs/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == ["docs/{slug}.md"]


def test_pattern_not_ending_in_md_is_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "src/{slug}/**"\n')
    assert load_plan_paths(tmp_path) == []


def test_absolute_and_traversal_patterns_are_dropped(tmp_path):
    _write(
        tmp_path,
        'version: 1\nplan_paths:\n  - "/etc/{slug}.md"\n  - "../{slug}.md"\n',
    )
    assert load_plan_paths(tmp_path) == []


def test_corrupt_yaml_fails_closed_to_empty(tmp_path):
    _write(tmp_path, "plan_paths: [unclosed\n")
    assert load_plan_paths(tmp_path) == []


def test_non_list_value_fails_closed_to_empty(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths: "docs/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == []


def test_non_string_entries_are_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - 42\n  - "docs/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == ["docs/{slug}.md"]


def test_never_raises_on_unreadable_file(tmp_path, monkeypatch):
    _write(tmp_path, 'version: 1\nplan_paths: ["docs/{slug}.md"]\n')
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    assert load_plan_paths(tmp_path) == []
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/core/test_plan_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'super_harness.core.plan_paths'`

**Step 3: Write the implementation**

```python
# src/super_harness/core/plan_paths.py
"""Loader for ``.harness/plan-paths.yaml`` — the owner-controlled patterns naming
where a change's plan documents live (design 2026-07-29).

Fail-CLOSED, deliberately unlike `core/source_scope.py`: this list *grants* a gate
allowance, so a corrupt or malformed file yields `[]` (nothing allowed → the state
table blocks, today's behaviour) rather than falling back to a permissive default
the owner may have narrowed away. A **missing** file is not corruption — it means
"never configured" — and gets the built-in default.

Two guard rails are enforced here, not at the gate, so an invalid pattern can never
reach the decision path:

1. every pattern must contain the literal ``{slug}`` — this is what binds the
   allowance to the active change; without it a pattern could name `AGENTS.md`,
   `README.md`, or `**/*.md`;
2. every pattern must end in ``.md`` (case-insensitive) and be a relative path with
   no ``..`` segment.

The gate re-checks the `.md` suffix on the *resolved* path afterwards — the pattern
check here cannot see through a symlink.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

# Matches this repo's own convention: `<date>-<slug>-<suffix>.md`, and one change
# routinely has both a `-design.md` and an `-implementation.md`, so the slug sits in
# the middle and an exact `{slug}.md` would match none of them.
DEFAULT_PLAN_PATHS: tuple[str, ...] = ("docs/plans/*{slug}*.md",)

SLUG_PLACEHOLDER = "{slug}"


def plan_paths_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "plan-paths.yaml"


def _is_valid_pattern(pattern: object) -> bool:
    if not isinstance(pattern, str) or not pattern:
        return False
    if SLUG_PLACEHOLDER not in pattern:
        return False
    if not pattern.lower().endswith(".md"):
        return False
    pp = PurePosixPath(pattern)
    return not pp.is_absolute() and ".." not in pp.parts


def load_plan_paths(workspace_root: Path) -> list[str]:
    """Return the validated plan-path patterns. NEVER raises.

    Missing file → built-in default. Anything else that is not a well-formed list of
    valid patterns → `[]` (fail-closed).
    """
    f = plan_paths_file(workspace_root)
    try:
        if not f.is_file():
            return list(DEFAULT_PLAN_PATHS)
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("plan_paths")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if _is_valid_pattern(p)]
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/core/test_plan_paths.py -v`
Expected: PASS (9 passed)

**Step 5: Commit**

```bash
git add src/super_harness/core/plan_paths.py tests/unit/core/test_plan_paths.py
git commit -m "feat(plan-paths): fail-closed loader for owner-configured plan document patterns"
```

---

## Task 2: Gate policy constants

**Files:**
- Modify: `src/super_harness/gates/decisions.py:52` (after `PLAN_ARTIFACT_ALLOW_STATES`)
- Test: `tests/unit/gates/test_decisions.py`

The gate policy must stay in ONE module — `d-single-gate-policy` is a ratified tier-1
decision anchored at `gates/decisions.py`. Adding the constants here (not in the gate)
is what keeps that decision true. Its *body text* still needs updating in Task 8.

**Step 1: Write the failing test**

```python
# append to tests/unit/gates/test_decisions.py
from super_harness.gates.decisions import (
    PLAN_PATH_ALLOW_STATES,
    SCRATCH_ROOT,
)


def test_plan_path_allow_states_is_intent_declared_only():
    # D1: PLAN_REJECTED keeps the plan_artifacts mechanism; the two never overlap.
    assert PLAN_PATH_ALLOW_STATES == frozenset({"INTENT_DECLARED"})


def test_scratch_root_is_under_harness_and_posix():
    assert SCRATCH_ROOT == ".harness/scratch"
    assert "\\" not in SCRATCH_ROOT
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/gates/test_decisions.py -v`
Expected: FAIL — `ImportError: cannot import name 'PLAN_PATH_ALLOW_STATES'`

**Step 3: Implement**

Add to `src/super_harness/gates/decisions.py`, after `PLAN_ARTIFACT_ALLOW_STATES`, and
extend `__all__`:

```python
# States whose default is `block` but where an edit to a path matching the owner's
# configured plan-path patterns (`.harness/plan-paths.yaml`, validated in
# `core.plan_paths`) is ALLOWED. INTENT_DECLARED only, by design: it is the state with
# no recorded `plan_artifacts` yet (nothing to narrow to), and it is where first
# authoring happens. PLAN_REJECTED deliberately does NOT appear here — it already has
# the `plan_artifacts` carve-out above, whose "replaced wholesale on each plan_ready"
# revocation semantics a second pattern-based source would dilute.
# @decision:d-single-gate-policy
PLAN_PATH_ALLOW_STATES: frozenset[str] = frozenset({"INTENT_DECLARED"})

# Per-change scratch area, allowed in EVERY state (including terminal ones). It is
# gitignored, never enters a review bundle, and never reaches a merge gate — blocking
# it prevents nothing and only pushes the agent toward the shell. Allowing it
# unconditionally is what lets the gate keep one rule ("the gate governs files that
# will enter git as product") instead of a second per-state table. The gate appends
# `/<change_id>/` and compares against the CANONICALIZED path, so `..` and symlink
# escapes out of this prefix resolve elsewhere and block.
# @decision:d-single-gate-policy
SCRATCH_ROOT: str = ".harness/scratch"
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/gates/test_decisions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/super_harness/gates/decisions.py tests/unit/gates/test_decisions.py
git commit -m "feat(gate): declare plan-path + scratch allowances in the single policy module"
```

---

## Task 3: Scratch-area allowance in the gate

**Files:**
- Modify: `src/super_harness/gates/pre_tool_use.py:60` (before the existing carve-out)
- Test: `tests/unit/gates/test_pre_tool_use.py`

**Step 1: Write the failing tests**

```python
# append to tests/unit/gates/test_pre_tool_use.py
import pytest

from super_harness.core.state import ChangeState
from super_harness.gates import GateDecision, ProposedAction
from super_harness.gates.pre_tool_use import PreToolUseGate


def _state(current: str, change_id: str = "my-change") -> ChangeState:
    return ChangeState(change_id=change_id, current_state=current)


def _decide(state, resolved, **kw):
    return PreToolUseGate(**kw).decide(
        ProposedAction(kind="edit", file=resolved, resolved_path=resolved), state, []
    )


@pytest.mark.parametrize(
    "current",
    [
        "INTENT_DECLARED",
        "AWAITING_PLAN_REVIEW",
        "PLAN_REJECTED",
        "AWAITING_CODE_REVIEW",
        "READY_TO_MERGE",
        "ARCHIVED",
        "ABANDONED",
    ],
)
def test_scratch_area_allowed_in_every_blocking_state(current):
    r = _decide(_state(current), ".harness/scratch/my-change/notes.md")
    assert r.decision is GateDecision.ALLOW


def test_scratch_area_allows_any_extension():
    # It never enters git, so there is no reason to restrict it to .md.
    r = _decide(_state("READY_TO_MERGE"), ".harness/scratch/my-change/probe.py")
    assert r.decision is GateDecision.ALLOW


def test_other_changes_scratch_is_blocked():
    r = _decide(_state("INTENT_DECLARED"), ".harness/scratch/other-change/notes.md")
    assert r.decision is GateDecision.BLOCK


def test_scratch_sibling_prefix_is_not_a_match():
    # `.harness/scratch/my-change-evil/` must NOT satisfy the `my-change` prefix.
    r = _decide(_state("INTENT_DECLARED"), ".harness/scratch/my-change-evil/x.md")
    assert r.decision is GateDecision.BLOCK


def test_scratch_bare_directory_path_is_blocked():
    r = _decide(_state("INTENT_DECLARED"), ".harness/scratch/my-change")
    assert r.decision is GateDecision.BLOCK


def test_gate_disabled_path_is_never_allowed_via_scratch():
    # Post-canonicalization the traversal has already resolved; the gate sees the
    # real target and must block it.
    r = _decide(_state("INTENT_DECLARED"), ".harness/gate-disabled")
    assert r.decision is GateDecision.BLOCK
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/gates/test_pre_tool_use.py -v -k scratch`
Expected: FAIL — the blocking-state cases return BLOCK

**Step 3: Implement**

In `src/super_harness/gates/pre_tool_use.py`, import `SCRATCH_ROOT` and insert
immediately after `rp = action.resolved_path` (before the plan-artifact carve-out):

```python
        # Scratch-area allowance (design 2026-07-29): the change's own scratch dir is
        # allowed in EVERY state. `rp` is already canonicalized by the caller, so a
        # `..`/symlink escape has resolved to its real target and fails this prefix.
        # The trailing `/` makes the test segment-aware: `.harness/scratch/my-change`
        # must not admit `.harness/scratch/my-change-evil/x`.
        if rp and state.change_id:
            scratch_prefix = f"{SCRATCH_ROOT}/{state.change_id}/"
            if rp.startswith(scratch_prefix):
                return GateResult(
                    decision=GateDecision.ALLOW,
                    reason=f"{state.current_state}: scratch area ({rp})",
                )
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/gates/test_pre_tool_use.py -v`
Expected: PASS (all pre-existing tests still green)

**Step 5: Commit**

```bash
git add src/super_harness/gates/pre_tool_use.py tests/unit/gates/test_pre_tool_use.py
git commit -m "feat(gate): allow the change's scratch area in every lifecycle state"
```

---

## Task 4: Plan-path allowance in the gate

**Files:**
- Modify: `src/super_harness/gates/pre_tool_use.py` (constructor + after the scratch block)
- Test: `tests/unit/gates/test_pre_tool_use.py`

The gate stays pure: patterns are injected at construction, never read from disk here.

**Step 1: Write the failing tests**

```python
# append to tests/unit/gates/test_pre_tool_use.py
PATTERNS = ["docs/plans/*{slug}*.md"]


def test_plan_path_allowed_in_intent_declared():
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.ALLOW


def test_plan_path_not_allowed_in_awaiting_plan_review():
    # D1 + design non-goal: the reviewer's frozen target must not move.
    r = _decide(
        _state("AWAITING_PLAN_REVIEW"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_plan_path_not_allowed_in_plan_rejected():
    # D1: PLAN_REJECTED keeps using the recorded plan_artifacts list only.
    r = _decide(
        _state("PLAN_REJECTED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_plan_path_for_a_different_slug_is_blocked():
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-other-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_non_md_resolved_path_is_blocked_even_if_pattern_matches():
    # Symlink laundering: `docs/plans/x-my-change.md` -> `src/evil.py` canonicalizes
    # to the .py, which must fail the post-resolution suffix check.
    r = _decide(
        _state("INTENT_DECLARED"), "src/evil.py", plan_path_patterns=PATTERNS
    )
    assert r.decision is GateDecision.BLOCK


def test_no_patterns_configured_blocks_as_before():
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=[],
    )
    assert r.decision is GateDecision.BLOCK


def test_forged_non_list_patterns_block_cleanly():
    # Defence in depth: a non-list must not raise (the hook would fail-open).
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns="docs/plans/*{slug}*.md",  # type: ignore[arg-type]
    )
    assert r.decision is GateDecision.BLOCK


def test_source_file_never_allowed_in_intent_declared():
    r = _decide(
        _state("INTENT_DECLARED"), "src/api.py", plan_path_patterns=PATTERNS
    )
    assert r.decision is GateDecision.BLOCK


def test_forged_glob_metachars_in_change_id_do_not_widen_the_pattern():
    """`change_id` comes from the gitignored state.yaml, which the agent can write
    in any ALLOW state. A `*` in it must NOT turn `docs/plans/*{slug}*.md` into a
    match for every plan document."""
    r = _decide(
        _state("INTENT_DECLARED", change_id="*"),
        "docs/plans/2026-07-29-somebody-elses-plan.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_forged_bracket_class_in_change_id_is_literal():
    r = _decide(
        _state("INTENT_DECLARED", change_id="[a-z]"),
        "docs/plans/x-a-y.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_literal_metachar_slug_still_matches_its_own_document():
    """Escaping must not break the legitimate case: a change_id containing a
    metachar still matches the file literally named after it."""
    r = _decide(
        _state("INTENT_DECLARED", change_id="odd*name"),
        "docs/plans/2026-07-29-odd*name-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.ALLOW
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/gates/test_pre_tool_use.py -v -k plan_path`
Expected: FAIL — `TypeError: PreToolUseGate() takes no arguments`

**Step 3: Implement**

Add a constructor and the allowance block to `PreToolUseGate`:

```python
import glob
from fnmatch import fnmatchcase

from super_harness.gates.decisions import (
    PLAN_ARTIFACT_ALLOW_STATES,
    PLAN_PATH_ALLOW_STATES,
    PRE_TOOL_USE_DECISIONS,
    SCRATCH_ROOT,
    SUGGESTIONS,
)


class PreToolUseGate(Gate):
    def __init__(self, plan_path_patterns: list[str] | None = None) -> None:
        """`plan_path_patterns` come from `core.plan_paths.load_plan_paths` (already
        validated: each contains `{slug}` and ends in `.md`). Injected rather than
        read here so the gate stays pure and testable. Default `None` keeps every
        existing construction site (and every pre-existing test) behaving exactly as
        before: no patterns → no plan-path allowance."""
        self._plan_path_patterns = plan_path_patterns or []
```

and, after the scratch block:

```python
        # Plan-path allowance (design 2026-07-29). Guards, in order: state opted in;
        # a canonicalized path exists; the RESOLVED path is `.md` (so a symlinked
        # `docs/plans/x-<slug>.md` -> `src/evil.py` cannot launder); the pattern list
        # is really a list (a forged config must BLOCK, never raise — the hook treats
        # an exception as non-blocking); and the slug-substituted pattern matches.
        #
        # `change_id` is ESCAPED before substitution. It is read from
        # `.harness/state.yaml`, which is gitignored and writable by the agent in
        # every ALLOW state, and `change start`'s slug validation does not protect
        # that path — so a forged `change_id` containing `*`, `?` or `[...]` would
        # otherwise widen the pattern past the active change (e.g. `*` turning
        # `docs/plans/*{slug}*.md` into a match for every doc). `glob.escape` renders
        # those characters literal, keeping the binding the guard rail promises.
        if (
            state.current_state in PLAN_PATH_ALLOW_STATES
            and rp
            and rp.lower().endswith(".md")
            and state.change_id
            and isinstance(state.change_id, str)
            and isinstance(self._plan_path_patterns, list)
        ):
            safe_slug = glob.escape(state.change_id)
            for pattern in self._plan_path_patterns:
                if not isinstance(pattern, str):
                    continue
                if fnmatchcase(rp, pattern.replace("{slug}", safe_slug)):
                    return GateResult(
                        decision=GateDecision.ALLOW,
                        reason=(
                            f"{state.current_state}: plan-document authoring "
                            f"authorized ({rp})"
                        ),
                    )
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/gates/ -v`
Expected: PASS (new + all pre-existing)

**Step 5: Commit**

```bash
git add src/super_harness/gates/pre_tool_use.py tests/unit/gates/test_pre_tool_use.py
git commit -m "feat(gate): allow configured plan-document paths in INTENT_DECLARED"
```

---

## Task 5: Wire the loader into both gate construction sites

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py:252` (`_decide`)
- Modify: `src/super_harness/cli/gate.py` (the `gate check pre-tool-use` path)
- Test: `tests/integration/daemon/test_hook_entry_plan_paths.py` (create)
- Test: `tests/unit/daemon/test_hook_entry_decide.py` (create — the deferral assertion)

**Step 1: Write the failing test**

```python
# tests/integration/daemon/test_hook_entry_plan_paths.py
"""End-to-end through the real hook entry point: config on disk → allow/block."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def _hook(root: Path, tool: str, file: str) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": file}})
    proc = subprocess.run(
        [sys.executable, "-c",
         "from super_harness.daemon.hook_entry import main; main()",
         "--agent", "claude-code"],
        cwd=root, input=payload, capture_output=True, text=True,
    )
    return proc.returncode


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / ".harness" / "state.yaml").write_text(
        yaml.safe_dump({
            "changes": {
                "my-change": {
                    "change_id": "my-change",
                    "current_state": "INTENT_DECLARED",
                    "last_event_at": "2026-07-29T00:00:00Z",
                    "plan_artifacts": [],
                }
            }
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_default_config_allows_docs_plans(repo):
    assert _hook(repo, "Write", "docs/plans/2026-07-29-my-change-design.md") == 0


def test_default_config_still_blocks_source(repo):
    assert _hook(repo, "Edit", "src/api.py") == 2


def test_corrupt_config_fails_closed(repo):
    (repo / ".harness" / "plan-paths.yaml").write_text("plan_paths: [oops\n", "utf-8")
    assert _hook(repo, "Write", "docs/plans/2026-07-29-my-change-design.md") == 2


def test_scratch_area_allowed(repo):
    (repo / ".harness" / "scratch" / "my-change").mkdir(parents=True)
    assert _hook(repo, "Write", ".harness/scratch/my-change/notes.md") == 0


def test_kill_switch_path_still_blocked(repo):
    assert _hook(repo, "Write", ".harness/gate-disabled") == 2
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/integration/daemon/test_hook_entry_plan_paths.py -v`
Expected: FAIL — the allow cases return 2 (loader not wired)

**Step 3: Implement**

In `hook_entry._decide`, add the import and pass the patterns — **only when the
state can actually use them**. Every `Edit`/`Write` in every state goes through
this path, and the patterns are consulted in `INTENT_DECLARED` alone; an
unconditional YAML read+parse would tax a hot path this project has deliberately
optimised elsewhere (import-light `gates.decisions`, daemon demoted for cold-start
cost, `state_snapshot`'s single parse with CSafeLoader):

```python
    from super_harness.core.plan_paths import load_plan_paths
    from super_harness.gates.decisions import PLAN_PATH_ALLOW_STATES
    ...
    # Deferred: skip the config read entirely unless the active state opts in.
    patterns = (
        load_plan_paths(root)
        if snapshot.state and snapshot.state.current_state in PLAN_PATH_ALLOW_STATES
        else []
    )
    result = PreToolUseGate(plan_path_patterns=patterns).decide(
        ProposedAction(
            kind="edit", file=file, resolved_path=canonical_relpath(root, file)
        ),
        snapshot.state,
        [],
    )
```

Apply the identical change at the `cli/gate.py` construction site so `gate check`
and the hook can never disagree (`d-single-gate-policy`: one policy, all readers).

Add a test asserting the deferral holds — otherwise a later refactor silently
reintroduces the cost. Two traps to avoid, both of which produced a test that
guards nothing:

- **`monkeypatch` cannot go in the integration module.** Its tests drive the gate
  through `_hook()`, i.e. `subprocess.run` of a *child* interpreter; patching in
  the parent has no effect on the child.
- **The deferral is not observable from the verdict.** `load_plan_paths` never
  raises and fails closed to `[]`, and `PreToolUseGate` re-checks
  `PLAN_PATH_ALLOW_STATES` itself — so reading or not reading the config yields
  the *same* allow/block outcome in every state. Any test asserting on exit codes
  is vacuous.

The deferral is a **performance** property, so assert it where it lives: in-process,
on the call itself. Create `tests/unit/daemon/test_hook_entry_decide.py` calling
`_decide` directly (no subprocess). Before writing it, check what is actually
available (`ls tests/unit/daemon/`, `grep -n "^def \|fixture" tests/conftest.py`)
and reuse anything that fits; the sketch below defines its own workspace builder
because nothing named `tmp_repo` exists to borrow:

```python
import pytest
import yaml

import super_harness.core.plan_paths as plan_paths
from super_harness.daemon import hook_entry


def _repo(tmp_path, change_id: str, state: str):
    """Minimal workspace: .harness/ plus one change in the requested state."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"changes": {change_id: {
                "change_id": change_id,
                "current_state": state,
                "last_event_at": "2026-07-29T00:00:00Z",
                "plan_artifacts": [],
            }}}
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    "state,expect_read",
    [("INTENT_DECLARED", True), ("IMPLEMENTATION_IN_PROGRESS", False),
     ("AWAITING_CODE_REVIEW", False), ("READY_TO_MERGE", False)],
)
def test_plan_path_config_read_only_where_it_is_consulted(
    tmp_path, monkeypatch, state, expect_read
):
    # `_decide` honours SUPER_HARNESS_CHANGE_ID as a change-id override; a value
    # leaking in from the ambient environment would resolve a different (or no)
    # change and silently invalidate the parametrisation. Same hazard #60 fixed.
    monkeypatch.delenv("SUPER_HARNESS_CHANGE_ID", raising=False)
    root = _repo(tmp_path, "my-change", state)
    calls: list = []
    monkeypatch.setattr(
        plan_paths, "load_plan_paths", lambda r: calls.append(r) or []
    )
    monkeypatch.chdir(root)
    hook_entry._decide("Edit", "src/api.py")
    assert bool(calls) is expect_read
```

Three things this test depends on, all easy to break later:

- `hook_entry` imports `load_plan_paths` **inside** `_decide`, so the lookup
  resolves against the module attribute at call time — patch
  `plan_paths.load_plan_paths`, not a name bound at import.
- `monkeypatch.chdir` is required because `_decide` resolves the workspace from
  `Path.cwd()`.
- `SUPER_HARNESS_CHANGE_ID` must be cleared (above). The unified shell-runner work
  scrubs `SUPER_HARNESS_*` for subprocess paths, but this test calls `_decide`
  in-process, so it must clear the variable itself.

**Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/integration/daemon/ tests/unit/daemon/ tests/unit/gates/ -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py src/super_harness/cli/gate.py \
        tests/integration/daemon/test_hook_entry_plan_paths.py \
        tests/unit/daemon/test_hook_entry_decide.py
git commit -m "feat(gate): load plan-path config at both gate construction sites"
```

---

## Task 6: `init` skeleton + gitignore

**Files:**
- Modify: `src/super_harness/cli/init.py:197-232` (`_skeleton_files`)
- Modify: `src/super_harness/engineering/gitignore_injector.py:83` (`_CANONICAL_PATHS`)
- Test: `tests/unit/cli/test_init_skeleton.py`, `tests/unit/engineering/test_gitignore_injector.py`

**Step 1: Write the failing tests**

```python
def test_init_writes_plan_paths_skeleton(tmp_path):
    from super_harness.cli.init import _skeleton_files
    assert "plan-paths.yaml" in _skeleton_files()


def test_plan_paths_skeleton_survives_its_own_loader(tmp_path):
    """Every shipped pattern must pass validation — one that fails silently ships
    a narrower allowance than the docs promise."""
    from super_harness.cli.init import _skeleton_files
    from super_harness.core.plan_paths import load_plan_paths
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "plan-paths.yaml").write_text(
        _skeleton_files()["plan-paths.yaml"], encoding="utf-8"
    )
    assert load_plan_paths(tmp_path) == [
        "docs/plans/*{slug}*.md",
        "openspec/changes/{slug}/*.md",
        "docs/superpowers/plans/*{slug}*.md",
        "docs/superpowers/specs/*{slug}*.md",
    ]


def test_skeleton_openspec_pattern_matches_the_files_openspec_watches(tmp_path):
    """Regression anchor for the `**` trap: fnmatch gives `**` no recursive
    meaning, so a glob-style pattern would miss proposal.md / tasks.md — exactly
    the files the OpenSpec adapter emits plan_ready from."""
    from fnmatch import fnmatchcase
    from super_harness.cli.init import _skeleton_files
    import yaml
    patterns = yaml.safe_load(_skeleton_files()["plan-paths.yaml"])["plan_paths"]
    openspec = [p for p in patterns if p.startswith("openspec/")]
    assert openspec, "skeleton must ship an openspec pattern"
    for name in ("proposal.md", "tasks.md", "specs/nested.md"):
        target = f"openspec/changes/my-change/{name}"
        assert any(
            fnmatchcase(target, p.replace("{slug}", "my-change")) for p in openspec
        ), target


def test_gitignore_covers_scratch():
    from super_harness.engineering.gitignore_injector import _CANONICAL_PATHS
    assert ".harness/scratch/" in _CANONICAL_PATHS
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/cli/test_init_skeleton.py tests/unit/engineering/test_gitignore_injector.py -v`
Expected: FAIL — KeyError / assertion

**Step 3: Implement**

Add to `_skeleton_files()`:

```python
        "plan-paths.yaml": (
            "# Where this repo's plan documents live. Commit this file — the gate\n"
            "# reads it, so widening it is itself a gated edit.\n"
            "#\n"
            "# Every pattern MUST contain {slug} (binds the allowance to the active\n"
            "# change) and MUST end in .md. Patterns failing either rule are dropped.\n"
            "# A corrupt file yields NO allowance (fail-closed), not the default.\n"
            "version: 1\n"
            "plan_paths:\n"
            '  - "docs/plans/*{slug}*.md"\n'
            '  - "openspec/changes/{slug}/*.md"\n'
            '  - "docs/superpowers/plans/*{slug}*.md"\n'
            '  - "docs/superpowers/specs/*{slug}*.md"\n'
        ),
```

Note the single `*` in the openspec line — a `**/` segment would miss
`proposal.md` and `tasks.md` under `fnmatch` (see the D4 measurements).

**All four ship enabled, none commented out.** `init --framework` is a documented
no-op placeholder and `_skeleton_files()` takes no framework argument, so a
commented-out pattern would leave a fresh OpenSpec repo blocked after this change
— the coverage claim would be false until the owner hand-edits a file nothing
tells them to edit. Enabling all four costs nothing: each stays `{slug}`-bound and
`.md`-bound, and a pattern whose directory does not exist simply never matches.
Owners trim what they don't use; they should not have to uncomment to get the
behaviour the docs promise.

The superpowers lines matter: the design's motivation names **both** adapters'
auto-`plan_ready` paths as dead, and `adapters/framework/superpowers.py` scans
three candidate dirs (`docs/plans`, `docs/superpowers/plans`,
`docs/superpowers/specs`). Only the first is covered by the default, so the other
two must at least be discoverable in the skeleton — otherwise half the stated
breakage silently stays broken. Task 7 states the same in `docs/adapters/`.

**Known limitation to state, not paper over:** superpowers identifies its artifacts
by the `change:` *marker*, and filenames are free-form. A repo whose superpowers
plan filenames do not contain the slug is not covered by any `{slug}`-bearing
pattern. Marker-based allowance was rejected in design (the agent can add a marker
to any `.md`), so this is a real gap, not an oversight — record it in
`docs/limitations.md`.

Add `".harness/scratch/",` to `_CANONICAL_PATHS` (next to the other runtime dirs).

**Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/cli/ tests/unit/engineering/ -v`
Expected: PASS

**Step 5: Commit + resync this repo's own managed files**

```bash
.venv/bin/super-harness sync
git add src/super_harness/cli/init.py \
        src/super_harness/engineering/gitignore_injector.py \
        .gitignore tests/
git commit -m "feat(init): ship plan-paths.yaml skeleton and gitignore the scratch area"
```

---

## Task 6b: Make this repo eat its own config — and fix the slug/filename mismatch

**Files:**
- Create: `.harness/plan-paths.yaml` (tracked)
- Rename: `docs/plans/2026-07-29-gate-authoring-space-{design,implementation}.md`
  → `…-gate-authoring-space-v2-{design,implementation}.md`

Two defects the plan review caught, with one shared root.

**The config file does not exist here.** The design designates
`.harness/plan-paths.yaml` as tracked config whose edits are themselves gated, and
Task 6 ships a skeleton for *new* repos — but nothing creates it in this
repository. Without it this repo silently runs on `DEFAULT_PLAN_PATHS`, and the
"widening the allowance is itself a gated edit" property is never demonstrated on
the project that ships it. Write it with the same content as the skeleton.

**The default pattern does not match this change's own plan documents.** This
change's slug is `2026-07-29-gate-authoring-space-v2` (the un-suffixed slug is
ABANDONED and cannot be reused), so `docs/plans/*{slug}*.md` expands to
`docs/plans/*2026-07-29-gate-authoring-space-v2*.md` — which matches **neither**
`…-gate-authoring-space-design.md` nor `…-implementation.md`. The allowance would
fail closed exactly where this plan claims it works.

The repo convention is `<date>-<slug>-<suffix>.md`, and the slug itself usually
carries **no** date: `2026-07-20-init-wizard-progressive-disclosure-design.md` for
slug `init-wizard-progressive-disclosure`. Either way the filename contains the
slug, which is all `*{slug}*.md` needs. This change is the odd one out because its
slug already begins with a date *and* gained a `-v2` suffix the filenames never
got. Renaming restores the invariant (filename contains the slug) rather than
working around the matcher — do **not** loosen the pattern to paper over it, since
the `{slug}` requirement is the guard rail that keeps `AGENTS.md` and
`docs/decisions/**` out.

Do this in `IMPLEMENTATION_IN_PROGRESS`, where the gate allows all edits — it is
not a bypass. Use `git mv` so the rename is staged as such, and update every
in-repo reference to the old filenames afterwards (`git grep -l gate-authoring-space`).

**Then list BOTH the old and the new paths in Task 10's `--scope`.** A rename only
appears as a rename in `git diff` when similarity detection succeeds; these two
files are being edited substantially on the same branch, so detection can fall
below the threshold and the diff degrades to *delete old + add new*. The old paths
would then be out-of-scope changes at the merge boundary. Declaring both costs
nothing and is robust either way.

**Verify:**

```bash
.venv/bin/super-harness doc refs --gate     # no dead references to the old names
git grep -n "gate-authoring-space-design\|gate-authoring-space-implementation"
# expect: no hits outside historical attestations/event logs
```

---

## Task 7: Fix the guidance that currently sends the agent into a loop

**Files:**
- Modify: `src/super_harness/gates/decisions.py` (`SUGGESTIONS["INTENT_DECLARED"]`)
- Modify: `src/super_harness/adapters/agent/claude_code.py:64` (`_AGENTS_MD_SUBSECTION`)
- Modify: `src/super_harness/adapters/agent/codex.py` (same subsection, keep symmetric)
- Modify: `docs/getting-started.md:315`, `docs/limitations.md`, `docs/concepts.md`
- Test: `tests/unit/gates/test_decisions.py`, `tests/unit/engineering/test_agents_md_render.py`

This is the half that made pothole ⑩ a documented procedure. Today the block says
"Draft a plan" — an action the same gate then blocks.

**Step 1: Write the failing tests**

```python
def test_intent_declared_suggestion_names_the_authoring_space():
    from super_harness.gates.decisions import SUGGESTIONS
    s = SUGGESTIONS["INTENT_DECLARED"]
    assert "plan-paths.yaml" in s or "plan document" in s
    assert "scratch" in s


def test_agents_md_documents_the_authoring_space():
    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
    text = ClaudeCodeAdapter().agents_md_subsection()
    assert ".harness/scratch/" in text
    assert "INTENT_DECLARED" in text
```

(Adjust the second test to whatever accessor `test_agents_md_render.py` already uses
for the subsection — do not invent a new one.)

**Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/gates/test_decisions.py tests/unit/engineering/test_agents_md_render.py -v`
Expected: FAIL

**Step 3: Implement**

`SUGGESTIONS["INTENT_DECLARED"]` →

```python
    "INTENT_DECLARED": (
        "Author the plan document at a path configured in .harness/plan-paths.yaml "
        "(default docs/plans/*<slug>*.md), then `plan ready`. Working notes go in "
        ".harness/scratch/<slug>/, which is writable in any state."
    ),
```

Add to the AGENTS.md subsection, next to the existing `PLAN_REJECTED` paragraph:

```
- **Authoring is allowed in-gate:** in `INTENT_DECLARED`, writing the change's plan
  document is ALLOWED at any path matching `.harness/plan-paths.yaml` (default
  `docs/plans/*<slug>*.md`). Scratch notes are ALLOWED in `.harness/scratch/<slug>/`
  in **every** state — it is gitignored and never reviewed. Source files stay blocked
  until the plan is approved. Never write these through the shell to dodge the gate.
```

Correct `docs/getting-started.md:315` to state that plan authoring in
`INTENT_DECLARED` requires the path to be covered by `plan-paths.yaml`, and that the
OpenSpec layout needs the commented-out pattern enabled. Add the scratch area and the
one-sentence rule to `docs/concepts.md`. Update `docs/limitations.md`'s plan-artifact
section.

**Step 4: Run to verify it passes**

```bash
.venv/bin/pytest tests/unit/ -v
.venv/bin/super-harness sync --agents-md && .venv/bin/super-harness doc check
.venv/bin/super-harness doc refs --gate
```
Expected: all PASS, `doc check: clean`, `doc refs: clean`

**Step 5: Commit**

```bash
git add src/super_harness/gates/decisions.py src/super_harness/adapters/agent/ \
        docs/ AGENTS.md tests/
git commit -m "docs(gate): tell the agent where it may author, and stop the block-loop"
```

---

## Task 8: Re-ratify `d-single-gate-policy` and record the new decision

**Files:**
- Modify: `docs/decisions/d-single-gate-policy.md`
- Create: `docs/decisions/d-gate-governs-git-product.md`

**Step 1: Update the ratified body**

`gates/decisions.py` now holds **four** policy literals, not two. The ratified text
says two. Editing the body trips the text lock, so:

```bash
# edit the body to describe all four literals, then:
.venv/bin/super-harness decision ratify d-single-gate-policy
.venv/bin/super-harness decision check
```
Expected: `bite-test: bites` then `decision check: clean`

**Step 2: Draft the new decision**

```bash
.venv/bin/super-harness decision new d-gate-governs-git-product \
  --text "The pre-tool-use gate governs files that will enter git as product; \
allowances are hard-coded path whitelists, never derived from gitignore status."
```

Body must record the load-bearing evidence: `.harness/gate-disabled`,
`.claude/settings.local.json`, `.codex/hooks.json`, and `.harness/state.yaml` are all
gitignored, so a gitignore-derived allowance would let a blocked agent disable the
gate.

**Step 3: Arm it if — and only if — a non-hollow check exists**

Candidate `check` block (verify it actually bites before keeping it):

```check
python -m tests.probes.gate_kill_switch_probe
```

The probe must assert, against the real `PreToolUseGate`, that `.harness/gate-disabled`
and `.claude/settings.local.json` BLOCK in every state. Required `counterexample`: add
`".harness"` to a whitelist constant and confirm the probe fails.

**If no honest check can be written, leave it tier-2 with a `review` block** — an
armed-but-hollow check is worse than none (`decision ratify` will refuse it anyway:
the bite-test fails when the counterexample does not flip the verdict).

**Step 4: Verify**

```bash
.venv/bin/super-harness decision check --gate-reconcile
```
Expected: exit 0, `clean`

**Step 5: Commit**

```bash
git add docs/decisions/ tests/probes/
git commit -m "docs(decisions): record the gate's scope rule; re-ratify d-single-gate-policy"
```

---

## Task 9: Live end-to-end proof (not a unit test)

**Files:**
- Create: `.harness/scratch/2026-07-29-gate-authoring-space-v2/live-proof.md` (throwaway)

> The directory MUST carry this change's own slug (`…-v2`). The un-suffixed
> `2026-07-29-gate-authoring-space` is a **different, ABANDONED** record still
> present in `.harness/state.yaml`; writing under it would neither be allowed by
> the gate (the scratch prefix is keyed on the *active* change) nor prove anything
> about this change. Same hazard as pothole ④ (stale-change hijack).

A green unit suite is not evidence the installed hook behaves. Reproduce the exact
probe from the design doc against a temp repo with the **real** adapter installed:

```bash
D=$(mktemp -d); cd "$D" && git init -q . && git config user.email t@e.com
mkdir -p src docs/plans && echo x > src/api.py && git add -A && git commit -qm i
super-harness init --no-agent --yes >/dev/null
super-harness adapter install claude-code >/dev/null
super-harness change start my-change >/dev/null

probe() { echo "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$1\"}}" \
  | super-harness-hook --agent claude-code >/dev/null 2>&1; echo "$1 → $?"; }

probe docs/plans/2026-07-29-my-change-design.md   # expect 0  (was 2)
probe .harness/scratch/my-change/notes.md         # expect 0  (was 2)
probe src/api.py                                  # expect 2  (unchanged)
probe AGENTS.md                                   # expect 2  (unchanged)
probe .harness/gate-disabled                      # expect 2  (unchanged)
probe /tmp/outside.md                             # expect 2  (unchanged)
```

All six must match. Paste the output into the change's scratch dir; it becomes the
code-review evidence that the gate behaves as designed with a real agent adapter.

---

## Task 10: Full suite, CI parity, and the lifecycle close-out

```bash
.venv/bin/pytest -q                       # expect: all green (2098+ baseline)
.venv/bin/ruff check .
.venv/bin/super-harness doc check
.venv/bin/super-harness doc refs --gate
.venv/bin/super-harness sync --check
.venv/bin/super-harness decision check --gate-reconcile
PYTHONPATH=src .venv/bin/lint-imports --config .importlinter
```

Then the self-host lifecycle. **`--scope` must list every file touched** (pothole ⑱ —
omitting it silently empties `plan_artifacts`):

```bash
super-harness plan ready 2026-07-29-gate-authoring-space-v2 --scope '[
  "docs/plans/2026-07-29-gate-authoring-space-design.md",
  "docs/plans/2026-07-29-gate-authoring-space-implementation.md",
  "docs/plans/2026-07-29-gate-authoring-space-v2-design.md",
  "docs/plans/2026-07-29-gate-authoring-space-v2-implementation.md",
  ".harness/plan-paths.yaml",
  "src/super_harness/core/plan_paths.py",
  "src/super_harness/gates/decisions.py",
  "src/super_harness/gates/pre_tool_use.py",
  "src/super_harness/daemon/hook_entry.py",
  "src/super_harness/cli/gate.py",
  "src/super_harness/cli/init.py",
  "src/super_harness/engineering/gitignore_injector.py",
  "src/super_harness/adapters/agent/claude_code.py",
  "src/super_harness/adapters/agent/codex.py",
  "docs/getting-started.md", "docs/concepts.md", "docs/limitations.md",
  "docs/cli-reference.md", "docs/decisions/d-single-gate-policy.md",
  "docs/decisions/d-gate-governs-git-product.md",
  "AGENTS.md", ".gitignore", "tests/"
]' --tier-hint Normal
```

**Review:** two independent sources for both plan and code review. This change edits
the gate decision table — the one place where a single-source miss has previously
been caught only by the second reviewer (PR #82: all 7 real findings came from the
second source; PR #85: the fail-open hole was caught by Codex alone). Do not thin it.

Close with `attest write` covering the full scope, then `on-merge` — and if this
branch carries more than one change, run `on-merge` for **each** (pothole ㉑).

---

## Deliberately NOT in scope

- Relaxing `AWAITING_PLAN_REVIEW` (would desync the reviewer's frozen target).
- `change start --plan` (D2 — agent-supplied identity).
- Framework-adapter auto-recording of `plan_artifacts` (still deferred from #85).
- Relaxing out-of-repo paths (fail-safe by design; the scratch area is the answer).
- Any change to what `PLAN_REJECTED` allows.
