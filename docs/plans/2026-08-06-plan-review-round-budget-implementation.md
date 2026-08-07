---
change: 2026-08-06-plan-review-round-budget
stage: plan
scope:
  files:
    - docs/plans/2026-08-06-plan-review-round-budget-design.md
    - docs/plans/2026-08-06-plan-review-round-budget-implementation.md
    - AGENTS.md
    - docs/getting-started.md
    - docs/state-machine.md
    - private/OPEN-ITEMS.md
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
    - tests/unit/cli/test_report.py
    - tests/unit/cli/test_review_prepare.py
    - tests/unit/cli/test_review_runs.py
    - tests/unit/core/test_review_bundle.py
    - tests/unit/engineering/test_attestation.py
    - tests/unit/engineering/test_review_budget.py
    - tests/unit/engineering/test_review_contract.py
    - tests/unit/engineering/test_review_governance.py
    - tests/unit/engineering/test_review_profiles.py
    - tests/unit/engineering/test_review_runs.py
    - tests/unit/engineering/test_value_report.py
tier_hint: Normal
---

# Cut 1 — the brake: make the round budget bind, and state the cost at the moment it blocks

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the review round budget actually bind on the plan path, and make the harness state its own round history, cost and reviewer availability at the moment it blocks — not afterwards in a report nobody runs.

**Architecture:** Three independent repairs plus one new derivation. The budget stops counting epochs and counts rounds per `(change, reviewer)`, because `plan_ready` re-fires on every rejection and resets it. A new pure module folds the event stream into the evidence a human needs in order to decide whether to fund another round, and the CLI prints it at the block and again at authorization. The receipt stops discarding what the reviewer CLI already reports — cached tokens, self-reported cost, session id — and a round that lost a review source says so. Every behavioural claim is checked by replaying a redacted corpus of 69 real plan-review rounds.

**Tech Stack:** Python 3.10+, click, pytest. No new dependencies.

**Design:** `docs/plans/2026-08-06-plan-review-round-budget-design.md`, in scope and reviewed alongside this plan, carries the evidence for every decision here: the eight measured finding curves, the four mechanisms verified in the source, the replay table that picks 6 as the plan default, and why this is the first of three cuts.

## Why this plan has no step-by-step choreography

`2026-08-06-plan-scope-fail-open` ran the controlled version of this experiment — same design, same reviewer, same contract. At 199 lines with seventeen numbered steps it produced 2 blocker+major findings, then 2 again. At 141 lines with the choreography deleted it produced 0 and was approved. Its design section was never once contested across eight rounds; four of those rounds landed entirely on prose the previous round's fix had just written, and one round's major was the reviewer rejecting a fact it had itself supplied a round earlier.

Prose about execution detail is an unbounded review surface, and the detail it describes is settled by a test run in seconds. This change exists because that loop does not terminate; its own plan will not feed it. So this document states decisions, boundaries, and the behaviours the tests must pin, and stops. How to sequence the edits is the executor's business; whether they are right is the suite's.

## What this cut does not touch

No finding semantics, no `verdict_blocks`, no `derive_open_findings`, and no prompt text — not because leaving the prompt alone buys contract compatibility, but because those belong to Cuts 2 and 3.

**A round frozen before this upgrade cannot be completed after it, and there is no way around that.** `cli/review.py:718` puts the governance payload into the bundle and `engineering/review_contract.py:412` digests the whole bundle, so Task 3's rename moves `contract_digest` by exactly the mechanism a prompt rewrite would. Any round in flight at upgrade time must be re-frozen. No compatibility shim: the digest is what binds a verdict to what was reviewed, and holding it stable across a semantic change to the budget would make it lie.

Epochs stay. They anchor contract freezing and run retries; only the budget stops counting them, so `review_runs.py:12-13`'s fold and `automatic_rounds_used` keep their present meaning.

No reset channel for the new counter. A change that genuinely needs to start over is closed and reopened, not washed inside itself.

`docs/plans/2026-07-11-review-contract-compiler.md` and `docs/plans/2026-07-16-per-severity-round-policy-plan.md` mention the old config key. They are records of what was true when written and are left alone.

## Dependency order

Task 3's counter is the foundation for Task 4's derivation, which is the foundation for Tasks 5 and 6. Task 1's corpus is the acceptance evidence for Tasks 3–6. Nothing else here is ordered.

---

## Task 1 — the redacted replay corpus

`tests/fixtures/review-corpus/corpus.jsonl` and its `README.md` · tests in `tests/unit/engineering/test_review_budget.py`

