# Auto-review hardening — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Force the code agent to perform a real code review against a harness-assembled bundle and leave a structured verdict inlined in the event payload, rejecting bare/incomplete/stale approvals at emit time.

**Architecture:** Pure-CLI, deterministic, no LLM. New `review prepare` verb assembles a bundle (diff∩scope + out-of-scope + spec/plan paths + checklist + a committed-HEAD digest) to a gitignored artifact. `review approve/reject` gain `--verdict-file`; `approve --reviewer code-reviewer` refuses to emit `code_review_passed` unless the verdict covers every checklist item and its `bundle_digest` matches the current in-scope committed diff. Verdict is inlined in the event payload (reaches the committed attestation via `attest write`).

**Tech Stack:** Python 3.10+, click, PyYAML, pytest. Mirrors existing patterns in `core/source_scope.py`, `engineering/reviewer_policy.py`, `cli/review.py`, `sensors/verification_runner.py`.

**Design doc:** `docs/plans/2026-06-23-auto-review-hardening-design.md` (umbrella). This plan is Slice 1 = A + B + C + configurable checklist. Slice 2 (D + E) is a separate plan.

**Verification convention:** run all commands with `PATH="$(pwd)/.venv/bin:$PATH"` prefixed (project venv; never `uv run` inside the repo). Example: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_scope_match.py -v`.

---

## File Structure

- Create `src/super_harness/core/scope_match.py` — pure scope matcher + fail-closed in-scope/out-of-scope diff computation + committed-HEAD digest + clean-tree check. (Extracted so both the verification baseline and the review bundle share one matcher; the digest path fails CLOSED, unlike the advisory baseline.)
- Create `src/super_harness/core/review_checklist.py` — per-reviewer checklist resolution (config override + built-in default).
- Create `src/super_harness/core/review_bundle.py` — assemble + serialize the review bundle.
- Create `src/super_harness/core/review_verdict.py` — parse/validate a verdict file; checklist-coverage check.
- Modify `src/super_harness/core/paths.py` — add `pending_reviews_dir(root, change_id)`.
- Modify `src/super_harness/sensors/verification_runner.py` — import the shared matcher instead of the local `_covered_by_scope` (no behavior change).
- Modify `src/super_harness/cli/review.py` — add `review prepare`; add `--verdict-file`/`--base` to `approve`/`reject`; wire emit-time validation for `code-reviewer`.
- Modify `docs/cli-reference.md` (regenerate), `AGENTS.md` (review protocol), `.harness/sensors.yaml` example annotation (or the templated copy) — doc sync.
- Tests under `tests/unit/core/` and `tests/unit/cli/`.

Build order (dependency chain): Task 1 (scope_match) → Task 2 (refactor baseline) → Task 3 (checklist) → Task 4 (bundle) → Task 5 (verdict) → Task 6 (paths) → Task 7 (prepare CLI) → Task 8 (emit teeth) → Task 9 (doc sync).

---

## Task 1: `core/scope_match.py` — shared matcher + fail-closed diff/digest

**Files:**
- Create: `src/super_harness/core/scope_match.py`
- Test: `tests/unit/core/test_scope_match.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_scope_match.py
"""Unit tests for core.scope_match (shared scope matcher + fail-closed git helpers)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from super_harness.core.scope_match import (
    GitScopeError,
    committed_scope_digest,
    covered_by_scope,
    split_changed_by_scope,
    working_tree_dirty,
)


def _git(ws: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, text=True)


def _repo(ws: Path) -> None:
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")


def test_covered_by_scope_segment_aware() -> None:
    assert covered_by_scope("src/foo/x.py", ["src/foo/"]) is True
    assert covered_by_scope("src/foo/x.py", ["src/foo"]) is True
    assert covered_by_scope("src/foo.py", ["src/foo.py"]) is True
    # sibling sharing textual prefix is NOT covered
    assert covered_by_scope("src/foobar.py", ["src/foo"]) is False
    assert covered_by_scope("a.py", []) is False


def test_split_changed_by_scope(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "in.py").write_text("a\n")
    (tmp_path / "out.py").write_text("b\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "in.py").write_text("a2\n")
    (tmp_path / "out.py").write_text("b2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    in_scope, out_scope = split_changed_by_scope(tmp_path, base="main", declared=["src/"])
    assert in_scope == ["src/in.py"]
    assert out_scope == ["out.py"]


def test_committed_scope_digest_stable_and_changes(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "f.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "w1")
    d1 = committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    d1_again = committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    assert d1 == d1_again and d1  # stable, non-empty
    (tmp_path / "f.py").write_text("v3\n")
    _git(tmp_path, "commit", "-aqm", "w2")
    d2 = committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    assert d2 != d1  # committed change moves the digest


def test_committed_scope_digest_empty_scope_is_constant(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    assert committed_scope_digest(tmp_path, base="main", in_scope=[]) == committed_scope_digest(
        tmp_path, base="main", in_scope=[]
    )


def test_working_tree_dirty(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    assert working_tree_dirty(tmp_path, ["f.py"]) is False
    (tmp_path / "f.py").write_text("dirty\n")
    assert working_tree_dirty(tmp_path, ["f.py"]) is True


def test_git_error_fails_closed(tmp_path: Path) -> None:
    # not a git repo → fail closed (raise), NOT silent pass
    with pytest.raises(GitScopeError):
        committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    with pytest.raises(GitScopeError):
        split_changed_by_scope(tmp_path, base="main", declared=["src/"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_scope_match.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'super_harness.core.scope_match'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/core/scope_match.py
"""Shared scope matcher + fail-closed git helpers for review bundling.

