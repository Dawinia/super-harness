---
change: multi-independent-reviewer-gate
stage: plan
tier_hint: Normal
scope:
  files:
    - .harness/policy.yaml
    - AGENTS.md
    - docs/cli-reference.md
    - docs/decisions/d-events-append-only.md
    - docs/decisions/d-fixed-transition-matrix.md
    - docs/state-machine.md
    - docs/plans/2026-07-09-multi-independent-reviewer-gate-plan.md
    - src/super_harness/adapters/agent/claude_code.py
    - src/super_harness/adapters/agent/codex.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/review.py
    - src/super_harness/cli/status.py
    - src/super_harness/core/events.py
    - src/super_harness/core/transitions.py
    - src/super_harness/engineering/reviewer_policy.py
    - scripts/gen_cli_reference.py
    - scripts/gen_state_machine.py
    - tests/integration/cli/test_init.py
    - tests/integration/cli/test_status.py
    - tests/unit/cli/test_review.py
    - tests/unit/cli/test_review_verdict_gate.py
    - tests/unit/engineering/test_reviewer_policy.py
    - tests/unit/scripts/test_gen_state_machine.py
---

# Multi-Independent Reviewer Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make review approval require N independent configured reviewer sources before the CLI emits the existing lifecycle milestone events.

**Architecture:** Keep `plan_approved` and `code_review_passed` as the downstream lifecycle milestones. Add a review-state self-loop `review_verdict_recorded` event for each approving source; once the current review attempt has enough distinct sources for the reviewer role, emit the existing milestone event with a summary payload. Reviewer roles (`plan-reviewer`, `code-reviewer`) remain separate from reviewer sources (`subagent`, `external`, `human`, or user-defined names); super-harness stores and validates source declarations but never spawns or executes reviewers.

**Tech Stack:** Python 3, Click CLI, PyYAML policy parsing, existing event-sourced lifecycle reducer, pytest.

---

## File Structure

- `src/super_harness/engineering/reviewer_policy.py` owns policy parsing for both the existing reviewer strategy and the new independence/source requirements.
- `src/super_harness/cli/review.py` owns CLI enforcement: `--source`, recording per-source verdict events, checking distinct accepted sources, and emitting the final lifecycle milestone.
- `src/super_harness/cli/status.py` owns read-only progress display for missing independent review verdicts.
- `src/super_harness/core/events.py` and `src/super_harness/core/transitions.py` register the new state-preserving event.
- `src/super_harness/cli/init.py` owns the default policy skeleton. Default `min_independent` stays `1` for backwards compatibility.
- `src/super_harness/adapters/agent/claude_code.py`, `src/super_harness/adapters/agent/codex.py`, `AGENTS.md`, generated docs, and tests document the new protocol.
- `.harness/policy.yaml` dogfoods this change with `min_independent: 2` and vendor-neutral sources (`subagent`, `external`), where `external` is locally instructed to run `codex exec --sandbox read-only`.
- `docs/decisions/d-events-append-only.md` and `docs/decisions/d-fixed-transition-matrix.md` are reconciled because this slice intentionally extends the event type list and declared transition matrix.
- `scripts/gen_state_machine.py` and `tests/unit/scripts/test_gen_state_machine.py` document state-specific self-loops separately from global informational no-op events.

## Task 1: Parse Reviewer Independence Policy

**Files:**
- Modify: `src/super_harness/engineering/reviewer_policy.py`
- Modify: `tests/unit/engineering/test_reviewer_policy.py`

- [ ] **Step 1: Write failing policy tests**

Add tests for default independence, configured `min_independent`, source allowlist, optional source instructions, malformed policy, and the `sources: [subagent, external]` shorthand.

Run: `python -m pytest tests/unit/engineering/test_reviewer_policy.py -v`

Expected: FAIL because the new API does not exist.

- [ ] **Step 2: Implement the policy dataclasses and parser**

Add a `ReviewerIndependencePolicy` dataclass with fields:

```python
reviewer: str
strategy: str
min_independent: int
allowed_sources: tuple[str, ...]
source_instructions: dict[str, str]
```

Add `load_reviewer_policy(root, reviewer)` while keeping `load_reviewer_strategy()` as a compatibility wrapper. Policy rules:

- Missing config defaults to `strategy="subagent"`, `min_independent=1`, no required source allowlist.
- `reviewers.<role>.min_independent` must be an integer >= 1.
- `reviewers.sources` accepts either a list of strings or a mapping from source name to `{instructions: <string>}`.
- Built-in instructions exist for `subagent`, `external`, and `human`.
- Source labels must be distinct; duplicate list entries are rejected before `min_independent` is evaluated.
- Mapping-form `reviewers.sources` duplicate keys are rejected during YAML load, before PyYAML can silently collapse them.
- If `min_independent >= 2`, at least `min_independent` allowed sources must be configured.
- A source name is vendor/tool-neutral by default; `claude-subagent` and `codex` are not built-in defaults.

Run: `python -m pytest tests/unit/engineering/test_reviewer_policy.py -v`

Expected: PASS.

## Task 2: Register `review_verdict_recorded`

**Files:**
- Modify: `src/super_harness/core/events.py`
- Modify: `src/super_harness/core/transitions.py`
- Modify: `docs/state-machine.md` (regenerate later)
- Test: `tests/unit/cli/test_review.py`

- [ ] **Step 1: Write failing transition coverage**

Add review CLI tests showing:

- the first approving source records `review_verdict_recorded`, leaves the change in `AWAITING_PLAN_REVIEW`, and does not emit `plan_approved`.
- `review approve --reviewer plan-reviewer --source subagent` from `PLAN_APPROVED` is rejected before appending anything.
- `review approve --reviewer code-reviewer --source subagent` from `PLAN_APPROVED` is rejected before appending anything.
- `review approve --reviewer plan-reviewer --source subagent` from `AWAITING_CODE_REVIEW` is rejected before appending anything.
- `review approve --reviewer code-reviewer --source subagent` from `AWAITING_PLAN_REVIEW` is rejected before appending anything.

Run: `python -m pytest tests/unit/cli/test_review.py -k independent -v`

Expected: FAIL because the event is unknown/unsupported and `--source` is unsupported.

- [ ] **Step 2: Add the event and explicit review-state self-loop semantics**

Add `review_verdict_recorded` to `EXTENSION_EVENT_TYPES`. Do not add it to `_INFORMATIONAL`; instead add explicit self-loop transitions for `AWAITING_PLAN_REVIEW`, `AWAITING_CODE_REVIEW`, and `CODE_REVIEW_REJECTED`. The CLI must still enforce the reviewer-specific state before writing:

- `plan-reviewer`: current state must be `AWAITING_PLAN_REVIEW`.
- `code-reviewer`: current state must be `AWAITING_CODE_REVIEW` or `CODE_REVIEW_REJECTED`.

Run: `python -m pytest tests/unit/cli/test_review.py -k independent -v`

Expected: Still FAIL until review CLI enforcement is implemented.

## Task 3: Enforce Cumulative Independent Approvals

**Files:**
- Modify: `src/super_harness/cli/review.py`
- Modify: `tests/unit/cli/test_review.py`
- Modify: `tests/unit/cli/test_review_verdict_gate.py`

- [ ] **Step 1: Add failing CLI behavior tests**

Cover these cases:

- `min_independent: 2` plus `--source subagent` records `review_verdict_recorded` only.
- A second approval from `--source external` emits both another `review_verdict_recorded` and the milestone (`plan_approved` or `code_review_passed`).
- Duplicate source does not satisfy independence.
- Unknown source is rejected when policy declares sources.
- `min_independent: 1` preserves old behavior and does not require `--source`; when `--source` is omitted, the CLI emits only the existing milestone event and does not create a partial source-verdict event with an implicit/null source.
- `min_independent: 2` rejects missing `--source`.
- Stale partial approvals from an earlier plan attempt do not count after a later `plan_rejected` + `plan_ready`.
- Stale partial approvals from an earlier code-review attempt do not count after a later `code_review_failed`.
- Stale code-review partial approvals whose verdict digest no longer matches the current committed in-scope diff do not count toward the final threshold.
- `review reject` remains immediate (`plan_rejected` / `code_review_failed`), with optional `--source` recorded when present.
- `review skip` remains immediate; this slice does not make skip cumulative.

Run: `python -m pytest tests/unit/cli/test_review.py -k independent -v`

Expected: FAIL for missing CLI support.

- [ ] **Step 2: Implement `--source` and cumulative approve logic**

Add a shared `--source` option to approve/reject/skip. For `approve`:

