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

## Why this plan has no step-by-step choreography

Rounds 3–7 of this plan's own review cost $5.4 and produced no change to the design, which has stood untouched since round 1. Every finding in rounds 6 and 7 landed on execution choreography — which branch a fixture commits on, which numbered step an instruction sits in, whether a title reads per-entry or per-set — and each round's findings were caused by the previous round's edits to that same prose. Round 7's major was the reviewer rejecting a fact it had itself supplied one round earlier.

Those are questions a test run answers in two seconds and a prose reviewer can always find more of. So this plan states **decisions, boundaries, and the behaviours the tests must pin**, and stops there. How to sequence the edits is the executor's business, and whether the edits are right is the test suite's.

---

## Task 1 — `resolve_spec_plan_paths` returns repo-relative paths

`src/super_harness/adapters/registry.py:77-96` · tests in `tests/unit/adapters/test_registry.py`

Behaviours to pin:

- superpowers and openspec artifact paths come back repo-relative, not absolute
- a path outside the workspace yields `""` for that slot — dropped, not raised, because several callers assume this helper never raises
- an already-relative path is unchanged (idempotent)
- `tests/unit/adapters/test_registry.py:265-270` pins the old absolute contract; **rewrite it**, do not leave two contradicting tests

## Task 2 — a scope that names no file at all fails closed

`src/super_harness/engineering/review_contract.py` (the `inspection` construction, `:330-345`) · tests in `tests/unit/engineering/test_review_contract.py`

The guard is **set-scoped**: it raises only when **not one** assignment-scope entry matches any file tracked at `target_head`. A per-entry check would hard-fail plan review whenever a scope declares a file the change has not created yet.

**Absent** (no entry names a tracked file at `target_head`) raises. **Unchanged** (entries exist but fall outside the diff range) always compiles. Keying the guard on an empty diff rather than on absence is the round-2 mistake this plan already reversed once.

Behaviours to pin — one raises, the rest compile:

| case | outcome |
| --- | --- |
| no entry names any file tracked at `target_head` | **raises**, naming the entries and the changed files |
| entries exist, unchanged since the incremental baseline | compiles, empty target |
| entries exist, unchanged in a non-empty full-change range | compiles, empty target — the split-out defect, and `tests/unit/cli/test_review_prepare.py:269` must stay green |
| `base..head` empty | compiles, empty target |
| assignment scope empty | unchanged |

## Task 3 — end-to-end regression

`tests/unit/core/test_review_bundle.py`

A superpowers-layout workspace, assembled and compiled, produces a `diff_argv` naming the plan document. Assert on the argv, not an intermediate. Commit the plan **on the feature branch** (as `_repo_with_change:51-64` already does) — a plan on the base yields `diff_argv == []` for a legitimate reason, and the test would pass while proving nothing.

Tasks 1 and 2 are each necessary and neither sufficient: without Task 1 the compiler would raise on every superpowers plan review instead of silently passing — safer, still broken.

## Task 4 — `--framework` is not a no-op

`src/super_harness/cli/change.py:76-77` and its module docstring `:19-22` · `private/OPEN-ITEMS.md` · `docs/cli-reference.md` **regenerated only**, via `doc check --fix` (its first line says autogenerated)

The help text calls the flag a `v0.1: no-op placeholder`. It selects the artifact resolver, and choosing `superpowers` is the single input that turned plan review into a vacuous pass — that text is what invited the mistake. Say what it does, and stop appealing to a project-wide convention this change contradicts for one of its two members.

Register both deferred items in `private/OPEN-ITEMS.md` as DOABLE-NOW. The entries are the only thing keeping either alive once this plan merges:

1. **A first-ever review targets a diff**, so a plan inherited from the base branch is reviewed as nothing. Design input: rounds 4–5 established that a content target has no defined representation, that the argv is frozen into `contract_digest`, and that `full-change`'s merge-base means it would change the target for effectively every first-round plan review.
2. **`cli/init.py:525` makes the same no-op claim** for install time, and `docs/cli-reference.md` keeps that wording on the `init --framework` row after regeneration. A separate question about a different command.

## Done when

`pytest tests/ -q && ruff check src tests && super-harness doc check && super-harness verify` all pass, and a superpowers-framework plan review produces a non-empty inspection target.

---

## Out of scope

`2026-08-06-plan-review-round-budget` (Cut 1 of the plan-review convergence work) is parked in `PLAN_REJECTED` until this lands, because its own plan reviews are not trustworthy while this defect stands. Its round-2 finding `PRB-R2-03` — the design document's frontmatter naming a change id that does not exist, hiding it from the plan-drift guard — belongs to that change and is fixed there.