Exported once from a private repository's `.harness/events.jsonl`, which cannot itself be a test dependency. Hand-written fixtures are not an option: validating this design against curves we invented would be circular, and the shape that matters — a stable one to three majors per round, indefinitely — is not something we would have thought to fabricate.

**The redaction contract.** Keep event identity and shape: `event_id`, `type`, `change_id`, `timestamp`, `actor`, `framework`, and from the payload only `reviewer`, `epoch_id`, `round_id`, `run_id`, `source`, `automatic`, `outcome`, `missing_sources`, `min_independent`, each finding's `severity` **and `id`**, and the receipt's token counts. Drop every finding summary and file; every digest; every path; and all `reason` free text. Rewrite `change_id` to `corpus-01`…`corpus-NN` in first-appearance order (the rule decides the count, not this sentence), the actor identifier to `corpus@example.invalid`, and each finding id to `c01/f-07` form — **finding ids are kept and rewritten, never dropped**, as the design specifies. They carry no content once the summary and file are gone, and Cut 2 is entirely per-id disposal, so a corpus without them cannot replay the cut it exists to serve. Re-exporting later is not cheap: the source repository is private and cannot be a test dependency.

Dropping `reason` costs exactly one datum: Task 4's missing-source reason cannot be corpus-replayed, and is pinned by a hand-written fixture instead. The redaction is not weakened to make that one test easier.

Behaviours to pin:

- the eight per-change `blocker+major` sequences the design tabulates each reproduce, exactly once. **Assert on the curve, never on a `corpus-NN` id**: the id follows from first-appearance order and no round of review could check a hardcoded mapping without the private source data. An earlier draft of this plan asserted the flat 10-round curve was `corpus-06`; it is `corpus-11`, and `corpus-06` converged in three rounds
- a mechanical scan of the committed corpus finds no residue of the source repository's vocabulary, no digests, and no paths

The README states what the corpus is, which real changes it came from by shape rather than by name, which fields were dropped and therefore cannot be corpus-tested, and that it is a regression asset every future change to review policy replays against.

## Task 2 — the receipt records what the producer already reports

