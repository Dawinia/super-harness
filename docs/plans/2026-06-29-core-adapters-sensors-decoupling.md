# Cut A+B — Decouple `core` from `sensors` (arm sensors in core-is-base) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every `core → sensors` import path so `super_harness.sensors` can be armed into the `core-is-base` import-linter contract and the gate locks it.

**⚠️ Mid-implementation discovery (2026-06-30):** After Cut A landed, arming `sensors` surfaced a **second** `core → sensors` path the grep + two independent reviews all missed — `core.sync_check → engineering.agents_md_render → adapters.registry → adapters → sensors`. Both paths share one root edge: `adapters/__init__.py:48` re-exports `WorkspaceContext` from `sensors`. So **Cut A alone cannot arm sensors** — the real fix is **Cut B** (relocate `WorkspaceContext` so `adapters ↛ sensors`), which severs the sensors tail of *both* paths. This vindicates G-FITNESS: only the real import-graph engine caught the transitive edge. Cut B is folded into this PR (user decision, 2026-06-30).

**Architecture:** *Cut A* (cleanup): `core.review_bundle._spec_plan_paths` was a `core → adapters` edge (function-local import dodging a cycle). Move that resolution to `adapters.registry.resolve_spec_plan_paths`; `assemble_bundle` takes an injected `spec_plan_resolver` callback (default no-op); the sole caller `cli/review.py` passes the real resolver. *Cut B* (the load-bearing fix): `WorkspaceContext` (a 4-field frozen dataclass) moves from `sensors/__init__.py` to a new pure `core/workspace.py`; `sensors/__init__` re-exports it (every existing `from super_harness.sensors import WorkspaceContext` keeps working — zero change to the 6 other importers); `adapters/__init__` imports it from `core`. This deletes the only `adapters → sensors` edge, so `core` no longer reaches `sensors` by any path. Then add `super_harness.sensors` to the `.importlinter` `core-is-base` forbidden list and re-ratify the text-locked `d-core-is-base` decision. (`core → adapters` via `sync_check → engineering` remains — allowed, since the contract forbids only sensors, not adapters.)

**Tech Stack:** Python 3.10+, `import-linter>=2.0` (already in `[dev]`), `pytest`, `click` (CLI), super-harness self-host merge-gate lifecycle.

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `src/super_harness/adapters/registry.py` | New `resolve_spec_plan_paths(framework, root, change_id)` — adapter-backed spec/plan path derivation (the old `core._spec_plan_paths` body, moved to a layer allowed to import adapters). | Modify |
| `src/super_harness/core/review_bundle.py` | Drop the `adapters` import + `_spec_plan_paths`; `assemble_bundle` gains an injected `spec_plan_resolver` (default no-op). Core stays adapter/sensor-free. | Modify |
| `src/super_harness/cli/review.py` | `review prepare` passes the real `resolve_spec_plan_paths` into `assemble_bundle`. | Modify |
| `src/super_harness/core/workspace.py` | **Cut B** — new home of the `WorkspaceContext` dataclass (moved from `sensors/__init__.py`). Pure: stdlib only. | Create |
| `src/super_harness/sensors/__init__.py` | **Cut B** — delete the `WorkspaceContext` class body; re-export it from `core.workspace` (back-compat for all existing importers). | Modify |
| `src/super_harness/adapters/__init__.py` | **Cut B** — import `WorkspaceContext` from `core.workspace` instead of `sensors` (deletes the only `adapters → sensors` edge); update the line-16 docstring note. | Modify |
| `tests/unit/core/test_workspace.py` | **Cut B** — tests for the relocated dataclass + re-export identity from `sensors`. | Create |
| `.importlinter` | Add `super_harness.sensors` to `forbidden_modules` **and** update the contract `name` (line 14) so it no longer reads only "cli/gates". | Modify |
| `src/super_harness/core/__init__.py` | Update the `d-core-is-base` anchor docstring (lines 1-8) — it still says "must not import the orchestration layers (cli, gates)"; add `sensors`. (Prose only; the `# @decision:d-core-is-base` marker is untouched.) | Modify |
| `docs/decisions/d-core-is-base.md` | Update prose (sensors now covered + how the edge was removed); re-ratify → new `ratified_text_hash`. | Modify |
| `tests/unit/adapters/test_registry.py` | Tests for `resolve_spec_plan_paths` (plain/openspec/unknown/no-framework). | Modify |
| `tests/unit/core/test_review_bundle.py` | Tests: injected resolver populates spec/plan; no resolver → empty. | Modify |
| `tests/unit/cli/test_review_prepare.py` | Test: `review prepare` wires the resolver (openspec change → populated `spec_path`). | Modify |
| `private/OPEN-ITEMS.md` | Mark the G-FITNESS `core→sensors` residue resolved; keep Cut B as next. **LOCAL-ONLY: `private/` is gitignored (`.gitignore:2`) — NOT committed, NOT in merge-gate scope.** | Modify (local) |