`covered_by_scope` is the segment-aware matcher extracted from
`sensors.verification_runner._covered_by_scope` (Task 2 re-points the baseline at
this copy). Unlike the advisory `scope-vs-plan-final` baseline (which fails OPEN
on git error so it never cries wolf), the helpers here that back the review
freshness gate fail CLOSED: a git error raises `GitScopeError` so the emit-time
check rejects rather than waving a stale review through.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class GitScopeError(RuntimeError):
    """A git operation backing a scope/digest computation failed (fail-closed)."""


def covered_by_scope(changed_file: str, declared_files: list[str]) -> bool:
    """True if `changed_file` is covered by any declared scope entry (segment-aware).

    Exact path equality OR a prefix landing on a path boundary. `src/foo` covers
    `src/foo/x.py` but NOT the sibling `src/foobar.py`.
    """
    for entry in declared_files:
        if changed_file == entry:
            return True
        prefix = entry if entry.endswith("/") else entry + "/"
        if changed_file.startswith(prefix):
            return True
    return False


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise GitScopeError(f"`git {' '.join(args)}` failed: {type(e).__name__}: {e}") from e
    return proc.stdout


def split_changed_by_scope(
    root: Path, *, base: str, declared: list[str]
) -> tuple[list[str], list[str]]:
    """Return (in_scope, out_of_scope) changed files for `base...HEAD`.

    Fail-closed: any git error raises `GitScopeError`.
    """
    out = _git(root, "diff", "--name-only", f"{base}...HEAD")
    changed = [ln for ln in out.splitlines() if ln.strip()]
    in_scope = sorted(f for f in changed if covered_by_scope(f, declared))
    out_scope = sorted(f for f in changed if not covered_by_scope(f, declared))
    return in_scope, out_scope