`engineering/value_report.py` (`_usage_tokens:212`, and the report's cost band) · `adapters/reviewer/base.py` (`ReviewerProtocolResult`) · `adapters/reviewer/claude_cli.py` (`parse_result`) · `cli/review.py` (receipt assembly) · `cli/report.py` · tests in `tests/unit/engineering/test_value_report.py`, `tests/unit/adapters/reviewer/test_claude_cli.py`

Three values the reviewer CLIs hand us and we throw away. Pure recording: nothing depends on any of them yet, and none may ever be fabricated when absent.

Cached tokens are where these CLIs actually bill. On the diagnosed change the report showed 161,701 tokens against 13,986,935 real — an 86x blind spot — and Task 4's evidence is worthless until that is fixed.

**The two shipped producers report cache differently, and the fix must not be generalized across them.** claude reports `cache_read_input_tokens` and `cache_creation_input_tokens` as amounts *in addition to* a near-empty `input_tokens` — 63 against 2,629,763 cache-read on this change's own first plan round. codex reports `cached_input_tokens` as a portion *already inside* `input_tokens` — 898,816 of 1,007,614 on `init-interactive-wizard`. So only claude's two keys are summed: adding codex's would inflate its total by roughly 89%, and leaving it alone is already correct. The rule is per named key, never "any key containing cache". codex's `reasoning_output_tokens` stays uncounted in this cut — 3,166 against a million input tokens is below the resolution this datum serves.

The harness still does not price tokens or estimate USD. It does stop discarding a cost the producer states about itself, which is the same class of repair as the session id.

Behaviours to pin:

- claude's two cache keys enter the fallback sum; a non-integer field contributes zero and the function still never raises
- a codex-shaped usage dict totals `input_tokens + output_tokens`, unchanged by this task — the subset key is not added
- a reported `total_tokens` still wins when present
- no usage at all yields `None`, not `0` — the report must distinguish "not captured" from "zero"
- `claude`'s `session_id` reaches the receipt; absent or non-string yields `None`. `base.py:38` already has the field and `codex_cli.py` already populates it, so `claude_cli.py` is the only gap
- the producer's reported cost reaches the receipt and the report sums it beside the token count; absent or non-numeric yields `None`, and the report omits that line entirely when no run reported one

## Task 3 — the budget counts rounds per change, not per epoch

`engineering/review_runs.py` · `engineering/review_governance.py:76,198-199,214` · `cli/review.py:237,907-909,1111,1219-1221` · `cli/status.py:279` · `cli/init.py:213,217,314` · `cli/init_plan.py:622` · `.harness/review-governance.yaml:28,32` · tests in `tests/unit/engineering/test_review_governance.py`, `tests/unit/engineering/test_review_runs.py` (where the existing budget-fold tests live, `:19`), `tests/unit/cli/test_review_runs.py`, `tests/unit/cli/test_review_prepare.py`, `tests/unit/engineering/test_review_contract.py`, `tests/unit/engineering/test_review_profiles.py`, `tests/unit/core/test_review_bundle.py`, `tests/integration/cli/test_status.py`, `tests/integration/cli/test_init.py`

`core/transitions.py:37` sends `PLAN_REJECTED` back out through `plan_ready`, which is `review_runs.py:12`'s plan epoch boundary, so the counter resets on every rejection. That is why twelve rounds produced zero authorizations.

`max_automatic_rounds_per_epoch` becomes `max_automatic_rounds`, and **the old key is a hard error with no deprecation period.** The number's meaning changes from per-epoch to per-change accumulation, and a config that reads identically while its `2` means something else is the same failure as `blocking_severity` being silently inert on the plan path.

**That hard error is why this task is large: every producer and consumer of the key moves in it, atomically.** Leave one behind and `super-harness init` emits a config that fails its own first governance load, or this repository's self-hosted lifecycle breaks mid-change. The eight test modules above are not incidental — each constructs the role or writes the key.

The generators in `cli/init.py` and `cli/init_plan.py` currently share one role template and cannot express differing values per role; that template splits.

Behaviours to pin:

- a config carrying `max_automatic_rounds_per_epoch` raises, naming both keys and stating that the semantics changed from per-epoch to per-change
- defaults differ per role: 6 for `plan-reviewer`, **4** for `code-reviewer`. `review.roles` keys are arbitrary non-empty strings (`engineering/review_governance.py:165-169`), so this is a per-role default table over a **fallback of 2**, today's single literal — any other role name, adopter-defined or future, keeps 2. An unknown role name is not a new error condition; this task does not narrow what `review.roles` accepts
- **the code path is not behaviour-neutral under this rename, and the code default moves 2 → 4 because of it.** Four events re-enter a state that can re-fire `implementation_complete` (`plan_redeclared`, `intent_redeclared`, `implementation_restarted`, `implementation_invalidated`), so a restarted change now carries its pre-restart code rounds forward where the per-epoch counter used to wash them. Keeping 2 would silently tighten the code path under a commit that says "rename". Replayed over the 10 corpus changes that reached code review (of 11 in the fixture): at 2 the brake fires 8 times across 5 of those 10, at 4 it never fires, and the longest recorded code review is 4 rounds
- a change that restarts implementation keeps its code-round count: `implementation_complete` firing a second time does **not** anchor a fresh count. That is the no-reset rule applied consistently, not an oversight — a change that genuinely needs to start over is closed and reopened
- a freshly `init`-ed repository's `review-governance.yaml` loads without error and yields those two values
- three `plan_ready` boundaries with one automatic round each count as three, not one
- `status` and `review begin` read the **same** counter. Otherwise `status` reports budget the agent does not have and `review begin` then blocks it, which is exactly the misinformation this cut exists to remove
- authorized rounds keep counting as automatic, so every round past the budget stays a fresh decision
- replayed against Task 1's corpus at a budget of 6, each change's authorization count equals `max(0, started_automatic_rounds - 6)`, asserted per change in the corpus's own `corpus-NN` ids and **computed by the test from the corpus**, not transcribed from this document or from the design

**A round whose runs failed still consumes budget.** The counter counts `review_round_started`, and `automatic_rounds_used` (`engineering/review_runs.py:73`) already does exactly that. Keep it. A failed round costs real money and produces no findings, which is the worst possible round to make invisible to a brake whose purpose is bounding spend — and a source that keeps failing is itself something Task 5 exists to put in front of a human. Excluding failed rounds would let a change burn unbounded money on half-failing rounds without ever tripping the brake.

**Therefore the design's replay table is not this criterion's expected value, and must not be copied into it.** That table is measured in *imported* rounds (`per imported plan round`, `69 imported plan rounds`, curve lengths summing to 69) while the counter counts started rounds — the design's own text puts one change at 12 started rounds against a 10-entry curve. Started ≥ imported everywhere, so the real prompt counts are at or above the table's.

**Task 3 recomputes that table against started rounds and reports the result** — the corpus carries both `review_round_started` and the imported results, so this is a derivation, not a new measurement. Two consequences to state rather than assume: this cut ships the *noisy* half of the design's pair (the severity threshold that makes it precise is Cut 3, out of scope here), so changes the design classifies as converged **will** be interrupted; and if the recomputed numbers materially change the picture that chose 6, the default is revisited on that evidence. Do not tune the counter to make either table's numbers come out.

## Task 4 — the evidence derivation

`engineering/review_budget.py` (new) · tests in `tests/unit/engineering/test_review_budget.py`

A pure fold over one change's events: no I/O, never raises — the same discipline as `value_report`. It reuses Task 3's counter and Task 2's token sum and does not grow a second one of either.

Behaviours to pin, each replayed against the corpus:

- which round this is — **Task 3's started-round count**, the same number the budget compares against, so the block and the counter never disagree
- the per-round `blocker+major` curve and the per-round total-findings curve, in round order. These come from **imported** rounds, because a failed round produced no findings; a round counted by the budget but absent from the curve is exactly the case the human needs to see, so the evidence states both counts rather than reconciling them
- cumulative tokens including cache
- per review source, how many consecutive rounds it has been missing
- whether the last round improved on the one before — defined as its `blocker+major` count against the previous round's, on that curve and no other
- the headline case, with every number **derived from the corpus by the test**, never transcribed: at the round where `corpus-06` first exceeds a budget of 6, the evidence reports its findings curve so far, **an improving last step**, and one source missing for every round so far. That combination is the point — the rounds bought a small net reduction while a required reviewer was absent throughout, and the improving last step is exactly what talks an automatic rule, or a human reading only the last two numbers, into funding one more round

The reason recorded on the most recent `review_run_failed` belongs here too — "the second reviewer is unavailable" is useless without "why". Task 1 strips that free text, so this one datum is pinned by a hand-written fixture, and the test name says which datum is not corpus-backed.

No automatic divergence verdict. A "three rounds without improvement" rule was tested against this corpus and both pathological changes defeat it at rounds 4–6, decreasing locally while staying flat globally. A human reading `3,2,3,2,2,1,2,3,1,1` needs no rule.

## Task 5 — a round that lost a review source says so

`cli/review.py` (the import-result output block) · tests in `tests/unit/cli/test_review_runs.py`

The data is already in the `review_round_closed` payload and nothing reads it. On the diagnosed change one source was dead from round 1 and eleven consecutive single-source rounds passed unremarked. This information is worth most at round 1 and worth nothing by round 7.

Close ordering checks rejection **before** missing sources, so a round where one reviewer rejected and the other died closes as a clean `rejected` and the death is never mentioned. **Do not change that ordering** — a rejection is a rejection regardless of quorum. Only the output changes.

Behaviours to pin:

- two required sources, one imported and one recorded as failed: the round still closes `rejected`, and stdout carries a warning naming the missing source, its recorded reason, and how many consecutive rounds it has now missed
- the JSON envelope carries the same as structured data
- nothing is printed when no source is missing

## Task 6 — the block carries the evidence, records it, and discloses it

`core/events.py:27` (`EXTENSION_EVENT_TYPES`) · `core/transitions.py:19` (`_INFORMATIONAL`) · `docs/state-machine.md:69-80` · `cli/review.py:907-935` (the block) and `:1142-1250` (`authorize_round`) · `engineering/value_report.py` · `cli/report.py` · `engineering/attestation.py` · tests in `tests/unit/cli/test_review_runs.py`, `tests/unit/engineering/test_value_report.py`, `tests/unit/engineering/test_attestation.py`

The **block** is the load-bearing surface, not the authorization prompt. The human does not read the CLI's stderr; the human reads what the agent says. An agent without the numbers can only say "I was blocked, please approve", which is the rubber-stamp path. By the time anyone types `review authorize` the decision is already made, so the prompt repeating the evidence is insurance, not the primary channel.

Advisory text is necessary and not sufficient: this repository's own research concluded that specifications read into context and not followed is the actual widespread failure. So the block is also recorded in the event stream, and both `report` and the merge attestation surface it whether or not the agent relayed anything. `gate-blocks.jsonl` is deliberately not reused — its schema serves pre-tool-use blocks and it lives outside the event stream to protect that fail-open hot path, neither of which applies inside `review begin`, which already emits events.

The disclosure is informational, like `review-independence`, not a merge blocker like an undisclosed gate bypass. Hitting the budget is a legitimate, human-authorized act: it must be *visible*, not *forbidden*.

Behaviours to pin:

- the block message carries every item from Task 4, plus an explicit instruction to the agent: stop, relay this verbatim to the human, do not retry, do not route around it
- a `review_budget_exceeded` event lands in `events.jsonl` carrying the evidence, emitted before the process exits
- replaying that event through the reducer leaves the state unchanged, and `docs/state-machine.md` lists it among the state-preserving events (`doc check` enforces that list)
- `report` counts **distinct over-budget rounds**, not blocks: one `review begin` invocation per refused round emits one event, and an agent that retries — the behaviour the block text forbids and cannot prevent, which is the whole premise of recording the event at all — must not be able to inflate the figure. Count unique attempted rounds per reviewer
- the merge attestation discloses the same count, under a label that says rounds
- the authorization prompt displays the same items before asking for a reason

Both halves ship together: a cut that lands only the report drops the half the agent cannot wash.

## Task 7 — documentation and closure

`docs/getting-started.md:347,351` · `AGENTS.md` regenerated via `super-harness sync --agents-md`, never by hand · `private/OPEN-ITEMS.md`

The governance examples move to the new key, the per-change semantics, and the differing per-role defaults. `AGENTS.md` is regenerated and may come back unchanged; it is declared because the generator decides that, not the author.

**The codex retirement stays, is reviewed here, and restoring codex is recorded rather than performed.** `.harness/review-governance.yaml` drops codex from both roles on this branch. Two independent blockers, both measured on 2026-08-07:

- the pinned model is rejected. `gpt-5.6-sol`, `gpt-5.6-codex`, `gpt-5.1-codex` and `gpt-5-codex` each return HTTP 400 `not supported when using Codex with a ChatGPT account`; `gpt-5.5` is accepted. That pin is a value this repository chooses, in the gitignored `.harness/review-profiles.local.yaml`, and it is already corrected there — which is exactly why it cannot be the recorded blocker.
- with an accepted model the account answers `You've hit your usage limit … try again at Aug 28th, 2026`. This one is external: the only local levers are a paid upgrade or waiting. The design document already recorded this quota lock for the diagnosed change's dead codex source.

Restoring codex inside this change is unsound whichever ordering is chosen:

- restore **before** code review and the change can never merge. `engineering/review_governance.py:192` forces `min_independent` to equal the participant count, so codex comes back as a *required* source, and `cli/review.py:1474-1476` closes any round missing a required source as `execution_failed`. Code review would be unreachable, not merely single-source.
- restore **after** `code_review_passed` and the file governing review independence merges having never been reviewed. `super-harness plan redeclare` is legal from `READY_TO_MERGE` (`core/transitions.py:108-110`; only `ARCHIVED`/`ABANDONED` are terminal), so there *is* a way back into review — and taking it lands in the first bullet, where the restored source cannot run. The trap is the loop, not a missing exit.

So the retirement is part of this change and is reviewed with everything else, and the attestation states that both this plan review and the code review ran single-source.

**Task 7 updates the existing register entry, it does not add a second one.** `private/OPEN-ITEMS.md` already carries this item — ⑤ `codex 评审源的模型 pin`, recording that PRs #87–#89 all ran single-source — classified `DOABLE-NOW`. Splitting the same subject across two entries in the register that is the deferred-work single source of truth is worse than either classification. Entry ⑤ is rewritten to name both blockers, to record `gpt-5.5` as the verified-accepted pin, and to reclassify as `BLOCKED-upstream` **on the quota, dated 2026-08-28** — not on the pin, which is fixed. Because the fix landed in a gitignored file, that entry is the only tracked record of it.

## Done when

`pytest tests/ -q && ruff check src tests && super-harness doc check && super-harness verify` all pass; a config carrying the old key fails loudly; a freshly `init`-ed repository loads its own generated governance; and replaying the corpus at a budget of 6 authorizes exactly `max(0, started_automatic_rounds - 6)` times per change, with the recomputed per-change totals recorded by the test and reported.

---

## Out of scope

Cut 2 (the finding ledger) and Cut 3 (the exit condition) are separate changes, in that order — see the design document. Two items from the diagnosis sit beyond all three: reviewer session continuity, which would trade offline recomputability for real reviewer memory, and a way to declare that a class of machine-checkable assertion must not reach an LLM reviewer at all.
