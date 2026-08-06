---
change: 2026-08-06-plan-scope-fail-open
stage: plan
scope:
  files:
    - docs/plans/2026-08-06-plan-scope-fail-open.md
    - docs/cli-reference.md
    - src/super_harness/adapters/registry.py
    - src/super_harness/cli/change.py
    - src/super_harness/engineering/review_contract.py
    - tests/unit/adapters/test_registry.py
    - tests/unit/core/test_review_bundle.py
    - tests/unit/engineering/test_review_contract.py
tier_hint: Micro
---

# The plan gate approves without the reviewer seeing anything

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close a fail-open in the review contract compiler: under two of the three shipped framework adapters, plan review hands the reviewer an empty inspection target, so a contract-compliant reviewer approves a plan nobody read.

**Architecture:** One normalization at the boundary that already exists for this purpose, plus one fail-closed guard so the same class of defect can never again degrade silently into "nothing to review".

**Tech Stack:** Python 3.10+, pytest. No new dependencies.

## How it was found

Observed live on `2026-08-06-plan-review-round-budget`, which was declared with `--framework superpowers`. Both of its plan-review rounds froze `Inspection argv: []` and a prompt reading `The assigned target is empty; do not construct a broader diff.` Round 1 produced seven genuine findings **only because that reviewer went off-contract and read the plan anyway**; round 2's reviewer obeyed the contract, noticed the empty target, and reported the defect. The second behaviour is the correct one, and it is the one that ends in a vacuous approval.

## The chain

```
adapters/framework/superpowers.py:236   result["plan"] = str(plan_path)   # absolute
adapters/framework/openspec.py:243      {"spec": str(base / "proposal.md"), ...}   # absolute
        ↓
adapters/registry.py:95-96      resolve_spec_plan_paths returns them verbatim
        ↓
core/review_bundle.py:135-136   plan_path = plan_path or declared_plan
                                the adapter value is non-empty, so it wins and the
                                correct repo-relative `declared_plan` is never used
        ↓
core/scope_match.py:48-60       covered_by_scope matches exact equality or `entry + "/"`;
                                an absolute entry can never match `git diff --name-only`
        ↓
files = []  →  scope_diff_argv → []  →  empty target, all-`na` verdict, plan approved
```

`plain` is unaffected **by accident**: it does not implement `spec_paths` at all, so the resolver returns `("", "")` and the repo-relative fallback runs. Every one of this repo's eleven prior changes used `plain`, which is why their frozen prompts all carry real `git diff` argv and this went unnoticed.

## Two decisions worth stating

**Normalize at the resolver, not in the adapters.** `sensors/verification_runner.py:287` also calls `spec_paths`, to substitute `${SPEC_PATH}` / `${PLAN_PATH}` into verification commands, where an absolute path is the useful form. The adapter contract stays "workspace-rooted absolute"; `resolve_spec_plan_paths` is the boundary that feeds a scope matcher, so it is the boundary that must produce repo-relative paths. One choke point, and no future adapter can reintroduce the bug.

**Fail closed, not empty.** An empty inspection target is legitimate when the change genuinely has no matching content, so the guard must distinguish that from "the scope matched nothing even though the range has changes". The latter is a compiler error, not a review.

---

### Task 1: The resolver returns repo-relative paths

**Files:**
- Modify: `src/super_harness/adapters/registry.py:77-96` (`resolve_spec_plan_paths`)
- Test: `tests/unit/adapters/test_registry.py`

**Step 1: Write the failing tests**

- `test_resolve_spec_plan_paths_are_repo_relative`: with the superpowers adapter and a plan at `<root>/docs/plans/x-implementation.md`, the resolver returns `docs/plans/x-implementation.md`, not the absolute path.
- `test_resolve_spec_plan_paths_openspec_is_repo_relative`: the same for `openspec/changes/<id>/tasks.md`.
- `test_resolve_spec_plan_paths_drops_paths_outside_root`: an adapter reporting a path outside the workspace yields `""` for that slot rather than a path no scope entry can ever match. Silently dropping is correct here — the alternative is a crash in a pure path-derivation helper that several callers assume never raises.
- `test_resolve_spec_plan_paths_passes_through_relative`: an adapter already returning relative paths is unchanged (idempotent).