**Verification commands** (run with the project venv on PATH — never `uv run`):
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
```

---

### Task 0: Prove the contract catches the coupling (pristine code, reverted)

Do this **first, on unmodified code**, so the red proof is sound. (Doing it after Tasks 1-3 commit the fix would be unsound — `git stash` only stashes *uncommitted* work, so it cannot recreate the old coupling. This is why we prove red up front instead.)

**Files:** `.importlinter` (temporary edit, reverted in Step 3 — not committed).

- [ ] **Step 1: Temporarily arm sensors against the pristine tree**

In `.importlinter`, under `[importlinter:contract:core-is-base]` → `forbidden_modules`, add the sensors line:

```ini
forbidden_modules =
    super_harness.cli
    super_harness.gates
    super_harness.sensors
```

- [ ] **Step 2: Run the contract and observe it BREAK on the current coupling**

Run: `PATH="$(pwd)/.venv/bin:$PATH" PYTHONPATH=src lint-imports --config .importlinter --no-cache`
Expected: `core is the base layer ... BROKEN`, with a reported chain `super_harness.core.review_bundle -> super_harness.adapters -> super_harness.sensors`. This proves the armed contract has teeth against the real pre-fix code.

- [ ] **Step 3: Revert the temporary contract edit**

Run: `git checkout -- .importlinter`
Expected: `.importlinter` back to the committed `cli`/`gates`-only form. (Tasks 1-3 do the refactor; Task 4 re-arms sensors for real, against the fixed code.)

---

### Task 1: Move spec/plan-path resolution into `adapters.registry`

**Files:**
- Modify: `src/super_harness/adapters/registry.py`
- Test: `tests/unit/adapters/test_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/adapters/test_registry.py`:

```python
def test_resolve_spec_plan_paths_openspec(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    spec, plan = resolve_spec_plan_paths("openspec", tmp_path, "c")
    assert spec == str(tmp_path / "openspec" / "changes" / "c" / "proposal.md")
    assert plan == str(tmp_path / "openspec" / "changes" / "c" / "tasks.md")


def test_resolve_spec_plan_paths_plain_is_empty(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    assert resolve_spec_plan_paths("plain", tmp_path, "c") == ("", "")


def test_resolve_spec_plan_paths_no_framework(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    assert resolve_spec_plan_paths(None, tmp_path, "c") == ("", "")
    assert resolve_spec_plan_paths("", tmp_path, "c") == ("", "")


def test_resolve_spec_plan_paths_unknown_framework(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    assert resolve_spec_plan_paths("no-such-fw", tmp_path, "c") == ("", "")
```

Confirm `from pathlib import Path` is already imported at the top of the test file; if not, add it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/adapters/test_registry.py -k resolve_spec_plan_paths -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_spec_plan_paths'`.

- [ ] **Step 3: Add the function to `adapters/registry.py`**

Add this function (e.g. just after `get_builtin`, around line 82). `Path`, `FrameworkAdapter`, and `get_builtin` are all already imported in this module:

```python
def resolve_spec_plan_paths(
    framework: str | None, root: Path, change_id: str
) -> tuple[str, str]:
    """Resolve ``(spec_path, plan_path)`` for ``change_id`` via its framework adapter.

    Pure path derivation — delegates to the builtin adapter's ``spec_paths``.
    Returns ``("", "")`` when ``framework`` is falsy or has no builtin adapter.

    Lives here (not in ``core``) so ``core.review_bundle`` stays free of any
    ``adapters`` import: the review-bundle assembler takes this as an injected
    resolver. See decision ``d-core-is-base`` (core is the base layer; it must
    not import the upper layers, including ``adapters``/``sensors``).
    """
    if not framework:
        return "", ""
    cls = get_builtin(framework)
    if cls is None or not issubclass(cls, FrameworkAdapter):
        return "", ""
    paths = cls().spec_paths(root, change_id)
    return paths.get("spec", ""), paths.get("plan", "")
```

Add `"resolve_spec_plan_paths"` to the module `__all__` list (keep it sorted).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/adapters/test_registry.py -k resolve_spec_plan_paths -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/registry.py tests/unit/adapters/test_registry.py
git commit -m "feat(adapters): add resolve_spec_plan_paths (spec/plan path derivation off core)"
```

---

### Task 2: Inject the resolver into `assemble_bundle`; drop core's `adapters` import

**Files:**
- Modify: `src/super_harness/core/review_bundle.py:57-67` (delete `_spec_plan_paths`), `:70-104` (signature + body)
- Test: `tests/unit/core/test_review_bundle.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/core/test_review_bundle.py`:

```python
def test_assemble_bundle_uses_injected_resolver(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])

    seen: dict[str, object] = {}

    def fake_resolver(framework: str | None, root: Path, change_id: str) -> tuple[str, str]:
        seen["framework"] = framework
        seen["change_id"] = change_id
        return "specs/proposal.md", "specs/tasks.md"

    b = assemble_bundle(
        ws, change_id="c", reviewer="code-reviewer", base="main",
        spec_plan_resolver=fake_resolver,
    )
    assert b["spec_path"] == "specs/proposal.md"
    assert b["plan_path"] == "specs/tasks.md"
    # core derived the framework from state and handed it to the injected resolver
    assert seen == {"framework": "plain", "change_id": "c"}


def test_assemble_bundle_no_resolver_yields_empty_spec_plan(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["spec_path"] == ""
    assert b["plan_path"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/core/test_review_bundle.py -k "injected_resolver or no_resolver" -v`
Expected: FAIL — `TypeError: assemble_bundle() got an unexpected keyword argument 'spec_plan_resolver'`.

- [ ] **Step 3: Refactor `review_bundle.py`**

(a) Add the `Callable` import. After `from typing import Any` (line 13) add:

```python
from collections.abc import Callable
```

(b) Delete the entire `_spec_plan_paths` function (lines 57-67, including the two function-local `from super_harness.adapters ...` imports).

(c) In its place add the resolver type alias + default no-op:

```python
# A spec/plan path resolver: (framework, root, change_id) -> (spec_path, plan_path).
# Injected by the caller so core stays free of any `adapters` import (decision
# d-core-is-base: core is the base layer). cli/review.py wires the adapters-backed
# resolver (adapters.registry.resolve_spec_plan_paths).
SpecPlanResolver = Callable[[str | None, Path, str], tuple[str, str]]


def _no_spec_plan(framework: str | None, root: Path, change_id: str) -> tuple[str, str]:
    """Default resolver: no framework spec/plan paths.

    Used when the caller wires no resolver (e.g. core-only tests). Keeps the
    bundle shape stable (``spec_path``/``plan_path`` present but empty).
    """
    return "", ""
```

(d) Change the `assemble_bundle` signature to add the parameter:

```python
def assemble_bundle(
    root: Path,
    *,
    change_id: str,
    reviewer: str,
    base: str | None = None,
    spec_plan_resolver: SpecPlanResolver | None = None,
) -> dict[str, Any]:
```

(e) Replace the body line `spec_path, plan_path = _spec_plan_paths(framework, root, change_id)` (line 93) with:

```python
    resolve = spec_plan_resolver or _no_spec_plan
    spec_path, plan_path = resolve(framework, root, change_id)
```

- [ ] **Step 4: Run the tests to verify they pass (and nothing regressed)**

Run: `pytest tests/unit/core/test_review_bundle.py -v`
Expected: PASS — the two new tests plus the existing `test_assemble_bundle_happy` / `_rejects_dirty_in_scope_tree` / `_empty_scope_inert_digest` / `load_base_branch` (existing tests use `framework="plain"` → spec/plan empty under both old and new code).

- [ ] **Step 5: Verify core no longer imports `adapters`**

Match `import` statements only (the new explanatory comments mention `adapters`/`adapters.registry` in prose, so a bare `grep adapters` would self-match):

Run: `grep -nE "^[[:space:]]*(from|import)[[:space:]]+super_harness\.(adapters|sensors)" src/super_harness/core/review_bundle.py`
Expected: NO matches (the two function-local `from super_harness.adapters ...` lines are gone; no `sensors` import). The authoritative check is `lint-imports` in Task 4 — this grep is just a fast local sanity check.

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/core/review_bundle.py tests/unit/core/test_review_bundle.py
git commit -m "refactor(core): inject spec/plan resolver into assemble_bundle (drop core->adapters edge)"
```

---

### Task 3: Wire the real resolver in `cli/review.py`

**Files:**
- Modify: `src/super_harness/cli/review.py:42` (import), `:350` (call site)
- Test: `tests/unit/cli/test_review_prepare.py`

- [ ] **Step 1: Parameterize the seed helper, then write the failing test**

The file's real helpers are `_seed_change(ws, declared)` (`tests/unit/cli/test_review_prepare.py:19`, which hardcodes `framework="plain"` in its `Event(...)` call at line ~34) and `_repo(tmp_path)` (`:39`). First parameterize the framework on `_seed_change` (default preserves every existing caller):

```python
def _seed_change(ws: Path, declared: list[str], framework: str = "plain") -> None:
    # ... unchanged body, except the Event construction now threads the framework:
        EventWriter(events_path(ws)).emit(Event(
            event_id=new_event_id(), type=t, change_id="c",
            timestamp="2026-06-23T00:00:00Z",
            actor=Actor(type="human", identifier="cli"), framework=framework, payload=p))
```

Then add a sibling test that seeds an `openspec` change and asserts the bundle's `spec_path`/`plan_path` are populated — proving the CLI wires the resolver (mirrors `test_prepare_writes_bundle`):

```python
def test_prepare_wires_resolver_for_openspec(tmp_path: Path) -> None:
    import json

    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"], framework="openspec")
    r = CliRunner().invoke(
        main,
        ["--json", "--workspace", str(ws), "review", "prepare", "c",
         "--reviewer", "code-reviewer", "--base", "main"],
    )
    assert r.exit_code == 0, r.output
    bundle = json.loads(
        (ws / ".harness" / "pending-reviews" / "c" / "code-reviewer.bundle.json").read_text()
    )
    assert bundle["spec_path"].endswith("openspec/changes/c/proposal.md")
    assert bundle["plan_path"].endswith("openspec/changes/c/tasks.md")
```

(`resolve_spec_plan_paths` is pure path derivation, so no on-disk `openspec/` directory is needed — the asserted paths are derived, not read.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_review_prepare.py -k wires_resolver -v`
Expected: FAIL — `spec_path` is `""` (review.py still calls `assemble_bundle` without a resolver after Task 2, so the default no-op applies), so `"".endswith(...)` is False.

- [ ] **Step 3: Wire the resolver in `cli/review.py`**

(a) Add the import near the existing `from super_harness.core.review_bundle import ...` (line 42):

```python
from super_harness.adapters.registry import resolve_spec_plan_paths
```

(b) At the call site (line 350), pass the resolver:

```python
        bundle = assemble_bundle(
            root, change_id=change, reviewer=reviewer, base=base,
            spec_plan_resolver=resolve_spec_plan_paths,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_review_prepare.py -v`
Expected: PASS — the new test plus the existing `test_prepare_writes_bundle` / `test_prepare_dirty_tree_errors`.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review_prepare.py
git commit -m "feat(cli): review prepare wires adapters spec/plan resolver into assemble_bundle"
```

---

### Task 3B: Cut B — relocate `WorkspaceContext` to `core` (sever `adapters → sensors`)

**Files:**
- Create: `src/super_harness/core/workspace.py`
- Modify: `src/super_harness/sensors/__init__.py` (delete class body; re-export)
- Modify: `src/super_harness/adapters/__init__.py:16,48`
- Test: `tests/unit/core/test_workspace.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/test_workspace.py`:

```python
"""Tests for the relocated WorkspaceContext (Cut B — now core-owned)."""
from __future__ import annotations

from pathlib import Path


def test_workspace_context_lives_in_core() -> None:
    from super_harness.core.workspace import WorkspaceContext

    ctx = WorkspaceContext(workspace_root=Path("/x"))
    assert ctx.workspace_root == Path("/x")
    assert ctx.git_branch is None
    assert ctx.active_change_id is None
    assert ctx.framework is None


def test_sensors_reexports_same_class() -> None:
    from super_harness.core.workspace import WorkspaceContext as Core
    from super_harness.sensors import WorkspaceContext as Sensors

    assert Core is Sensors  # sensors re-exports the core definition (single source of truth)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/core/test_workspace.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'super_harness.core.workspace'`.

- [ ] **Step 3: Create `core/workspace.py`**

```python
"""Workspace snapshot type shared across the harness (core-owned base type).

`WorkspaceContext` is the read-only snapshot passed to every `Sensor.check()`
call and consumed by adapters/CLI. It lives in `core` (the base layer) so neither
`sensors` nor `adapters` has to own it — `sensors` re-exports it for back-compat.
See decision d-core-is-base: core is the base layer; the upper layers depend on
it, not vice-versa.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    """Read-only snapshot of the workspace passed to every Sensor.check() call.

    See sensor-gate-architecture spec §2.1.
    """

    workspace_root: Path
    git_branch: str | None = None
    active_change_id: str | None = None
    # Framework name of the active change (HG-01), used by the verification runner
    # to resolve `${SPEC_PATH}`/`${PLAN_PATH}` via the adapter's `spec_paths`.
    # None → those vars stay empty. Defaulted so every existing construction site
    # (and sensors that don't need it) keep working unchanged.
    framework: str | None = None
```

- [ ] **Step 4: In `sensors/__init__.py`, delete the class body and re-export**

Delete the entire `@dataclass(frozen=True) class WorkspaceContext: ...` block (lines ~60-74) and add a re-export near the existing `from super_harness.core.events import Event` import:

```python
from super_harness.core.events import Event
from super_harness.core.workspace import WorkspaceContext
```

Keep `"WorkspaceContext"` in `__all__` (now a re-export). If `ruff` then flags `from pathlib import Path` as unused in this file, remove it (verify `Path` has no other use in `sensors/__init__.py` first).

- [ ] **Step 5: In `adapters/__init__.py`, import from core instead of sensors**

Change line 48 `from super_harness.sensors import WorkspaceContext` → `from super_harness.core.workspace import WorkspaceContext`, and update the line-16 docstring note from "re-exported from super_harness.sensors (single source of truth)" → "re-exported from super_harness.core.workspace (single source of truth)".

- [ ] **Step 6: Run the new test + the back-compat suites**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
pytest tests/unit/core/test_workspace.py tests/unit/sensors tests/unit/adapters tests/unit/cli/test_verify.py -q
```
Expected: PASS — the relocation is transparent to every existing `from super_harness.sensors import WorkspaceContext` consumer.

- [ ] **Step 7: Verify `adapters ↛ sensors` is gone, then commit**

Run: `grep -rn "import super_harness.sensors\|from super_harness.sensors" src/super_harness/adapters/`
Expected: NO matches (the docstring note no longer says "sensors", the import now targets `core.workspace`).

```bash
git add src/super_harness/core/workspace.py src/super_harness/sensors/__init__.py src/super_harness/adapters/__init__.py tests/unit/core/test_workspace.py
git commit -m "refactor: relocate WorkspaceContext to core.workspace (sever adapters->sensors edge)"
```

---

### Task 4: Arm `sensors` in the `core-is-base` contract + re-ratify the decision

**Files:**
- Modify: `.importlinter` (forbidden_modules + contract `name` on line 14)
- Modify: `src/super_harness/core/__init__.py` (anchor docstring prose)
- Modify: `docs/decisions/d-core-is-base.md` (body prose + re-ratify)

(The red proof that the armed contract bites the *old* coupling was done soundly in Task 0 against pristine code. Here we arm it for real against the *fixed* code and confirm green; the bite-test in Step 4 independently re-proves teeth via the counterexample.)

- [ ] **Step 1: Arm sensors + update the contract name**

In `.importlinter`, add the sensors line to `forbidden_modules`:

```ini
forbidden_modules =
    super_harness.cli
    super_harness.gates
    super_harness.sensors
```

And update the contract `name` on line 14 so it no longer claims only cli/gates:

```ini
name = core is the base layer (must not import cli/gates/sensors)
```

- [ ] **Step 2: Verify the contract is green with the fix in place**

Run: `PATH="$(pwd)/.venv/bin:$PATH" PYTHONPATH=src lint-imports --config .importlinter --no-cache`
Expected: `core is the base layer (must not import cli/gates/sensors) KEPT` — `Contracts: 1 kept, 0 broken.`

- [ ] **Step 3: Update the `core/__init__.py` anchor docstring**

`src/super_harness/core/__init__.py` carries the `# @decision:d-core-is-base` anchor and its docstring still documents only `cli`/`gates`. Update the docstring prose (lines 1-8) to include `sensors` — leave the `# @decision:d-core-is-base` marker line itself unchanged:

```python
"""super_harness.core — the pure base layer.

Must not import the upper layers (`cli`, `gates`, `sensors`), directly or
transitively, so the core can be imported (e.g. by the daemon) without dragging
in the CLI/gate/sensor stack. This invariant is enforced as a rung-1
architecture-fitness check; see the `core-is-base` contract in `.importlinter`.
"""
# @decision:d-core-is-base
```

- [ ] **Step 4: Update the decision prose to reflect reality**

In `docs/decisions/d-core-is-base.md`, replace the body (everything after the frontmatter `---`, keeping the ` ```check ` and ` ```counterexample ` blocks unchanged) with:

```markdown
core/ is the base layer: it must not import the upper layers cli/gates/sensors.

`super_harness.core` is the pure foundation the upper layers build on. It must not
depend (directly OR transitively) on the upper layers `super_harness.cli`,
`super_harness.gates`, or `super_harness.sensors`, so the core can be imported (e.g. by
the daemon) without dragging in the CLI/gate/sensor stack. The faithful mechanical form
of this invariant is an import-graph contract, not a text grep: `grep` sees only direct
textual imports and is blind to the transitive and function-local edges that actually
break layering. The rung-1 check is the import-linter `core-is-base` contract in
`.importlinter`.

(`sensors` is now covered too. The former `core.review_bundle -> adapters -> sensors`
transitive edge — a real coupling import-linter caught that grep declared clean — was
removed by injecting the spec/plan-path resolver from the caller
(`adapters.registry.resolve_spec_plan_paths`) instead of importing `adapters` inside
core.)
```

Leave the ` ```check ` block and the ` ```counterexample ... ``` ` block exactly as they are — the counterexample still imports `cli`, so it still makes the contract fail (teeth intact).

- [ ] **Step 5: Prove the check still bites, then re-ratify (re-stamp the text hash)**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-core-is-base --dry-run
```
Expected: bite-test passes (the counterexample makes `core-is-base` BROKEN → the check proves it bites).

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-core-is-base
```
Expected: re-ratified — `ratified_text_hash` re-stamped to the new body's hash, `ratified_by`/`ratified_at` updated.

- [ ] **Step 6: Verify decision integrity is clean**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check`
Expected: no integrity violation (body hash matches the freshly stamped `ratified_text_hash`), no dangling anchors for `d-core-is-base`.

- [ ] **Step 7: Commit**

```bash
git add .importlinter src/super_harness/core/__init__.py docs/decisions/d-core-is-base.md
git commit -m "feat(fitness): arm sensors in core-is-base contract; re-ratify d-core-is-base"
```

---

### Task 5: Full verification + close the OPEN-ITEMS residue

**Files:**
- Modify (local-only, gitignored): `private/OPEN-ITEMS.md`

- [ ] **Step 1: Run the full test suite + linters**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
pytest -v -m "not e2e"
ruff check src tests
mypy
```
Expected: all green. Pay attention to `tests/unit/core/test_review_bundle.py`, `tests/unit/adapters/test_registry.py`, `tests/unit/cli/test_review_prepare.py`, and any `test_check_runner` / decision tests.

- [ ] **Step 2: Confirm no derived-doc drift (AGENTS.md / doc refs)**

This change adds no CLI command/event/state, but the decision body changed. Refresh derived docs and check:

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md -y
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix
git status --porcelain
```
Expected: if either command modifies a file, `git add` it and fold it into the change scope (merge-gate lifecycle). If nothing changes, proceed.

- [ ] **Step 3: Mark the G-FITNESS `core→sensors` residue resolved in OPEN-ITEMS (LOCAL-ONLY — do NOT commit)**

`private/` is gitignored (`.gitignore:2`), so `private/OPEN-ITEMS.md` is a local working note — edit it but do **not** `git add` it (it is not tracked and must not be in merge-gate scope).

In `private/OPEN-ITEMS.md`, under `## G-FITNESS architecture-fitness rung-1 ... — residue`, update the first bullet (the `core` transitively imports `sensors` item) to mark it resolved, e.g. prefix it with `✅ RESOLVED (2026-06-29, Cut A — sensors now in core-is-base; core→adapters edge removed via injected resolver).` and keep the "expand coverage" + "let it bleed" bullets. Add a one-line pointer that Cut B (relocate `WorkspaceContext` to core + arm `adapters ⊥ sensors`) is the next increment.

No commit step — this is local bookkeeping only.

---

## Merge-gate lifecycle (self-host execution wrapper)

Run the whole plan inside the self-host merge-gate sequence (proven across #50–#55). Use a date-prefixed slug, e.g. `2026-06-29-core-adapters-decoupling`.

1. `git checkout -b 2026-06-29-core-adapters-decoupling`
2. `super-harness change start 2026-06-29-core-adapters-decoupling` (intent)
3. **Before `plan ready`:** run `sync --agents-md -y` + `doc check --fix` once to surface any derived-doc drift, so the full file set is known.
4. `super-harness plan ready 2026-06-29-core-adapters-decoupling --tier-hint <t> --scope @scope.yaml` where `scope.yaml` lists **every** file touched:
   - `src/super_harness/adapters/registry.py` (A)
   - `src/super_harness/core/review_bundle.py` (A)
   - `src/super_harness/core/__init__.py` (A)
   - `src/super_harness/cli/review.py` (A)
   - `src/super_harness/core/workspace.py` (B, new)
   - `src/super_harness/sensors/__init__.py` (B)
   - `src/super_harness/adapters/__init__.py` (B)
   - `.importlinter`
   - `docs/decisions/d-core-is-base.md`
   - `tests/unit/adapters/test_registry.py` (A)
   - `tests/unit/core/test_review_bundle.py` (A)
   - `tests/unit/cli/test_review_prepare.py` (A)
   - `tests/unit/core/test_workspace.py` (B, new)
   - `docs/plans/2026-06-29-core-adapters-sensors-decoupling.md`
   - (+ any file `sync`/`doc check` modified in step 3)
   - **NOT** `private/OPEN-ITEMS.md` — gitignored (`.gitignore:2`), local-only, never in scope.
5. Independent plan-review subagent → `review approve --reviewer plan-reviewer`.
6. `super-harness implementation start ...` → execute Tasks 1–5 (subagent-driven).
7. All green (`pytest -m "not e2e"`, `ruff`, `mypy`, `lint-imports`, `decision check`) → `super-harness done 2026-06-29-core-adapters-decoupling`.
8. **Commit all anchors/config before dispatching the code reviewer** (an adversarial `git checkout` in review can revert uncommitted edits — #55 lesson). Then `review prepare --reviewer code-reviewer --base main` → independent reviewer subagent produces verdict YAML (full checklist + matching `bundle_digest`).
9. `review approve --verdict-file <f>` → `attest write` + commit attestation → `attest verify --base main --head HEAD`.
10. `git push` → `gh pr create` (token lacks `read:org`; write title/body correctly on first try) → CI all green (test, lint, decision-check, doc-check, merge-gate) → `gh pr merge --squash --delete-branch`.
11. `git checkout main && git pull` → `super-harness on-merge --commit <sha> --change 2026-06-29-core-adapters-decoupling` → verify landed.

**Re-prepare reminder:** any edit after `review prepare` requires a fresh `review prepare` (the verdict's `bundle_digest` must match the committed tree).

---

## Self-Review

**Spec coverage:** Cut A's two edges + the contract arming + the text-lock consequence are all covered — Task 0 (sound red-proof on pristine code), Task 1 (move resolver to adapters), Task 2 (drop core→adapters via injection), Task 3 (wire caller), Task 4 (arm sensors + name + anchor prose + re-ratify), Task 5 (full verify + OPEN-ITEMS). Cut B is explicitly out of scope (separate PR, noted in Task 5 Step 3 and OPEN-ITEMS).

**Placeholder scan:** No TBD/TODO. The one deliberate "read the file first" instruction (Task 3 Step 1 seed-helper name) is a real constraint, not a placeholder — the exact code to add is shown; only the existing helper's name must be matched to avoid duplicating a fixture.

**Type consistency:** `resolve_spec_plan_paths(framework: str | None, root: Path, change_id: str) -> tuple[str, str]` (Task 1) matches the `SpecPlanResolver = Callable[[str | None, Path, str], tuple[str, str]]` alias and `spec_plan_resolver` param (Task 2) and the value passed in Task 3. Bundle keys `spec_path` / `plan_path` match the existing `assemble_bundle` return dict.

**Codex review (2026-06-29, APPROVE-WITH-CHANGES) — all 4 must-fix applied:**
- *Red-proof soundness:* the old stash-based proof was unsound (Tasks 1-3 commit the fix, so `git stash` can't restore the old coupling). Moved to **Task 0**, run on pristine code with `git checkout -- .importlinter` to revert; Task 4 relies on the bite-test for teeth.
- *Gitignored private/:* `private/OPEN-ITEMS.md` is under `.gitignore:2` → made Task 5 Step 3 **local-only (no commit)** and removed it from merge-gate scope.
- *Self-matching grep:* Task 2 Step 5 now greps `import`-statement patterns only (the new comments mention `adapters` in prose), with `lint-imports` as the authoritative check.
- *Metadata drift:* Task 4 now also updates the `.importlinter` contract **`name`** (line 14) and the **`core/__init__.py`** anchor docstring (both still said cli/gates); `core/__init__.py` added to scope.

**Ambiguity check:** The default-resolver behavior is pinned explicitly (`_no_spec_plan` → `("", "")`), and the existing `framework="plain"` tests are called out as the reason no behavior changes for plain changes. The Task 0 red-proof reverts cleanly (no committed/CI-red intermediate state).
