---
change: 2026-08-06-plan-scope-fail-open
stage: plan
scope:
  files:
    - docs/plans/2026-08-06-plan-scope-fail-open.md
    - docs/cli-reference.md
    - private/OPEN-ITEMS.md
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

**Architecture:** One normalization at the boundary that already exists for this purpose, so scope entries can never name a file that does not exist, plus one fail-closed guard so that failure is loud instead of empty. A second, independent path to the same vacuous approval — a first-ever review of a plan inherited from the base branch — is documented below and deliberately left to its own change.

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

**Fail closed on paths that do not exist, not on ranges that are empty.** The obvious discriminator — "the range has changes but the scope matched none of them" — is wrong. `review_contract.py:328-335` runs the inspection against an *incremental* baseline whenever `resolve_source_baseline` returns an ancestor, so a legitimate re-review round where the plan is unchanged since that reviewer last looked (findings dispositioned `wontfix`, or addressed in the design document or the code instead) has a non-empty range and zero matching files. Guarding on that would hard-fail `review prepare` with no way forward except editing the plan to make the error go away.

The precise discriminator is **existence, not change**: this defect is entirely about scope entries that can never name a real file. So the primary guard is *"the assignment scope is non-empty and not one of its entries matches any file tracked at `target_head`"*. An absolute path fails it always; an unchanged plan passes it always; and it does not care which baseline the range was computed from.

## The second path to the same defect is split out, not solved here

Rounds 4 and 5 of this plan's own review established that the absolute path is not the only way to reach a vacuous approval. `mode == "full-change"` means `resolve_source_baseline` found no completed prior review by that source (`review_contract.py:108, :311-333`) — the reviewer **has never seen this plan**. A plan authored on the base branch and untouched on the feature branch therefore has an empty diff, and a contract-compliant reviewer approves a document it was never shown. `tests/unit/cli/test_review_prepare.py:269` pins that outcome as `EXIT_OK` today.

That is a real defect and **this change does not fix it.** Fixing it means changing what a review targets, and rounds 2–5 oscillated on it precisely because it is not settled:

- raising on it (round 2) wedges a legitimate workflow;
- dropping the question (round 3) leaves the hole open;
- retargeting to the artifact's content (round 4) has no defined representation — `scope_diff_argv` emits `["git","diff",range,"--",*files]` and an *empty* argv is today's empty-target signal, so a content target needs a different argv shape plus decisions on `inspection["base"]` and `["files"]`, and that argv is frozen into `contract_digest`;
- and its blast radius is not narrow: `full-change`'s base is the merge-base, so the target representation would change for effectively **every** first-round plan review, not just the inherited-plan case.

Those are design questions with a digest-bearing contract, raised on round 4 of a change that was already in review. Folding them in is the exact failure this whole line of work exists to prevent: one document carrying a settled decision and an unsettled one, re-litigated every round. They belong to their own change, with rounds 4 and 5's findings as its design input.

**What stays here** is the part no round has contested since round 1: adapter paths that can never name a real file, and a guard that makes that loud instead of empty. The split-out defect is registered in `private/OPEN-ITEMS.md` by Task 4, as DOABLE-NOW with rounds 4 and 5's findings attached — it must not survive only as a paragraph in a merged plan.

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

**Replace, do not add.** `tests/unit/adapters/test_registry.py:265-270`'s existing `test_resolve_spec_plan_paths_openspec` asserts `str(tmp_path / "openspec" / ... / "proposal.md")` — it pins the exact absolute contract being removed and will go red at Step 4. Rewrite that test to the repo-relative expectation rather than leaving two contradicting tests for an executor to adjudicate.

**Step 2: Run to verify they fail**

Run: `pytest tests/unit/adapters/test_registry.py -k spec_plan -v`
Expected: FAIL — absolute paths returned.

**Step 3: Implement.** Normalize each reported path against `root`; keep it relative, drop what falls outside. Pure, never raises.

**Step 4: Run and commit**

```bash
git commit -am "fix(review): resolve framework artifact paths repo-relative"
```

---

### Task 2: A scope that names no file at all fails closed

**Files:**
- Modify: `src/super_harness/engineering/review_contract.py` (around the `inspection` construction, `:330-345`)
- Test: `tests/unit/engineering/test_review_contract.py`

**Step 1: Write the failing tests**

- `test_compile_fails_when_scope_names_no_tracked_file`: the assignment scope is non-empty and no entry matches any file tracked at `target_head` → compilation raises rather than emitting an empty `diff_argv`. This is the absolute-path case, and it is the only case that raises.

  **The guard is set-scoped, not per-entry.** It fires only when **not one** entry matches any file tracked at `target_head`. A per-entry existence check would be a new wedge: declaring a file the change has not created yet is ordinary, and it must not hard-fail plan review.

  Two words, never interchangeable: **absent** (no entry names any tracked file at `target_head`) is the raising condition; **unchanged** (entries exist at `target_head` but fall outside the diff range) always compiles. A guard keyed on an empty diff rather than on absence is the round-2 mistake this plan already reversed once.