- Validate source requirements before writing anything.
- Validate structured verdicts exactly as today before writing the source event.
- If `min_independent == 1` and `--source` is omitted, preserve the existing approval path: emit only the existing milestone event and do not create `review_verdict_recorded`.
- Emit `review_verdict_recorded` with payload `{reviewer, source, reason, outcome: "approved", verdict?}`.
- Count distinct source values from prior `review_verdict_recorded` approvals for the same change, reviewer, and current review attempt, plus the current source.
- If distinct count is below `min_independent`, refresh state and exit 0 with a "pending independent verdicts" message.
- If the threshold is met, emit the existing milestone event (`plan_approved` / `code_review_passed`) with payload containing reviewer, reason, source, `independent_sources`, `min_independent`, and the final verdict when present.

The current review attempt window is append-order based:

- `plan-reviewer`: count only partial approvals after the latest `plan_ready`.
- `code-reviewer`: count only partial approvals after the latest `implementation_complete` or `code_review_failed`, whichever is later.

For `code-reviewer`, the distinct-source count additionally filters prior partial approvals to verdict payloads whose `bundle_digest` matches the current approving verdict. This preserves the existing structured-review freshness guarantee across cumulative partial approvals.

Status reporting uses the same digest-aware count for code-review progress when a current prepared bundle is available, so UI/CLI progress does not show stale code-review partial approvals as accepted after committed in-scope changes.

For `reject`, preserve current immediate fail behavior and include `source` when supplied.

For `skip`, preserve current immediate pass behavior and include `source` when supplied.

Run: `python -m pytest tests/unit/cli/test_review.py -k independent -v`

Expected: PASS.

## Task 4: Surface Progress in Status

**Files:**
- Modify: `src/super_harness/cli/status.py`
- Modify: `tests/integration/cli/test_status.py`

- [ ] **Step 1: Write failing status tests**

Add tests that place a change in a review state with policy `min_independent: 2`, one source already recorded, and assert that human and JSON status report:

- reviewer role
- strategy
- `min_independent`
- accepted sources so far
- missing count
- remaining configured sources
- advisory instructions for remaining sources when available

Run: `python -m pytest tests/integration/cli/test_status.py -k independent -v`

Expected: FAIL because status does not render these fields.

- [ ] **Step 2: Implement review progress rendering**

Use the policy parser and events stream to compute review progress. For human output, add concise lines under `reviewer:`; for JSON, add a `review_progress` object.

Run: `python -m pytest tests/integration/cli/test_status.py -k independent -v`

Expected: PASS.

## Task 5: Update Defaults and Docs Surfaces

**Files:**
- Modify: `src/super_harness/cli/init.py`
- Modify: `tests/integration/cli/test_init.py`
- Modify: `scripts/gen_cli_reference.py`
- Modify: `docs/cli-reference.md` (regenerate)
- Modify: `docs/state-machine.md` (regenerate)
- Modify: `src/super_harness/adapters/agent/claude_code.py`
- Modify: `src/super_harness/adapters/agent/codex.py`
- Modify: `AGENTS.md`
- Modify: `.harness/policy.yaml`

- [ ] **Step 1: Write failing init/doc tests**

Update init tests to assert the default skeleton includes `min_independent: 1`, vendor-neutral sources, and no `claude-subagent` default source.

Run: `python -m pytest tests/integration/cli/test_init.py -v`

Expected: FAIL until the skeleton changes.

- [ ] **Step 2: Update skeleton and agent-facing protocol text**

Default skeleton:

```yaml
reviewers:
  sources:
    subagent: {}
    external: {}
    human: {}
  plan-reviewer:
    strategy: subagent
    min_independent: 1
  code-reviewer:
    strategy: subagent
    min_independent: 1
```

Update local `.harness/policy.yaml` to dogfood:

```yaml
reviewers:
  sources:
    subagent: {}
    external:
      instructions: "Run codex exec --sandbox read-only against the prepared plan or review bundle."
    human: {}
  plan-reviewer:
    strategy: subagent
    min_independent: 2
  code-reviewer:
    strategy: subagent
    min_independent: 2
```

Update agent docs to say source labels are configured reviewer sources and are not commands executed by super-harness.

Run: `python -m pytest tests/integration/cli/test_init.py -v`

Expected: PASS.

- [ ] **Step 3: Regenerate derived docs**

