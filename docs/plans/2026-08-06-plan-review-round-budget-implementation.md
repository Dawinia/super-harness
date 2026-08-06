---
change: 2026-08-06-plan-review-round-budget
stage: plan
scope:
  files:
    - docs/plans/2026-08-06-plan-review-convergence-design.md
    - docs/plans/2026-08-06-plan-review-round-budget-implementation.md
    - AGENTS.md
    - docs/getting-started.md
    - docs/state-machine.md
    - .harness/review-governance.yaml
    - src/super_harness/adapters/reviewer/base.py
    - src/super_harness/adapters/reviewer/claude_cli.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/init_plan.py
    - src/super_harness/cli/report.py
    - src/super_harness/cli/review.py
    - src/super_harness/cli/status.py
    - src/super_harness/core/events.py
    - src/super_harness/core/transitions.py
    - src/super_harness/engineering/attestation.py
    - src/super_harness/engineering/review_budget.py
    - src/super_harness/engineering/review_governance.py
    - src/super_harness/engineering/review_runs.py
    - src/super_harness/engineering/value_report.py
    - tests/fixtures/review-corpus/corpus.jsonl
    - tests/fixtures/review-corpus/README.md
    - tests/integration/cli/test_init.py
    - tests/integration/cli/test_status.py
    - tests/unit/adapters/reviewer/test_claude_cli.py
    - tests/unit/cli/test_init_plan.py
    - tests/unit/cli/test_review_prepare.py
    - tests/unit/cli/test_review_runs.py
    - tests/unit/engineering/test_attestation.py
    - tests/unit/engineering/test_review_budget.py
    - tests/unit/engineering/test_review_contract.py
    - tests/unit/engineering/test_review_governance.py
    - tests/unit/engineering/test_review_profiles.py
    - tests/unit/engineering/test_value_report.py
tier_hint: Normal
---

# Cut 1 — The Brake: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Design:** `docs/plans/2026-08-06-plan-review-convergence-design.md` carries the evidence and rationale for every decision below. Read it first.

**Goal:** Make the review round budget actually bind on the plan path, and make the harness state its own cost, round history, and reviewer availability at the moment it blocks — not afterwards in a report.

**Architecture:** Three independent repairs plus one new derivation. (a) The budget stops counting epochs and counts rounds per `(change, reviewer)`, because `plan_ready` re-fires on every rejection and resets it. (b) A new pure module folds the event stream into the evidence a human needs to decide whether to fund another round, and the CLI prints it both at the block and at authorization. (c) Cost accounting learns about cached tokens, the claude adapter stops discarding `session_id`, and a round that lost a review source says so.

**Tech Stack:** Python 3.10+, click, pytest. No new dependencies.

**Contracts live in the code, not in this document.** Exact dataclass fields and function signatures are decided at implementation time and pinned by the tests named here. This plan states behaviour and test names; it deliberately does not hand-maintain type signatures in prose — that practice is the specific failure this whole change exists to prevent.

**Task order is a dependency order.** Task 4 (the counter) is the foundation for Task 5 (evidence), which is the foundation for Tasks 6–8. Do not reorder.

**Historical plan documents are not touched.** `docs/plans/2026-07-11-review-contract-compiler.md` and `docs/plans/2026-07-16-per-severity-round-policy-plan.md` mention the old config key; they are records of what was true when written and are deliberately left alone.

---

### Task 1: Redacted replay corpus

The acceptance evidence for Tasks 4–8. Exported once from `/Users/dawinialo/Work/vice/pantheon/.harness/events.jsonl`, which is private and cannot be a test dependency.

**Files:**
- Create: `tests/fixtures/review-corpus/corpus.jsonl`
- Create: `tests/fixtures/review-corpus/README.md`
- Test: `tests/unit/engineering/test_review_budget.py`

**Step 1: Write the export script to the scratchpad** (not the repo — it runs once)

Keep per event: `event_id`, `type`, `change_id`, `timestamp`, `actor`, `framework`, and from `payload` only `reviewer`, `epoch_id`, `round_id`, `run_id`, `source`, `automatic`, `outcome`, `missing_sources`, `min_independent`, each finding's `severity`, and `receipt.usage`'s four token counts.

Drop entirely: finding `summary`, `file`, `id`; `contract_digest`, `bundle_digest`, `profile_digest`, `target_head`, `result_digest`; every path; **`reason` free text** (see Task 5 Step 4 for the consequence).

Rewrite: `change_id` → `corpus-01` … `corpus-08` in first-appearance order; `actor.identifier` → `corpus@example.invalid`.

**Step 2: Verify the redaction mechanically**

Run: `grep -Eio 'pack|rebuttal|ranking|slug|docs/|[0-9a-f]{40}' tests/fixtures/review-corpus/corpus.jsonl | sort -u`
Expected: no output.