def committed_scope_digest(root: Path, *, base: str, in_scope: list[str]) -> str:
    """sha256 over the committed diff (`base...HEAD`) of the in-scope paths.

    Committed state only (reproducible / tamper-evident); working-tree content is
    deliberately NOT hashed. Empty `in_scope` → digest of empty diff (a constant);
    the caller documents that the freshness check is inert for empty scope.
    Fail-closed: git error raises `GitScopeError`.
    """
    if not in_scope:
        diff = ""
    else:
        diff = _git(root, "diff", f"{base}...HEAD", "--", *sorted(in_scope))
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def working_tree_dirty(root: Path, paths: list[str]) -> bool:
    """True if any of `paths` has uncommitted changes (modified / staged / untracked).

    Empty `paths` → False (nothing to be dirty). Fail-closed on git error.
    """
    if not paths:
        return False
    out = _git(root, "status", "--porcelain", "--", *sorted(paths))
    return bool(out.strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_scope_match.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/scope_match.py tests/unit/core/test_scope_match.py
git commit -m "feat(review): shared scope matcher + fail-closed diff/digest helpers"
```

---

## Task 2: Re-point the verification baseline at the shared matcher

**Files:**
- Modify: `src/super_harness/sensors/verification_runner.py` (the `_covered_by_scope` definition ~line 470 and its call site ~line 448)

- [ ] **Step 1: Run the existing baseline tests to capture green baseline**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/sensors/ -k scope -v`
Expected: PASS (record the passing set; this task must not change it)

- [ ] **Step 2: Replace the local matcher with an import (no behavior change)**

In `src/super_harness/sensors/verification_runner.py`:
- Add to imports near the other `from super_harness.core...` imports:

```python
from super_harness.core.scope_match import covered_by_scope as _covered_by_scope
```

- Delete the local `def _covered_by_scope(changed_file, declared_files) -> bool:` function body (the ~16-line definition). Keep every call site (`_covered_by_scope(f, declared_files)`) unchanged — the imported alias has the identical signature and semantics. The baseline's fail-OPEN git handling stays exactly as-is in `_baseline_scope_vs_plan` (we did NOT move the git call, only the pure matcher).

- [ ] **Step 3: Run baseline tests to verify still green**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/sensors/ -k scope -v`
Expected: PASS (same set as Step 1)

- [ ] **Step 4: Run the full sensors suite to catch import-cycle regressions**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/sensors/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/sensors/verification_runner.py
git commit -m "refactor(sensors): baseline reuses core.scope_match.covered_by_scope"
```

---

## Task 3: `core/review_checklist.py` — per-reviewer checklist resolution

**Files:**
- Create: `src/super_harness/core/review_checklist.py`
- Test: `tests/unit/core/test_review_checklist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_review_checklist.py
"""Unit tests for core.review_checklist resolution (config override + default)."""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.core.review_checklist import (
    DEFAULT_CHECKLISTS,
    ReviewChecklistError,
    resolve_checklist,
)


def _harness(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_default_when_no_config(tmp_path: Path) -> None:
    _harness(tmp_path)
    assert resolve_checklist(tmp_path, "code-reviewer") == DEFAULT_CHECKLISTS["code-reviewer"]


def test_config_override(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text(
        "checklists:\n  code-reviewer:\n    - custom-a\n    - custom-b\n"
    )
    assert resolve_checklist(root, "code-reviewer") == ["custom-a", "custom-b"]


def test_corrupt_config_falls_back_to_default(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text("checklists: [unbalanced\n")
    assert resolve_checklist(root, "code-reviewer") == DEFAULT_CHECKLISTS["code-reviewer"]


def test_empty_override_list_is_rejected(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text(
        "checklists:\n  code-reviewer: []\n"
    )
    with pytest.raises(ReviewChecklistError):
        resolve_checklist(root, "code-reviewer")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_checklist.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/core/review_checklist.py
"""Per-reviewer review checklist resolution.

Resolution order (mirrors engineering.reviewer_policy tolerance):
1. `.harness/review-checklists.yaml` → `checklists.<reviewer>` (a non-empty list);
2. else the built-in default for that reviewer.

Absent / corrupt YAML → default. A PRESENT-but-empty list is a config error
(the author meant to configure a checklist but emptied it) → raise.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CHECKLISTS: dict[str, list[str]] = {
    "code-reviewer": [
        "spec-compliance",
        "scope-adherence",
        "code-quality",
        "edge-cases",
    ],
    "plan-reviewer": [
        "spec-coverage",
        "design-soundness",
        "scope-declared",
    ],
}


class ReviewChecklistError(ValueError):
    """`.harness/review-checklists.yaml` is present but a reviewer's list is malformed."""


def _checklists_file(root: Path) -> Path:
    return root / ".harness" / "review-checklists.yaml"


def resolve_checklist(root: Path, reviewer: str) -> list[str]:
    """Return the resolved checklist item ids for `reviewer`."""
    default = list(DEFAULT_CHECKLISTS.get(reviewer, []))
    f = _checklists_file(root)
    if not f.is_file():
        return default
    try:
        parsed: Any = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return default
    if not isinstance(parsed, dict):
        return default
    checklists = parsed.get("checklists")
    if not isinstance(checklists, dict) or reviewer not in checklists:
        return default
    items = checklists[reviewer]
    if not isinstance(items, list) or any(not isinstance(i, str) for i in items):
        raise ReviewChecklistError(
            f"checklists.{reviewer} must be a list of strings, got {items!r}"
        )
    if not items:
        raise ReviewChecklistError(
            f"checklists.{reviewer} is an empty list — remove the key to use the default"
        )
    return list(items)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_checklist.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_checklist.py tests/unit/core/test_review_checklist.py
git commit -m "feat(review): per-reviewer checklist resolution (config + default)"
```

---

## Task 4: `core/review_bundle.py` — assemble the review bundle

**Files:**
- Create: `src/super_harness/core/review_bundle.py`
- Test: `tests/unit/core/test_review_bundle.py`

Bundle dict shape (serialized to the artifact + echoed in `--json`):
`{change, reviewer, base, diff_in_scope, out_of_scope, spec_path, plan_path, checklist, bundle_digest}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_review_bundle.py
"""Unit tests for core.review_bundle assembly."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from super_harness.core.review_bundle import (
    BundleError,
    assemble_bundle,
    load_base_branch,
)


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _change(ws: Path, declared: list[str]) -> str:
    """Seed a change in AWAITING_CODE_REVIEW with declared scope.files."""
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    seq = [
        ("intent_declared", {}),
        ("plan_ready", {"scope": {"files": declared}}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("implementation_complete", {}),
    ]
    for t, payload in seq:
        EventWriter(events_path(ws)).emit(
            Event(
                event_id=new_event_id(), type=t, change_id="c",
                timestamp="2026-06-23T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain", payload=payload,
            )
        )
    refresh_state_after_emit(ws)
    return "c"


def _repo_with_change(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    (tmp_path / "other.py").write_text("o1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    (tmp_path / "other.py").write_text("o2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    return tmp_path


def test_assemble_bundle_happy(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["diff_in_scope"] == ["src/a.py"]
    assert b["out_of_scope"] == ["other.py"]
    assert b["checklist"] == ["spec-compliance", "scope-adherence", "code-quality", "edge-cases"]
    assert b["bundle_digest"]  # non-empty
    assert b["base"] == "main"


def test_assemble_bundle_rejects_dirty_in_scope_tree(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    (ws / "src" / "a.py").write_text("uncommitted\n")  # dirty in-scope file
    with pytest.raises(BundleError, match="commit"):
        assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")


def test_assemble_bundle_empty_scope_inert_digest(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, [])  # no declared scope
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["diff_in_scope"] == []
    # empty-scope digest is the constant empty-diff digest (freshness inert; documented)
    assert b["bundle_digest"]


def test_load_base_branch_default_and_override(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir(parents=True)
    assert load_base_branch(tmp_path) == "main"
    (tmp_path / ".harness" / "policy.yaml").write_text("review:\n  base_branch: develop\n")
    assert load_base_branch(tmp_path) == "develop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_bundle.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/core/review_bundle.py
"""Assemble a deterministic review bundle for `review prepare`.

The bundle is the harness-assembled context a reviewer subagent reviews against:
the in-scope committed diff, out-of-scope drift, spec/plan paths, the resolved
checklist, and a committed-HEAD digest tying a later verdict to this diff state.
No LLM, no inference — pure derivation. Requires a clean in-scope working tree
(the digest is over committed HEAD; see design §4.C "commit obligation").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from super_harness.core.paths import events_path
from super_harness.core.reducer import derive_state
from super_harness.core.review_checklist import resolve_checklist
from super_harness.core.scope_match import (
    GitScopeError,
    committed_scope_digest,
    split_changed_by_scope,
    working_tree_dirty,
)

_DEFAULT_BASE = "main"


class BundleError(ValueError):
    """The review bundle cannot be assembled (dirty tree, git failure, etc.)."""


def load_base_branch(root: Path) -> str:
    """Base branch for the in-scope diff: `.harness/policy.yaml` review.base_branch, else `main`.

    Tolerant: absent/corrupt yaml → default. This is the single config location
    for the base branch so the implementer never re-hardcodes `main`.
    """
    f = root / ".harness" / "policy.yaml"
    if not f.is_file():
        return _DEFAULT_BASE
    try:
        parsed: Any = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return _DEFAULT_BASE
    if not isinstance(parsed, dict):
        return _DEFAULT_BASE
    review = parsed.get("review")
    if isinstance(review, dict) and isinstance(review.get("base_branch"), str):
        return review["base_branch"]
    return _DEFAULT_BASE


def _spec_plan_paths(framework: str | None, root: Path, change_id: str) -> tuple[str, str]:
    if not framework:
        return "", ""
    from super_harness.adapters import FrameworkAdapter
    from super_harness.adapters.registry import get_builtin

    cls = get_builtin(framework)
    if cls is None or not issubclass(cls, FrameworkAdapter):
        return "", ""
    paths = cls().spec_paths(root, change_id)
    return paths.get("spec", ""), paths.get("plan", "")


def assemble_bundle(
    root: Path, *, change_id: str, reviewer: str, base: str | None = None
) -> dict[str, Any]:
    """Build the review bundle dict for `change_id` / `reviewer`.

    Raises `BundleError` on a dirty in-scope tree or any git failure (fail-closed).
    """
    resolved_base = base or load_base_branch(root)
    cs = derive_state(events_path(root)).get(change_id)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    framework = cs.framework if cs is not None else None

    if working_tree_dirty(root, declared):
        raise BundleError(
            "in-scope files have uncommitted changes — commit them first "
            "(the review digest is over the committed HEAD diff)."
        )
    try:
        in_scope, out_scope = split_changed_by_scope(root, base=resolved_base, declared=declared)
        digest = committed_scope_digest(root, base=resolved_base, in_scope=in_scope)
    except GitScopeError as e:
        raise BundleError(str(e)) from e

    spec_path, plan_path = _spec_plan_paths(framework, root, change_id)
    return {
        "change": change_id,
        "reviewer": reviewer,
        "base": resolved_base,
        "diff_in_scope": in_scope,
        "out_of_scope": out_scope,
        "spec_path": spec_path,
        "plan_path": plan_path,
        "checklist": resolve_checklist(root, reviewer),
        "bundle_digest": digest,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_bundle.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_bundle.py tests/unit/core/test_review_bundle.py
git commit -m "feat(review): assemble deterministic review bundle (diff/scope/digest)"
```

---

## Task 5: `core/review_verdict.py` — parse + coverage check

**Files:**
- Create: `src/super_harness/core/review_verdict.py`
- Test: `tests/unit/core/test_review_verdict.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_review_verdict.py
"""Unit tests for core.review_verdict parse + coverage."""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.core.review_verdict import (
    VerdictError,
    check_coverage,
    parse_verdict_file,
)

_OK = """
bundle_digest: abc123
checklist:
  - item: spec-compliance
    status: pass
  - item: scope-adherence
    status: pass
  - item: code-quality
    status: pass
  - item: edge-cases
    status: pass
findings: []
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "v.yaml"
    p.write_text(text)
    return p


def test_parse_ok(tmp_path: Path) -> None:
    v = parse_verdict_file(_write(tmp_path, _OK))
    assert v["bundle_digest"] == "abc123"
    assert len(v["checklist"]) == 4


def test_parse_rejects_bad_status(tmp_path: Path) -> None:
    bad = _OK.replace("status: pass", "status: maybe", 1)
    with pytest.raises(VerdictError, match="status"):
        parse_verdict_file(_write(tmp_path, bad))


def test_parse_rejects_findings_required_when_a_check_fails(tmp_path: Path) -> None:
    # a checklist item fails but findings empty → invalid
    text = """
bundle_digest: x
checklist:
  - item: spec-compliance
    status: fail
findings: []
"""
    with pytest.raises(VerdictError, match="findings"):
        parse_verdict_file(_write(tmp_path, text))


def test_check_coverage_missing_item(tmp_path: Path) -> None:
    v = parse_verdict_file(_write(tmp_path, _OK))
    # require an item the verdict didn't cover
    missing = check_coverage(v, ["spec-compliance", "scope-adherence", "code-quality",
                                 "edge-cases", "security"])
    assert missing == ["security"]


def test_check_coverage_complete(tmp_path: Path) -> None:
    v = parse_verdict_file(_write(tmp_path, _OK))
    assert check_coverage(v, ["spec-compliance", "scope-adherence",
                              "code-quality", "edge-cases"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/core/review_verdict.py
"""Parse + validate a structured review verdict file (for `review approve/reject`).

Shape (YAML):
    bundle_digest: <str>
    checklist:
      - item: <str>
        status: pass | fail | na
        note: <str, optional>
    findings:               # required non-empty when any checklist item is `fail`
      - id: <str>
        severity: blocker | major | minor
        file: <str>
        summary: <str>
    prior_findings: ...     # slice-2 only; ignored here if present

This module validates SHAPE; the emit-time CLI check (cli/review.py) layers on the
freshness (digest) + coverage gates. Inferential quality is never checked here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_STATUSES = {"pass", "fail", "na"}
_SEVERITIES = {"blocker", "major", "minor"}


class VerdictError(ValueError):
    """The verdict file is missing, unparseable, or structurally invalid."""


def parse_verdict_file(path: Path) -> dict[str, Any]:
    """Load + structurally validate a verdict file. Raises `VerdictError`."""
    if not path.is_file():
        raise VerdictError(f"verdict file not found: {path}")
    try:
        parsed: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        raise VerdictError(f"verdict file is not valid YAML: {e}") from e
    if not isinstance(parsed, dict):
        raise VerdictError("verdict file must be a YAML mapping")
    if not isinstance(parsed.get("bundle_digest"), str) or not parsed["bundle_digest"]:
        raise VerdictError("verdict.bundle_digest must be a non-empty string")
    checklist = parsed.get("checklist")
    if not isinstance(checklist, list) or not checklist:
        raise VerdictError("verdict.checklist must be a non-empty list")
    any_fail = False
    for entry in checklist:
        if not isinstance(entry, dict) or not isinstance(entry.get("item"), str):
            raise VerdictError(f"each checklist entry needs a string `item`: {entry!r}")
        status = entry.get("status")
        if status not in _STATUSES:
            raise VerdictError(f"checklist[{entry.get('item')!r}].status must be one of {sorted(_STATUSES)}")
        any_fail = any_fail or status == "fail"
    findings = parsed.get("findings") or []
    if not isinstance(findings, list):
        raise VerdictError("verdict.findings must be a list")
    for f in findings:
        if not isinstance(f, dict) or f.get("severity") not in _SEVERITIES:
            raise VerdictError(f"each finding needs severity in {sorted(_SEVERITIES)}: {f!r}")
    if any_fail and not findings:
        raise VerdictError("a checklist item is `fail` but findings is empty")
    return parsed


def check_coverage(verdict: dict[str, Any], required_items: list[str]) -> list[str]:
    """Return the required checklist item ids NOT covered by the verdict (in order)."""
    covered = {e["item"] for e in verdict["checklist"]}
    return [i for i in required_items if i not in covered]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_verdict.py tests/unit/core/test_review_verdict.py
git commit -m "feat(review): structured verdict parse + checklist coverage check"
```

---

## Task 6: `paths.py` — `pending_reviews_dir` helper

**Files:**
- Modify: `src/super_harness/core/paths.py` (add after `verification_results_dir`, which ends ~line 129 — it is the last function)
- Test: `tests/unit/core/test_paths.py` (add a test; file exists)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/core/test_paths.py`:

```python
def test_pending_reviews_dir(tmp_path):
    from super_harness.core.paths import pending_reviews_dir
    p = pending_reviews_dir(tmp_path, "2026-06-23-foo")
    assert p == tmp_path / ".harness" / "pending-reviews" / "2026-06-23-foo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_paths.py::test_pending_reviews_dir -v`
Expected: FAIL — `ImportError: cannot import name 'pending_reviews_dir'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/super_harness/core/paths.py`:

```python
def pending_reviews_dir(root: Path, change_id: str) -> Path:
    """Per-change directory for transient review bundles (gitignored — input aid,
    NOT the record of review; the record is the inlined verdict in the event)."""
    return root / ".harness" / "pending-reviews" / change_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_paths.py::test_pending_reviews_dir -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/paths.py tests/unit/core/test_paths.py
git commit -m "feat(paths): pending_reviews_dir helper"
```

---

## Task 7: `review prepare` CLI verb

**Files:**
- Modify: `src/super_harness/cli/review.py` (add a `prepare` command to `review_group`)
- Test: `tests/unit/cli/test_review_prepare.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_review_prepare.py
"""Unit tests for `super-harness review prepare`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.paths import pending_reviews_dir
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _seed_change(ws: Path, declared: list[str]) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    for t, p in [("intent_declared", {}), ("plan_ready", {"scope": {"files": declared}}),
                 ("plan_approved", {}), ("implementation_started", {}),
                 ("implementation_complete", {})]:
        EventWriter(events_path(ws)).emit(Event(
            event_id=new_event_id(), type=t, change_id="c",
            timestamp="2026-06-23T00:00:00Z",
            actor=Actor(type="human", identifier="cli"), framework="plain", payload=p))
    refresh_state_after_emit(ws)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    return tmp_path


def test_prepare_writes_bundle(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_OK, r.output
    out = json.loads(r.output)
    assert out["status"] == "pass"
    bundle_path = pending_reviews_dir(ws, "c") / "code-reviewer.bundle.json"
    assert bundle_path.is_file()
    bundle = json.loads(bundle_path.read_text())
    assert bundle["diff_in_scope"] == ["src/a.py"]
    assert bundle["bundle_digest"]


def test_prepare_dirty_tree_errors(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    (ws / "src" / "a.py").write_text("dirty\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "commit" in r.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_prepare.py -v`
Expected: FAIL — no `prepare` command (`Error: No such command 'prepare'`)

- [ ] **Step 3: Write minimal implementation**

Add imports near the top of `src/super_harness/cli/review.py`:

```python
import json
from super_harness.core.review_bundle import BundleError, assemble_bundle
from super_harness.core.paths import pending_reviews_dir
```

Add this command to `src/super_harness/cli/review.py` (inside the `review_group`):

```python
@review_group.command("prepare")
@click.argument("change")
@_reviewer_opt
@click.option("--base", default=None, help="Base branch for the in-scope diff "
              "(default: .harness/policy.yaml review.base_branch, else main).")
@click.pass_context
def prepare(ctx: click.Context, change: str, reviewer: str, base: str | None) -> None:
    """Assemble the review bundle (diff∩scope + checklist + digest) → disk.

    The harness does NOT review — this hands the reviewer subagent a complete,
    deterministic context to review against. Requires a clean in-scope tree.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="review prepare", message=e.message, hint=e.hint),
                   err=True)
        sys.exit(EXIT_NO_CONFIG)
    try:
        bundle = assemble_bundle(root, change_id=change, reviewer=reviewer, base=base)
    except BundleError as e:
        click.echo(format_error(subcommand="review prepare", message=str(e),
                                hint="Commit the in-scope changes, then re-run review prepare."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    out_dir = pending_reviews_dir(root, change)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / f"{reviewer}.bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if ctx.obj.get("json"):
        click.echo(json_envelope(command="review prepare", status="pass", exit_code=EXIT_OK,
                                 data={"change": change, "reviewer": reviewer,
                                       "bundle_path": str(bundle_path),
                                       "bundle_digest": bundle["bundle_digest"],
                                       "diff_in_scope": bundle["diff_in_scope"],
                                       "out_of_scope": bundle["out_of_scope"]}))
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: wrote review bundle for {change} ({reviewer}) → {bundle_path}")
        if bundle["out_of_scope"]:
            click.echo("  out-of-scope changes (review carefully):\n    "
                       + "\n    ".join(bundle["out_of_scope"]))
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_prepare.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review_prepare.py
git commit -m "feat(review): add `review prepare` bundle-assembly verb"
```

---

## Task 8: Emit-time teeth — `--verdict-file` + reject bare/incomplete/stale code-review approve

**Files:**
- Modify: `src/super_harness/cli/review.py` (`approve` + `reject` commands; add a validation helper)
- Test: `tests/unit/cli/test_review_verdict_gate.py`

The teeth apply ONLY to `approve --reviewer code-reviewer` (slice 1 hardens the content boundary; plan-reviewer + reject accept an optional `--verdict-file` that is inlined when present but not required — see design §4.C / §11).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_review_verdict_gate.py
"""Emit-time verdict teeth for `review approve --reviewer code-reviewer`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.paths import events_path
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _repo_change(tmp_path: Path) -> Path:
    from super_harness.core.events import Actor, Event
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    for t, p in [("intent_declared", {}), ("plan_ready", {"scope": {"files": ["src/"]}}),
                 ("plan_approved", {}), ("implementation_started", {}),
                 ("implementation_complete", {})]:
        EventWriter(events_path(tmp_path)).emit(Event(
            event_id=new_event_id(), type=t, change_id="c",
            timestamp="2026-06-23T00:00:00Z",
            actor=Actor(type="human", identifier="cli"), framework="plain", payload=p))
    refresh_state_after_emit(tmp_path)
    return tmp_path


def _good_verdict(ws: Path, digest: str) -> Path:
    p = ws / "verdict.yaml"
    items = "\n".join(f"  - item: {i}\n    status: pass"
                      for i in ["spec-compliance", "scope-adherence", "code-quality", "edge-cases"])
    p.write_text(f"bundle_digest: {digest}\nchecklist:\n{items}\nfindings: []\n")
    return p


def _prepare_digest(ws: Path) -> str:
    r = CliRunner().invoke(main, ["--json", "--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    return json.loads(r.output)["data"]["bundle_digest"]


def test_bare_approve_rejected(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "verdict" in r.output.lower()


def test_incomplete_checklist_rejected(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    p = ws / "v.yaml"
    p.write_text(f"bundle_digest: {digest}\nchecklist:\n  - item: spec-compliance\n    status: pass\nfindings: []\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "scope-adherence" in r.output  # names a missing item


def test_stale_digest_rejected(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    _prepare_digest(ws)
    p = _good_verdict(ws, "stale-does-not-match")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "stale" in r.output.lower() or "digest" in r.output.lower()


def test_complete_fresh_verdict_passes_and_inlines(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output
    # verdict inlined into the emitted event payload
    last = [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()][-1]
    assert last["type"] == "code_review_passed"
    assert last["payload"]["verdict"]["bundle_digest"] == digest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_verdict_gate.py -v`
Expected: FAIL — bare approve currently succeeds; `--verdict-file` is an unknown option.

- [ ] **Step 3: Write minimal implementation**

Add imports to `src/super_harness/cli/review.py`:

```python
from super_harness.core.reducer import derive_state
from super_harness.core.review_bundle import load_base_branch
from super_harness.core.review_checklist import resolve_checklist
from super_harness.core.review_verdict import VerdictError, check_coverage, parse_verdict_file
from super_harness.core.scope_match import (
    GitScopeError, committed_scope_digest, split_changed_by_scope, working_tree_dirty,
)
```

Add a validation helper to `src/super_harness/cli/review.py`:

```python
def _validate_code_review_verdict(
    root: Path, change: str, reviewer: str, verdict_file: str | None, base: str | None,
    subcommand: str,
) -> dict[str, object]:
    """Validate the structured verdict for a code-review approval (emit-time teeth).

    Returns the parsed verdict dict to inline into the event payload, or exits
    (EXIT_VALIDATION) with a structured error. Fail-closed on git errors.
    """
    if not verdict_file:
        click.echo(format_error(subcommand=subcommand,
            message="code-reviewer approval requires a structured verdict.",
            hint="Run `review prepare`, review the bundle, then pass --verdict-file <path>."),
            err=True)
        sys.exit(EXIT_VALIDATION)
    try:
        verdict = parse_verdict_file(Path(verdict_file))
    except VerdictError as e:
        click.echo(format_error(subcommand=subcommand, message=str(e)), err=True)
        sys.exit(EXIT_VALIDATION)

    required = resolve_checklist(root, reviewer)
    missing = check_coverage(verdict, required)
    if missing:
        click.echo(format_error(subcommand=subcommand,
            message=f"verdict does not cover every checklist item; missing: {', '.join(missing)}",
            hint="Every checklist item must have a status (pass/fail/na)."), err=True)
        sys.exit(EXIT_VALIDATION)

    resolved_base = base or load_base_branch(root)
    cs = derive_state(events_path(root)).get(change)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    if working_tree_dirty(root, declared):
        click.echo(format_error(subcommand=subcommand,
            message="in-scope files have uncommitted changes; cannot verify the reviewed diff.",
            hint="Commit the in-scope changes and re-run review prepare + approve."), err=True)
        sys.exit(EXIT_VALIDATION)
    try:
        in_scope, _ = split_changed_by_scope(root, base=resolved_base, declared=declared)
        current = committed_scope_digest(root, base=resolved_base, in_scope=in_scope)
    except GitScopeError as e:
        click.echo(format_error(subcommand=subcommand,
            message=f"cannot verify review freshness (git error): {e}",
            hint="Resolve the git/base-branch issue; the gate fails closed."), err=True)
        sys.exit(EXIT_VALIDATION)
    if verdict["bundle_digest"] != current:
        click.echo(format_error(subcommand=subcommand,
            message="verdict is stale — its bundle_digest does not match the current in-scope diff.",
            hint="The code changed since `review prepare`; re-prepare and re-review."), err=True)
        sys.exit(EXIT_VALIDATION)
    return verdict
```

Then modify the `approve` command to add the options and call the helper for code-reviewer:

```python
@review_group.command("approve")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="approved", help="Audit reason recorded on the event.")
@click.option("--verdict-file", default=None, help="Structured verdict file "
              "(REQUIRED for code-reviewer; see `review prepare`).")
@click.option("--base", default=None, help="Base branch for freshness check "
              "(default: policy.yaml review.base_branch, else main).")
@_as_opt
@click.pass_context
def approve(ctx: click.Context, change: str, reviewer: str, reason: str,
            verdict_file: str | None, base: str | None, as_identity: str | None) -> None:
    """Record a PASS verdict: emit `plan_approved` / `code_review_passed`."""
    extra: dict[str, object] | None = None
    if reviewer == "code-reviewer":
        try:
            root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
        except HarnessNotInitialized as e:
            click.echo(format_error(subcommand="review approve", message=e.message, hint=e.hint),
                       err=True)
            sys.exit(EXIT_NO_CONFIG)
        verdict = _validate_code_review_verdict(
            root, change, reviewer, verdict_file, base, "review approve")
        extra = {"verdict": verdict}
    elif verdict_file:  # plan-reviewer: inline if provided, not required (advisory this slice)
        try:
            extra = {"verdict": parse_verdict_file(Path(verdict_file))}
        except VerdictError as e:
            click.echo(format_error(subcommand="review approve", message=str(e)), err=True)
            sys.exit(EXIT_VALIDATION)
    _emit_verdict(
        ctx, subcommand="review approve", change=change, reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer], reason=reason, as_identity=as_identity,
        extra_payload=extra,
    )
```

Also add `--verdict-file` to `reject` (inline if provided, never required — a reject can stop early):

```python
# in reject(): add the same --verdict-file option (default None); before _emit_verdict:
extra = None
if verdict_file:
    try:
        extra = {"verdict": parse_verdict_file(Path(verdict_file))}
    except VerdictError as e:
        click.echo(format_error(subcommand="review reject", message=str(e)), err=True)
        sys.exit(EXIT_VALIDATION)
# pass extra_payload=extra into _emit_verdict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_verdict_gate.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Fix the two pre-existing bare-approve tests, then run the suite**

Exactly TWO existing tests do a bare `review approve --reviewer code-reviewer` and will now break (verified): `test_review_approve_records_as_identity` (`tests/unit/cli/test_review.py:174`) and `test_review_approve_default_identity_via_resolver` (`tests/unit/cli/test_review.py:206`). Both build state via `_seed()` (events only, **no git repo**), so the new code-reviewer teeth hit `GitScopeError` → fail-closed → EXIT_VALIDATION; **adding `--verdict-file` does NOT rescue them**. They assert identity recording, not code-review semantics, so the correct fix is to switch each to `--reviewer plan-reviewer` (no teeth this slice, no git dependency) and leave their identity assertions intact. (The `skip`/`reject` code-reviewer tests — `test_review_skip_sets_structured_marker` and the reject cases — are unaffected: skip/reject get no teeth this slice.)

Make those two edits, then run:
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review.py tests/unit/cli/test_review_prepare.py tests/unit/cli/test_review_verdict_gate.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review_verdict_gate.py tests/unit/cli/test_review.py
git commit -m "feat(review): emit-time teeth — reject bare/incomplete/stale code-review approve"
```

---

## Task 9: Doc sync (in scope — required by AGENTS.md doc rules)

**Files:**
- Modify: `docs/cli-reference.md` (regenerate), `AGENTS.md` (review-protocol section), `.harness/sensors.yaml` + the templated copy under `src/super_harness/templates/` (annotate `plan-reviewer` example as v0.2/unbuilt)

- [ ] **Step 1: Add exit-code entries for the new/changed leaves, then regenerate**

`docs/cli-reference.md` is regenerated by `scripts/gen_cli_reference.py`, which sources per-command exit codes from a hand-maintained `_EXIT_CODES` map (unknown leaves fall back to a generic `0/1` block — inaccurate for our new gates). First add/update entries in `scripts/gen_cli_reference.py` `_EXIT_CODES`:
- `review prepare`: `0` ok / `2` validation (dirty tree / git error) / `3` no `.harness/`.
- `review approve`: add `2` (verdict gate: bare/incomplete/stale) and `3` (no config) to its existing entry.

Then regenerate in place: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix`
Expected: `docs/cli-reference.md` now lists `review prepare` and the new `--verdict-file`/`--base` options on `review approve`/`reject`.

- [ ] **Step 2: Verify no doc drift**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check`
Expected: PASS (exit 0 — no drift between the click surface and `docs/cli-reference.md`). `doc check` owns regen-and-diff; there is no `sync check` verb (`super-harness sync` is a single command that re-renders AGENTS.md/.gitignore, not the cli-reference).

- [ ] **Step 3: Update the AGENTS.md review protocol**

In `AGENTS.md`, in the `#### Review protocol` section (~line 52), add to the `AWAITING_CODE_REVIEW` guidance: the agent must (1) `super-harness review prepare <change> --reviewer code-reviewer`, (2) hand the bundle to its reviewer subagent, (3) record the verdict with `super-harness review approve <change> --reviewer code-reviewer --verdict-file <path>`. State plainly: a bare `review approve --reviewer code-reviewer` is now rejected; the in-scope files must be committed before `review prepare` (the digest is over committed HEAD). Note plan-reviewer is unchanged this slice.

- [ ] **Step 4: Annotate the sensors.yaml `plan-reviewer` example**

In `.harness/sensors.yaml` (and the templated copy used by `init`, under `src/super_harness/templates/`), add a comment on the `plan-reviewer` example noting it is **aspirational / unbuilt (v0.2)** — review is not a daemon sensor; see design §7.

- [ ] **Step 5: Run the whole suite once**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest -q`
Expected: PASS (full suite green).

- [ ] **Step 6: Commit**

```bash
git add docs/cli-reference.md AGENTS.md .harness/sensors.yaml src/super_harness/templates/ scripts/gen_cli_reference.py
git commit -m "docs(review): sync cli-reference + AGENTS.md review protocol + sensors note"
```

---

## Self-Review (run after writing all tasks; checklist, not a subagent)

1. **Spec coverage (design §4 slice-1 = A+B+C+checklist):** A `review prepare` = Task 7; B structured verdict (parse + inline) = Tasks 5, 8; C emit-time teeth (bare/incomplete/stale + clean-tree + fail-closed) = Task 8; digest semantics (committed HEAD, explicit base, fail-closed) = Tasks 1, 4, 8; configurable checklist = Task 3; reuse/extract scope matcher = Tasks 1, 2; pending-reviews path = Task 6; doc sync + sensors annotation = Task 9. No slice-1 requirement is unmapped. (D, E, reducer-verdict retention = slice 2, explicitly excluded.)
2. **Placeholder scan:** every code step has complete code; no TBD/TODO except the documented future-slice note. All commands are explicit (Task 9 uses the real `super-harness doc check [--fix]`); no "find the command" guesswork remains.
3. **Type consistency:** `covered_by_scope`, `split_changed_by_scope`, `committed_scope_digest`, `working_tree_dirty`, `GitScopeError` (Task 1) are used verbatim in Tasks 4, 8. `assemble_bundle`/`load_base_branch`/`BundleError` (Task 4) used in Task 7. `parse_verdict_file`/`check_coverage`/`VerdictError` (Task 5) used in Task 8. `resolve_checklist`/`DEFAULT_CHECKLISTS` (Task 3) used in Tasks 4, 8. `pending_reviews_dir` (Task 6) used in Task 7. Bundle dict keys (`diff_in_scope`, `out_of_scope`, `bundle_digest`, `checklist`) consistent across Tasks 4, 7, 8 and tests.

## Self-host bootstrap reminder (design §10)

This change is dogfooded through its own lifecycle, and Task 8 changes the meaning of `review approve --reviewer code-reviewer` — so THIS change's own approval must use the new contract: run `review prepare` for this change, produce a genuine `--verdict-file`, then `review approve ... --verdict-file <path>` before `attest write`. Old merged attestations are not retroactively broken (`attest verify` checks milestone presence, not verdict shape). `plan ready --scope` must cover every file touched by Tasks 1–9 (src + tests + docs/cli-reference.md + AGENTS.md + .harness/sensors.yaml + templates + this plan + the design doc).

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-23-auto-review-hardening-slice1-implementation.md`. No Flow context (this project uses plain superpowers — see memory `feedback-super-harness-no-flow-context`). Claude Code has subagents → execute with **superpowers:subagent-driven-development** (fresh subagent per task + two-stage spec/quality review), after the plan clears 2 rounds of adversarial review.