Run:

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check
```

Expected: generated `docs/cli-reference.md` and `docs/state-machine.md` are in sync and doc check passes.

## Task 6: Resolve Code Review Findings

**Files:**
- Modify: `src/super_harness/engineering/reviewer_policy.py`
- Modify: `src/super_harness/cli/review.py`
- Modify: `src/super_harness/cli/status.py`
- Modify: `scripts/gen_state_machine.py`
- Modify: `docs/state-machine.md` (regenerate)
- Modify: `tests/unit/engineering/test_reviewer_policy.py`
- Modify: `tests/unit/cli/test_review_verdict_gate.py`
- Modify: `tests/integration/cli/test_status.py`
- Modify: `tests/unit/scripts/test_gen_state_machine.py`

- [ ] **Step 1: Add failing regression tests for reviewer findings**

Add tests that prove:

- `reviewers.sources: [subagent, subagent]` is rejected as a duplicate source declaration.
- A stale code-review partial approval with an older verdict `bundle_digest` does not count toward a later current-digest approval after committed in-scope changes.
- The generated state-machine doc includes state-specific self-loop rows for `review_verdict_recorded`, while global informational no-op events remain in the separate no-op section.

Run:

```bash
python -m pytest tests/unit/engineering/test_reviewer_policy.py tests/unit/cli/test_review_verdict_gate.py tests/unit/scripts/test_gen_state_machine.py -v
```

Expected: FAIL before the fix.

- [ ] **Step 2: Implement reviewer-finding fixes**

Implement duplicate source validation, code-review digest filtering for cumulative partial approvals, and state-machine generator support for state-specific self-loop rows. Regenerate `docs/state-machine.md` with `super-harness doc check --fix`.

Run:

```bash
python -m pytest tests/unit/engineering/test_reviewer_policy.py tests/unit/cli/test_review_verdict_gate.py tests/unit/scripts/test_gen_state_machine.py -v
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check
```

Expected: PASS.

- [ ] **Step 3: Resolve second-round code review findings**

Add tests that prove:

- Mapping-form duplicate source labels in `reviewers.sources` are rejected before PyYAML can collapse duplicate mapping keys.
- Code-review status progress uses the same current-bundle digest filter as the approval gate and does not report stale partial approvals as accepted.

Implement:

- A policy YAML loader that detects duplicate mapping keys and raises `ReviewerPolicyError`.
- Digest-aware code-review status progress when `.harness/pending-reviews/<change>/code-reviewer.bundle.json` exists.

Run:

```bash
python -m pytest tests/unit/engineering/test_reviewer_policy.py tests/integration/cli/test_status.py -v
```

Expected: FAIL before the fix; PASS after the fix.

## Task 7: Full Verification and Self-Host Lifecycle

**Files:**
- All scoped files above.

- [ ] **Step 1: Run focused tests**

```bash
python -m pytest tests/unit/engineering/test_reviewer_policy.py tests/unit/cli/test_review.py tests/unit/cli/test_review_verdict_gate.py tests/integration/cli/test_status.py tests/integration/cli/test_init.py tests/unit/scripts/test_gen_state_machine.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest
```

Expected: PASS.

- [ ] **Step 3: Run harness checks**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check --changed
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision reconcile d-events-append-only --kind independent --justification "Multi-independent reviewer gate adds a new event type only; events remain append-only and state remains derived from the log."
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision reconcile d-fixed-transition-matrix --kind independent --justification "Multi-independent reviewer gate adds explicit review-state self-loops to the declared transition matrix; compute_target_state remains the sole transition authority."
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check --changed --gate-reconcile
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check
PATH="$(pwd)/.venv/bin:$PATH" super-harness verify --check pytest
```

Expected: PASS.

- [ ] **Step 4: Complete self-host lifecycle**

Run two independent plan reviews before implementation approval, then after implementation run two independent code reviews using configured sources:

- `subagent` source: independent subagent reviewer in this session.
- `external` source: `codex exec --sandbox read-only` reviewer in this repo.

Do not merge to `main` without explicit user confirmation.

## Self-Review

- Spec coverage: The plan covers policy declaration, cumulative N distinct reviewer-source verdicts within the current review attempt, default `1` compatibility, status reporting, docs, and self-host dogfooding. Automatic reviewer spawning is explicitly out of scope.
- Placeholder scan: No task relies on TODO/TBD placeholders; all behavior gates and commands are concrete.
- Type consistency: The reviewer role remains `reviewer`; the new execution-source axis is consistently named `source`.
