---
change: 2026-08-06-plan-review-round-budget
stage: plan
scope:
  files:
    - docs/plans/2026-08-06-plan-review-convergence-design.md
    - docs/plans/2026-08-06-plan-review-round-budget-implementation.md
    - src/super_harness/adapters/reviewer/claude_cli.py
    - src/super_harness/cli/review.py
    - src/super_harness/core/events.py
    - src/super_harness/core/transitions.py
    - src/super_harness/engineering/review_budget.py
    - src/super_harness/engineering/review_governance.py
    - src/super_harness/engineering/review_runs.py
    - src/super_harness/engineering/value_report.py
    - docs/getting-started.md
    - tests/fixtures/review-corpus/corpus.jsonl
    - tests/fixtures/review-corpus/README.md
    - tests/unit/adapters/reviewer/test_claude_cli.py
    - tests/unit/cli/test_review_runs.py
    - tests/unit/engineering/test_review_budget.py
    - tests/unit/engineering/test_review_governance.py
    - tests/unit/engineering/test_value_report.py
tier_hint: Normal
---

# Cut 1 — The Brake: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Design:** `docs/plans/2026-08-06-plan-review-convergence-design.md` carries the evidence and rationale for every decision below. Read it first.

**Goal:** Make the review round budget actually bind on the plan path, and make the harness state its own cost, round history, and reviewer availability at the moment it blocks — not afterwards in a report.

**Architecture:** Three independent repairs plus one new derivation. (a) The budget stops counting epochs and counts rounds per `(change, reviewer)`, because `plan_ready` re-fires on every rejection and resets it. (b) A new pure module folds the event stream into the evidence a human needs to decide whether to fund another round, and the CLI prints it both at the block and at authorization. (c) Cost accounting learns about cached tokens, the claude adapter stops discarding `session_id`, and a round that lost a review source says so. Every behavioural claim is pinned by replaying a redacted corpus of 69 real plan-review rounds.

**Tech Stack:** Python 3.10+, click, pytest. No new dependencies.

**Contracts live in the code, not in this document.** Exact dataclass fields and function signatures are decided at implementation time and pinned by the tests named here. This plan states behaviour and test names; it deliberately does not hand-maintain type signatures in prose — that practice is the specific failure this whole change exists to prevent.

---

### Task 1: Redacted replay corpus

The acceptance evidence for Tasks 5–8. Exported once from `/Users/dawinialo/Work/vice/pantheon/.harness/events.jsonl`, which is private and cannot be a test dependency.

**Files:**
- Create: `tests/fixtures/review-corpus/corpus.jsonl`
- Create: `tests/fixtures/review-corpus/README.md`

**Step 1: Write the export script to the scratchpad** (not the repo — it runs once)

Keep per event: `event_id`, `type`, `change_id`, `timestamp`, `actor`, `framework`, and from `payload` only `reviewer`, `epoch_id`, `round_id`, `run_id`, `source`, `automatic`, `outcome`, `missing_sources`, `min_independent`, each finding's `severity`, and `receipt.usage`'s four token counts.

Drop entirely: finding `summary`, `file`, `id`; `contract_digest`, `bundle_digest`, `profile_digest`, `target_head`, `result_digest`; every path; `reason` free text.

Rewrite: `change_id` → `corpus-01` … `corpus-08` in first-appearance order; `actor.identifier` → `corpus@example.invalid`.

**Step 2: Verify the redaction mechanically**

Run: `grep -Eio 'pack|rebuttal|ranking|slug|docs/|[0-9a-f]{40}' tests/fixtures/review-corpus/corpus.jsonl | sort -u`
Expected: no output.

**Step 3: Verify the curves survived**

Write `tests/unit/engineering/test_review_budget.py::test_corpus_reproduces_known_curves` asserting the eight per-change `blocker+major` sequences from the design document's table, e.g. `corpus-06` (`stage5b`) is `[3,2,3,2,2,1,2,3,1,1]`.

Run: `pytest tests/unit/engineering/test_review_budget.py -v`
Expected: PASS.

**Step 4: Write the README** — what the corpus is, that it is redacted, which real changes it came from (by shape, not by name), and that it is a regression asset: any future change to review policy replays against it.

**Step 5: Commit**

```bash
git add tests/fixtures/review-corpus tests/unit/engineering/test_review_budget.py
git commit -m "test: add redacted plan-review replay corpus (69 rounds, 8 changes)"
```

---

### Task 2: Cost accounting counts cached tokens

**Files:**
- Modify: `src/super_harness/engineering/value_report.py:212` (`_usage_tokens`)
- Test: `tests/unit/engineering/test_value_report.py`

**Step 1: Write the failing test**

`test_usage_tokens_counts_cache_reads`: a usage dict shaped like a real claude receipt — `input_tokens: 40`, `output_tokens: 15610`, `cache_read_input_tokens: 1614779`, `cache_creation_input_tokens: 85246` — totals 1,715,675, not 15,650.

Also `test_usage_tokens_prefers_reported_total`: when `total_tokens` is present it still wins. And `test_usage_tokens_absent_usage_is_none`: `None` (not `0`) when no usage at all — the report distinguishes "not captured" from "zero".