**Step 3: Verify the curves survived**

`test_corpus_reproduces_known_curves` asserts the eight per-change `blocker+major` sequences from the design document's table, e.g. `corpus-06` (`stage5b`) is `[3,2,3,2,2,1,2,3,1,1]`.

Run: `pytest tests/unit/engineering/test_review_budget.py -v`
Expected: PASS.

**Step 4: Write the README** — what the corpus is, that it is redacted, which real changes it came from by shape rather than by name, **which fields were dropped and therefore cannot be corpus-tested**, and that it is a regression asset: any future change to review policy replays against it.

**Step 5: Commit**

```bash
git add tests/fixtures/review-corpus tests/unit/engineering/test_review_budget.py
git commit -m "test: add redacted plan-review replay corpus (69 rounds, 8 changes)"
```

---

### Task 2: Cost accounting counts cached tokens and records reported cost

**Files:**
- Modify: `src/super_harness/engineering/value_report.py:212` (`_usage_tokens`), and the report's cost band
- Modify: `src/super_harness/adapters/reviewer/base.py:33-41` (`ReviewerProtocolResult` gains an optional reported-cost field)
- Modify: `src/super_harness/adapters/reviewer/claude_cli.py:146-152`
- Modify: `src/super_harness/cli/review.py` (carry the field into the receipt)
- Modify: `src/super_harness/cli/report.py`
- Test: `tests/unit/engineering/test_value_report.py`, `tests/unit/adapters/reviewer/test_claude_cli.py`

**Step 1: Write the failing token tests**

- `test_usage_tokens_counts_cache_reads`: usage shaped like a real claude receipt — `input_tokens: 40`, `output_tokens: 15610`, `cache_read_input_tokens: 1614779`, `cache_creation_input_tokens: 85246` — totals 1,715,675, not 15,650.
- `test_usage_tokens_prefers_reported_total`: `total_tokens` still wins when present.
- `test_usage_tokens_absent_usage_is_none`: `None`, not `0` — the report distinguishes "not captured" from "zero".

**Step 2: Write the failing reported-cost tests**