- `test_compile_allows_unchanged_scope_in_incremental_range`: the scope entries exist at `target_head` but nothing in them changed since the incremental baseline, while other files did → **must still compile** with an empty target. This is a legitimate re-review round; failing it would wedge the reject loop.
- `test_compile_allows_unchanged_scope_in_full_change_mode`: the scope entries exist at `target_head` and nothing in them changed in a non-empty range → **also compiles**, with an empty target. This is the inherited-plan case split out above; `tests/unit/cli/test_review_prepare.py:269` pins it today and must stay green, because this change deliberately does not touch it.
- `test_compile_allows_empty_target_when_range_is_empty`: `base..head` has no changes at all → the existing empty-target path still works.
- `test_compile_allows_empty_assignment_scope`: a role with no declared artifacts is unchanged.

Every case except the first compiles. The guard keys on **existence**, never on emptiness, so it cannot fire on any legitimate round.

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

**Step 1: Write the failing test** — `test_superpowers_change_gets_a_real_inspection_target`: a workspace with the superpowers layout, assembled and compiled, produces a `diff_argv` that names the plan document. Assert on the argv, not on an intermediate.

**The plan document must be committed on the feature branch.** `_repo_with_change` (`tests/unit/core/test_review_bundle.py:51-64`) already ends on `feat`, so following the neighbouring fixtures gives the right shape — but state it, because a plan committed on the base instead falls outside the range and yields `diff_argv == []` for a legitimate reason (the split-out defect, not this one), and the test would then pass while proving nothing.

**Step 2–4: Run, implement (should already pass after Tasks 1–2), run.**

**Step 5: Commit**

```bash
git commit -am "test: pin a real inspection target for superpowers-framework reviews"
```

---

### Task 4: Stop advertising `--framework` as a no-op

`cli/change.py:77` describes the flag as a `v0.1: no-op placeholder`, mirrored in `docs/cli-reference.md:236`. It is not a no-op: it selects the artifact resolver, and choosing `superpowers` was the single input that turned plan review into a vacuous pass. That help text is what invited the mistake.

**Files:**
- Modify: `src/super_harness/cli/change.py:76-77` (help text) and the module docstring at `:19-22`
- Modify: `private/OPEN-ITEMS.md`
- Regenerate: `docs/cli-reference.md` — **never edit by hand**; its first line is `<!-- AUTOGENERATED by scripts/gen_cli_reference.py -->` and it is produced from the click help this task is changing

**Step 1:** Say what the flag does — the recorded framework selects which adapter resolves the change's spec and plan artifacts for review scoping.

**Step 2: Do not claim a convention this change contradicts.** The docstring at `:19-22` currently justifies the no-op wording as project-wide, citing `init --framework` as a fellow member. After this change that claim is false for `change start` and unverified for `init`, so the docstring must stop appealing to a convention and simply describe `change start`'s own behaviour.

**Step 3:** Regenerate the reference.

Run: `super-harness doc check --fix`
Then verify: `super-harness doc check`

**Step 4: Register both deferred items in `private/OPEN-ITEMS.md`, as DOABLE-NOW.**

This is a step, not an afterthought: the entries are the only thing keeping either item alive once this plan is merged, and a note placed after the commit step is a note that never gets written.

1. **A first-ever review targets a diff, so a plan inherited from the base branch is reviewed as nothing.** Carry rounds 4 and 5's findings as its design input: the undefined representation for a content target, its effect on `contract_digest`, and the fact that `full-change`'s merge-base means the change would touch effectively every first-round plan review.
2. **`cli/init.py:525` makes the same "`--framework` is a no-op" claim** for install time, and `docs/cli-reference.md` will still carry that wording on the `init --framework` row after regeneration. Whether it is also false is a separate question about a different command; answering it here would widen a Micro change that is closing a live fail-open.

**Step 5:** Full suite and lint.

Run: `pytest tests/ -q && ruff check src tests && super-harness verify`

**Step 6: Commit**

```bash
git commit -am "docs: --framework selects the artifact resolver, not a no-op"
```

---

## Out of scope

`2026-08-06-plan-review-round-budget` (Cut 1 of the plan-review convergence work) is parked in `PLAN_REJECTED` until this lands, because its own plan reviews are not trustworthy while this defect stands. Its round-2 finding `PRB-R2-03` — the design document's frontmatter naming a change id that does not exist, hiding it from the plan-drift guard — belongs to that change and is fixed there.