**Step 2: Run to verify it fails**

Run: `pytest tests/unit/engineering/test_value_report.py -k usage_tokens -v`
Expected: FAIL, actual 15650.

**Step 3: Implement** — add the two cache fields to the fallback sum. Non-int values contribute zero; the function still never raises.

**Step 4: Also record the producer's own cost when it reports one.** `claude --print` returns `total_cost_usd` at the top level (observed: `1.849` for a single `stage5b` round) and we currently discard it. Carry it through the reviewer protocol result into the receipt, and surface the sum in `report` next to the token count. Absent for producers that do not report it — the field is optional everywhere, never fabricated.

**Step 5: Run and commit**

Run: `pytest tests/unit/engineering/test_value_report.py -v`

```bash
git commit -am "fix(report): count cached tokens and record producer-reported cost"
```

---

### Task 3: The claude adapter stops discarding `session_id`

`ReviewerProtocolResult.session_id` already exists (`adapters/reviewer/base.py:38`) and `codex_cli.py:125` populates it; `claude_cli.py` does not, so every claude receipt in the wild records `null`. Pure recording — no behaviour depends on it yet.

**Files:**
- Modify: `src/super_harness/adapters/reviewer/claude_cli.py:146-152`
- Test: `tests/unit/adapters/reviewer/test_claude_cli.py`

**Step 1: Write the failing test** — `test_parse_result_records_session_id`: raw output containing `"session_id": "6071902f-62ee-4f8b-9bf9-00a867d614d3"` yields that value on the result. Plus `test_parse_result_tolerates_missing_session_id` → `None`, and a non-string `session_id` → `None`.

**Step 2: Run to verify it fails.** Run: `pytest tests/unit/adapters/reviewer/test_claude_cli.py -k session -v`

**Step 3: Implement** — one guarded field read in `parse_result`.

**Step 4: Run and commit**

```bash
git commit -am "feat(reviewer): record claude session_id on the receipt"
```

---

### Task 4: A round that lost a review source says so

`cli/review.py:1463-1475` checks rejection **before** missing sources, so a round where one reviewer rejected and the other died closes as a clean `rejected` and the death is never mentioned. **Do not change that ordering** — a rejection is a rejection regardless of quorum. Only the output changes.

**Files:**
- Modify: `src/super_harness/cli/review.py` (the import-result output block near `1880`)
- Test: `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing test** — `test_round_close_reports_missing_source`: two required sources, one imported, one recorded as failed; the round closes `rejected` and stdout carries a warning line naming the missing source and how many consecutive rounds it has now missed. The JSON envelope gains the same as structured data.

**Step 2: Run to verify it fails.**

**Step 3: Implement** — derive the consecutive-miss count in Task 5's module (a source is "missing" for a round when it is in `required_sources` and has no `imported` run for that round). Print only when non-empty.

**Step 4: Run and commit**

```bash
git commit -am "feat(review): report a round's missing review sources at close"
```

---

### Task 5: The budget counts rounds per change, not per epoch

**Files:**
- Modify: `src/super_harness/engineering/review_runs.py` (add a per-change count; leave `automatic_rounds_used` and epoch folding alone — retry anchoring still needs them)
- Modify: `src/super_harness/engineering/review_governance.py:197-199` (key rename)
- Modify: `src/super_harness/cli/review.py:907-909, 1219-1221, 1111` (the two `needs_authorization` sites and the reported max)
- Test: `tests/unit/engineering/test_review_governance.py`, `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing governance tests**