- `test_parse_result_records_reported_cost`: `claude --print` returns `total_cost_usd` at the top level (observed: `1.340269` on this change's own first plan round) and we currently discard it. The parsed result carries it.
- `test_parse_result_tolerates_missing_cost` and a non-numeric value → `None`. Never fabricated; producers that do not report it simply have none.
- `test_report_sums_reported_cost`: the report's cost band shows the sum alongside tokens, and omits the line entirely when no run reported a cost.

**Step 3: Run to verify both sets fail**

Run: `pytest tests/unit/engineering/test_value_report.py tests/unit/adapters/reviewer/test_claude_cli.py -k "usage_tokens or cost" -v`
Expected: FAIL — token total 15650; no cost field.

**Step 4: Implement.** Add the two cache fields to the fallback sum; non-int values contribute zero and the function still never raises. Thread the optional reported cost adapter → result → receipt → report.

**Step 5: Run and commit**

```bash
git commit -am "fix(report): count cached tokens and record producer-reported cost"
```

---

### Task 3: The claude adapter stops discarding `session_id`

`ReviewerProtocolResult.session_id` already exists (`adapters/reviewer/base.py:38`) and `codex_cli.py:125` populates it; `claude_cli.py` does not, so every claude receipt records `null`. Pure recording — nothing depends on it yet.

**Files:**
- Modify: `src/super_harness/adapters/reviewer/claude_cli.py:146-152`
- Test: `tests/unit/adapters/reviewer/test_claude_cli.py`

**Step 1: Write the failing test** — `test_parse_result_records_session_id`: raw output containing `"session_id": "2133c52b-5782-4e99-8580-dd43294cce54"` yields that value. Plus missing → `None`, and non-string → `None`.

**Step 2: Run to verify it fails.** Run: `pytest tests/unit/adapters/reviewer/test_claude_cli.py -k session -v`

**Step 3: Implement** — one guarded field read in `parse_result`.

**Step 4: Run and commit**

```bash
git commit -am "feat(reviewer): record claude session_id on the receipt"
```

---

### Task 4: The budget counts rounds per change, not per epoch

The foundation for Tasks 5–8. **The rename is a hard error with no deprecation, so every producer and consumer of the key must move in this one task** — otherwise `super-harness init` emits a config that fails its own first governance load, and this repo's self-hosted lifecycle breaks mid-change.

**Files:**
- Modify: `src/super_harness/engineering/review_runs.py` — add a per-change count. **Leave `automatic_rounds_used` and the epoch fold alone**; retry anchoring still needs them.
- Modify: `src/super_harness/engineering/review_governance.py:197-208` — rename, hard-error the old key, per-role defaults
- Modify: `src/super_harness/cli/review.py:907-909, 1111, 1219-1221` — both `needs_authorization` sites and the reported max
- Modify: `src/super_harness/cli/status.py:279` — `remaining_rounds` must read the **same** counter
- Modify: `src/super_harness/cli/init.py:213,217,314` and `src/super_harness/cli/init_plan.py:622` — the generated config
- Modify: `.harness/review-governance.yaml:17,21` — this repo's own governance
- Test: `tests/unit/engineering/test_review_governance.py`, `tests/unit/cli/test_review_runs.py`, `tests/integration/cli/test_status.py`, `tests/integration/cli/test_init.py`, `tests/unit/cli/test_init_plan.py`, `tests/unit/cli/test_review_prepare.py`, `tests/unit/engineering/test_review_contract.py`, `tests/unit/engineering/test_review_profiles.py`

**Step 1: Write the failing governance tests**

- `test_old_rounds_key_is_rejected`: a config carrying `max_automatic_rounds_per_epoch` raises `ReviewGovernanceError` naming both keys and stating that the semantics changed from per-epoch to per-change. **No deprecation period, no silent acceptance** — the same `2` would quietly mean something else.
- `test_plan_reviewer_rounds_default_is_six` / `test_code_reviewer_rounds_default_is_two`: defaults differ per role.

**Step 2: Write the failing generator test**

`test_init_emits_per_role_round_budgets` (integration): a freshly `init`-ed repo's `review-governance.yaml` loads without error and yields 6 for `plan-reviewer`, 2 for `code-reviewer`. The generators in `init.py` and `init_plan.py` currently share one role template and cannot express differing values — split it.

**Step 3: Write the failing counting test**

`test_automatic_rounds_survive_plan_resubmit`: a stream with three `plan_ready` boundaries and one automatic round each yields a per-change count of 3. (Today's `automatic_rounds_used` reads 1 — that is the bug.)

**Step 4: Write the failing status test**

`test_status_remaining_rounds_uses_per_change_count`: after three rounds across three epochs with a budget of 6, `status` reports 3 remaining, not 6. Without this, `status` tells the agent it has budget left and `review begin` then blocks it — reintroducing exactly the misinformation this cut exists to remove.

**Step 5: Run to verify all fail.**

Run: `pytest tests/ -k "rounds or round_budget" -v`

**Step 6: Implement.** Count `review_round_started` events for the change whose payload `reviewer` matches and `automatic` is `True`, **without filtering on `epoch_id`**. Point `review begin`, `review authorize` and `status` at that one counter.

Authorized rounds keep counting as automatic (`cli/review.py:1074`), so each round past the budget stays a fresh decision — do not change that.

**Step 7: Replay against the corpus**

`test_corpus_budget_fires_only_on_divergent_changes`: with the budget at 6, per-change authorization counts match the design document's table, and the six converging changes require **zero**.

**Step 8: Run the full suite and commit**

Run: `pytest tests/ -x -q`

```bash
git commit -am "fix(review): count the round budget per change, not per epoch"
```

---

### Task 5: The evidence derivation

A pure fold over one change's events producing what a human needs to decide whether to fund another round. No I/O, never raises — the same discipline as `value_report`.

**Files:**
- Create: `src/super_harness/engineering/review_budget.py`
- Test: `tests/unit/engineering/test_review_budget.py`

**Step 1: Write the failing tests**, one per datum, replayed against the corpus:

- round number, counted per change (Task 4's counter — do not write a second one)
- the per-round `blocker+major` curve and the per-round total-findings curve, in round order
- cumulative tokens including cache (Task 2's counter — do not write a second one)
- per source: consecutive rounds missing
- whether the last round improved on the one before

**Step 2: Run to verify they fail.**

**Step 3: Implement.**

**Step 4: The failure *reason* is fixture-tested, not corpus-replayed.**

The evidence also surfaces the reason recorded on the most recent `review_run_failed`, because "the second reviewer is unavailable" is useless without "why". Task 1 strips `reason` free text from the corpus, so this one datum **cannot** be validated there. Pin it with a hand-written fixture in `test_review_budget.py` and say so in the test name: `test_missing_source_reason_from_fixture_not_corpus`. Do not weaken the corpus redaction to make a test easier.

**Step 5: Assert the headline case** — `test_corpus_06_evidence_shows_flat_curve`: `corpus-06` at round 7 reports curve `[3,2,3,2,2,1]`, no improvement, and codex missing for 6 consecutive rounds.

**Step 6: Run and commit**

```bash
git commit -am "feat(review): derive round-budget evidence from the event stream"
```

---

### Task 6: A round that lost a review source says so

`cli/review.py:1463-1475` checks rejection **before** missing sources, so a round where one reviewer rejected and the other died closes as a clean `rejected` and the death is never mentioned. **Do not change that ordering** — a rejection is a rejection regardless of quorum. Only the output changes.

**Files:**
- Modify: `src/super_harness/cli/review.py` (the import-result output block near `1880`)
- Test: `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing test** — `test_round_close_reports_missing_source`: two required sources, one imported, one recorded as failed; the round closes `rejected` and stdout carries a warning naming the missing source, its recorded reason, and how many consecutive rounds it has now missed. The JSON envelope gains the same as structured data.

**Step 2: Run to verify it fails.**

**Step 3: Implement** using Task 5's module. Print only when non-empty.

**Step 4: Run and commit**

```bash
git commit -am "feat(review): report a round's missing review sources at close"
```

---

### Task 7: The block carries the evidence and is recorded

The **block** is the load-bearing surface, not the authorization prompt: the human reads what the agent says, not the CLI's stderr, and an agent without the numbers can only ask for approval.

**Files:**
- Modify: `src/super_harness/core/events.py:27-46` (register `review_budget_exceeded` in `EXTENSION_EVENT_TYPES`)
- Modify: `src/super_harness/core/transitions.py:19-26` (add it to `_INFORMATIONAL` — state-preserving, like `gate_bypassed`)
- Modify: `docs/state-machine.md:69-80` (the generated list of state-preserving events; `doc check` enforces it)
- Modify: `src/super_harness/cli/review.py:923-935` (the block site)
- Modify: `src/super_harness/engineering/value_report.py`, `src/super_harness/cli/report.py` (count and render budget hits)
- Modify: `src/super_harness/engineering/attestation.py` (merge-time disclosure)
- Test: `tests/unit/cli/test_review_runs.py`, `tests/unit/engineering/test_value_report.py`, `tests/unit/engineering/test_attestation.py`

**Step 1: Write the failing tests**

- `test_budget_block_prints_evidence`: the block message carries all of Task 5's items plus an explicit instruction to the agent — stop, relay this verbatim to the human, do not retry, do not route around it.
- `test_budget_block_emits_event`: a `review_budget_exceeded` event lands in `events.jsonl` carrying the evidence.
- `test_budget_block_event_is_state_preserving`: replaying through the reducer leaves the state unchanged.
- `test_report_counts_budget_hits`.
- `test_attestation_discloses_budget_hits`: the merge attestation states how many times this change hit the round budget.

Advisory text alone is not enough — this repo's own research found that specifications read into context and not followed is the widespread failure. **Both halves ship together**: the design commits to `report` *and* the merge disclosure, so a cut that lands only the report drops the un-washable half.

The disclosure is informational, like `review-independence`, not a merge blocker like an undisclosed gate bypass: hitting the budget is a legitimate, human-authorized act. It must be *visible*, not *forbidden*.

**Step 2: Run to verify they fail.**

**Step 3: Implement.** `_emit_review_event` already exists; emit **before** `sys.exit(EXIT_VALIDATION)`.

**Step 4: Run and commit**

```bash
git commit -am "feat(review): carry evidence into the budget block, record and disclose it"
```

---

### Task 8: The authorization prompt repeats the evidence

Insurance, not the primary surface — by the time someone types `review authorize` the decision is usually already made.

**Files:**
- Modify: `src/super_harness/cli/review.py:1136-1250` (`authorize_round`)
- Test: `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing test** — `test_authorize_prompt_shows_evidence`: the interactive confirmation displays the same items before asking for the reason.

**Step 2–4: Run, implement, run.**

**Step 5: Commit**

```bash
git commit -am "feat(review): show round-budget evidence at authorization"
```

---

### Task 9: Documentation and change closure

**Files:**
- Modify: `docs/getting-started.md:347,351`
- Modify: `AGENTS.md` via `super-harness sync --agents-md` (never by hand)

**Step 1:** Update the governance examples to the new key, the per-change semantics, and the differing per-role defaults.

**Step 2:** Regenerate `AGENTS.md`.

Run: `super-harness sync --agents-md`

**Step 3:** Verify no dead documentation references.

Run: `super-harness doc check`

**Step 4:** Full suite, lint, and the harness's own checks.

Run: `pytest tests/ -q && ruff check src tests && super-harness verify`

**Step 5: Commit and re-enter the lifecycle**

```bash
git commit -am "docs: per-change round budget"
```

Then implementation review, `attest write`, PR. The declared scope must cover **every** changed file or the merge gate rejects the attestation.

---

## Out of scope for this cut

Cut 2 (the finding ledger) and Cut 3 (the severity threshold) are separate changes, in that order — see the design document. Nothing here touches finding semantics, `verdict_blocks`, `derive_open_findings`, or the prompt text.

Not touching the prompt is deliberate and load-bearing: prompt text feeds `contract_digest` (`review_contract.py:344-390`), so a cut that rewrites it invalidates every in-flight frozen contract. Cut 1 leaves rounds in progress completable across the upgrade.