**Step 2: Run to verify they fail**

Run: `pytest tests/unit/adapters/test_registry.py -k spec_plan -v`
Expected: FAIL — absolute paths returned.

**Step 3: Implement.** Normalize each reported path against `root`; keep it relative, drop what falls outside. Pure, never raises.

**Step 4: Run and commit**

```bash
git commit -am "fix(review): resolve framework artifact paths repo-relative"
```

---

### Task 2: An unmatched assignment scope fails closed

**Files:**
- Modify: `src/super_harness/engineering/review_contract.py` (around the `inspection` construction, `:330-345`)
- Test: `tests/unit/engineering/test_review_contract.py`

**Step 1: Write the failing tests**

- `test_compile_fails_when_scope_matches_nothing_in_a_nonempty_range`: `base..head` contains changes, the assignment scope is non-empty, and no changed file matches it → compilation raises rather than emitting an empty `diff_argv`.
- `test_compile_allows_empty_target_when_range_is_empty`: `base..head` has no changes at all → the existing empty-target path still works. This is the case the current message was written for and it must survive.
- `test_compile_allows_empty_assignment_scope`: a role with no declared artifacts is unchanged.

**Step 2: Run to verify they fail.**

**Step 3: Implement.** The error names the assignment scope entries and the changed files that failed to match — the message is the whole point, since the failure mode being closed is one that produced *no* signal at all.

**Step 4: Run and commit**

```bash
git commit -am "fix(review): fail closed when the assignment scope matches nothing"
```

---

### Task 3: End-to-end regression

Tasks 1 and 2 are each necessary and neither is sufficient: without Task 1 the compiler would now *raise* on every superpowers plan review instead of silently passing, which is safer but still broken.

**Files:**
- Test: `tests/unit/core/test_review_bundle.py`

**Step 1: Write the failing test** — `test_superpowers_change_gets_a_real_inspection_target`: a workspace with the superpowers layout and a committed plan document, assembled and compiled, produces a `diff_argv` that names the plan document. Assert on the argv, not on an intermediate.

**Step 2–4: Run, implement (should already pass after Tasks 1–2), run.**

**Step 5: Commit**

```bash
git commit -am "test: pin a real inspection target for superpowers-framework reviews"
```

---

### Task 4: Stop advertising `--framework` as a no-op

`cli/change.py:77` describes the flag as a `v0.1: no-op placeholder`, mirrored in `docs/cli-reference.md:236`. It is not a no-op: it selects the artifact resolver, and choosing `superpowers` was the single input that turned plan review into a vacuous pass. That help text is what invited the mistake.

**Files:**
- Modify: `src/super_harness/cli/change.py:76-77` and the module docstring at `:19`
- Modify: `docs/cli-reference.md:236`

**Step 1:** Say what it does — the recorded framework selects which adapter resolves the change's spec and plan artifacts for review scoping.

**Step 2:** Verify documentation consistency.

Run: `super-harness doc check`

**Step 3:** Full suite and lint.

Run: `pytest tests/ -q && ruff check src tests && super-harness verify`

**Step 4: Commit**

```bash
git commit -am "docs: --framework selects the artifact resolver, not a no-op"
```

`cli/init.py:525` carries a similar claim about `--framework` at install time. It is **out of scope**: whether that one is also false is a separate question about a different command, and answering it here would widen a Micro change on a live fail-open.

---

## Out of scope

`2026-08-06-plan-review-round-budget` (Cut 1 of the plan-review convergence work) is parked in `PLAN_REJECTED` until this lands, because its own plan reviews are not trustworthy while this defect stands. Its round-2 finding `PRB-R2-03` — the design document's frontmatter naming a change id that does not exist, hiding it from the plan-drift guard — belongs to that change and is fixed there.