- `test_old_rounds_key_is_rejected`: a config carrying `max_automatic_rounds_per_epoch` raises `ReviewGovernanceError` naming both the old and new key and stating that the semantics changed from per-epoch to per-change. **No deprecation period, no silent acceptance** — the same `2` would quietly mean something else.
- `test_plan_reviewer_rounds_default_is_six` and `test_code_reviewer_rounds_default_is_two`: per-role defaults differ, replayed from the corpus (six healthy changes never interrupted; code review's own history supports 2).

**Step 2: Write the failing counting test**

`test_automatic_rounds_survive_plan_resubmit`: an event stream with three `plan_ready` boundaries and one automatic round each. The per-change count is 3. (Today's `automatic_rounds_used` reads 1 — that is the bug.)

**Step 3: Run to verify both fail.**

Run: `pytest tests/unit/engineering/test_review_governance.py tests/unit/cli/test_review_runs.py -k "rounds" -v`

**Step 4: Implement** — count `review_round_started` events for the change whose payload `reviewer` matches and `automatic` is `True`, **without filtering on `epoch_id`**. Point both `needs_authorization` sites at it.

Authorized rounds keep counting as automatic (`cli/review.py:1074`), so each round past the budget is a fresh decision — do not change that.

**Step 5: Replay against the corpus**

`test_corpus_budget_fires_only_on_divergent_changes`: with the budget at 6, the number of rounds requiring authorization per corpus change matches the design document's table. Assert the totals and that the six converging changes require **zero**.

**Step 6: Run the full suite and commit**

Run: `pytest tests/ -x -q`

```bash
git commit -am "fix(review): count the round budget per change, not per epoch"
```

---

### Task 6: The evidence derivation

A pure fold over one change's events producing what a human needs to decide whether to fund another round. No I/O, never raises — same discipline as `value_report`.

**Files:**
- Create: `src/super_harness/engineering/review_budget.py`
- Test: `tests/unit/engineering/test_review_budget.py`

**Step 1: Write the failing tests**, one per datum, each replayed against a corpus change:

- round number, counted per change
- the per-round `blocker+major` curve and the per-round total-findings curve, in round order
- cumulative tokens including cache (reusing Task 2's counter — do not write a second one)
- per source: consecutive rounds missing, and the reason recorded on the most recent `review_run_failed`
- whether the last round improved on the one before

Malformed payloads degrade to omitted entries, never an exception: `test_evidence_never_raises_on_malformed_payloads`.

**Step 2: Run to verify they fail.**

**Step 3: Implement.**

**Step 4: Assert the headline case** — `test_corpus_06_evidence_shows_flat_curve`: `corpus-06` at round 7 reports curve `[3,2,3,2,2,1]`, no improvement, and codex missing for 6 consecutive rounds.

**Step 5: Run and commit**

```bash
git commit -am "feat(review): derive round-budget evidence from the event stream"
```

---

### Task 7: The block carries the evidence and is recorded

The **block** is the load-bearing surface, not the authorization prompt: the human reads what the agent says, not the CLI's stderr, and an agent without the numbers can only ask for approval.

**Files:**
- Modify: `src/super_harness/core/events.py:27-46` (register `review_budget_exceeded` in `EXTENSION_EVENT_TYPES`)
- Modify: `src/super_harness/core/transitions.py:19-26` (add it to `_INFORMATIONAL` — state-preserving, like `gate_bypassed`)
- Modify: `src/super_harness/cli/review.py:923-935` (the block site)
- Test: `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing tests**

- `test_budget_block_prints_evidence`: the block message carries all five items from Task 6 and an explicit instruction to the agent — stop, relay this verbatim to the human, do not retry, do not route around it.
- `test_budget_block_emits_event`: a `review_budget_exceeded` event lands in `events.jsonl` carrying the evidence, and the change's state is unchanged.
- `test_budget_block_event_is_state_preserving`: replaying the stream through the reducer leaves the state as it was.

Advisory text alone is not enough — this repo's own research found that specifications read into context and not followed is the widespread failure. The event is the half the agent cannot wash.

**Step 2: Run to verify they fail.**

**Step 3: Implement.** `_emit_review_event` already exists; the event is emitted **before** `sys.exit(EXIT_VALIDATION)`.

**Step 4: Surface it in `report`** — "this change hit the round budget N times", counted from the new event, next to the existing bands.

**Step 5: Run and commit**

```bash
git commit -am "feat(review): carry evidence into the budget block and record it"
```

---

### Task 8: The authorization prompt repeats the evidence

Insurance, not the primary surface — by the time someone types `review authorize` the decision is usually already made.

**Files:**
- Modify: `src/super_harness/cli/review.py:1136-1250` (`authorize_round`)
- Test: `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing test** — `test_authorize_prompt_shows_evidence`: the interactive confirmation displays the same five items before asking for the reason.

**Step 2–4: Run, implement, run.**

**Step 5: Commit**

```bash
git commit -am "feat(review): show round-budget evidence at authorization"
```

---

### Task 9: Documentation and change closure

**Files:**
- Modify: `docs/getting-started.md:347,351` (the `max_automatic_rounds_per_epoch` examples)
- Modify: `AGENTS.md` via `super-harness sync --agents-md` (never by hand)

**Step 1:** Update the governance examples to the new key and note the per-change semantics and the differing per-role defaults.

**Step 2:** Regenerate `AGENTS.md`.

Run: `super-harness sync --agents-md`

**Step 3:** Verify no dead documentation references.

Run: `super-harness doc check`

**Step 4:** Full suite, lint, and the harness's own checks.

Run: `pytest tests/ -q && ruff check src tests && super-harness verify`

**Step 5: Commit, then run the change through its own lifecycle**

```bash
git commit -am "docs: per-change round budget"
super-harness plan ready 2026-08-06-plan-review-round-budget --scope <every file in this plan's frontmatter>
```

Then plan review, implementation, code review, `attest write`, PR. Per the self-hosting rule, the declared scope must cover **every** changed file or the merge gate rejects the attestation.

---

## Out of scope for this cut

Cut 2 (the finding ledger) and Cut 3 (the severity threshold) are separate changes, in that order — see the design document. Nothing here touches finding semantics, `verdict_blocks`, `derive_open_findings`, or the prompt text.

Not touching the prompt is deliberate and load-bearing: prompt text feeds `contract_digest` (`review_contract.py:344-390`), so a cut that rewrites it invalidates every in-flight frozen contract. Cut 1 leaves rounds in progress completable across the upgrade.
