---
change: 2026-08-08-plan-review-prompt
stage: plan
scope:
  files:
    - docs/plans/2026-08-08-plan-review-prompt.md
    - docs/concepts.md
    - docs/getting-started.md
    - src/super_harness/core/review_checklist.py
    - src/super_harness/engineering/review_contract.py
    - tests/unit/core/test_review_bundle.py
    - tests/unit/core/test_review_checklist.py
    - tests/unit/engineering/test_review_contract.py
tier_hint: Micro
---

# The plan reviewer is asked for one finding against an undefined checklist

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Raise how much of a plan's real defect population one review round surfaces, by telling the reviewer what to look at and that it must finish looking.

**Architecture:** Two edits to the frozen prompt, no new machinery, no new state, no config migration. The checklist stops being opaque ids and becomes four defined questions; the instruction block stops being purely restrictive and asks for completeness. Everything downstream — schema enum, coverage check, incremental baseline — already derives from the resolved checklist and follows automatically.

**Tech Stack:** Python 3.10+, pytest. No new dependencies.

## How it was found

Measured, not reasoned. Replayed against `pantheon`, whose recorded event stream is the source of `tests/fixtures/review-corpus/`.

Across its 71 imported plan-review rounds the yield per round is flat — it never decays. A change whose whole run produced four findings and a change whose run produced thirty-seven both returned three to six in round one. Round count tracks the size of the defect population divided by a near-constant per-round yield, not the quality of the document. That constant is the thing worth attacking.

Ten live runs against one frozen plan document at one commit, with this repository's real prompt reproduced byte-for-byte, established which knob moves it:

- Raising reasoning effort moved nothing. Two runs at different effort returned the same single finding, on the same line, with the same checklist statuses.
- Adding one sentence asking for exhaustiveness roughly doubled the yield at unchanged cost — two runs of the identical configuration returned the identical count, so this is not sampling noise.
- Defining the four checklist items and adding a consequence test cut low-value findings by roughly three quarters while raising blocker-and-major, promoted one finding from `minor` to `major` on the correct grounds that the implementer cannot proceed without it, and opened a technology-choice angle no earlier run had taken.

The findings recovered in one round included defects the recorded history did not surface until its third, eighth and tenth rounds.

The prompt is where this lives. `engineering/review_contract.py:150-175` asks the reviewer to report *only* issues caused by the target, *not* to expand, *not* to widen scope — and never once asks it to finish. The result shape reinforces that: `findings: []  # required non-empty when any checklist item fails` is satisfied by one finding.

## Task 1 — Four defined checklist items replace three undefined ones

`src/super_harness/core/review_checklist.py:26-30` · tests in `tests/unit/core/test_review_checklist.py`

`plan-reviewer` currently resolves to `spec-coverage`, `design-soundness`, `scope-declared`. None of the three is defined anywhere in the codebase, documentation or configuration. In the replayed history `design-soundness` was the sole failing item in roughly half of all rejections — an unlabelled item carried the gate.

The replacement is the four questions a human asks of an implementation plan: whether the architecture holds up, whether the technology choices hold up, whether it conforms to this project's norms, and whether it covers the spec.

`scope-declared` is dropped. It is harness bookkeeping, it is mechanically decidable, and the two guards that actually enforce it are elsewhere and unaffected: `scope_sufficient` is a separate verdict field that blocks independently of the checklist, and `compile_review_contract` already fails closed when the assignment scope names no tracked file.

Behaviours to pin:

- Each built-in item carries a definition, and an item with no definition still resolves — a checklist supplied through `.harness/review-checklists.yaml` keeps working unchanged, with or without definitions available.
- The existing YAML resolution and its error cases are untouched: absent or corrupt file falls back to the default, a present-but-empty list is still an error.
- `code-reviewer`'s checklist is not changed by this task.

## Task 2 — The prompt renders the definitions, asks for completeness, and gates on consequence

`src/super_harness/engineering/review_contract.py:115-175` · tests in `tests/unit/engineering/test_review_contract.py`

Three additions to `_review_prompt`, all inside the existing frozen-prompt mechanism:

1. The checklist is rendered as ids with their definitions rather than a JSON array of bare strings. Items without a definition render as the bare id.
2. An instruction to be exhaustive: work the whole target, report every distinct issue that can be substantiated, and treat a single finding as an incomplete review when more exist.
3. A consequence test the reviewer applies before reporting anything: following this document literally, would the implementer build the wrong thing, get stuck, or would two implementers build different things? If none of the three, it is not a finding — regardless of how defensible the observation is.

The consequence test is deliberately phrased on outcome rather than on topic. Roughly two fifths of the findings in the replayed pathological case were internal contradictions between sections of the plan, and most of those did block implementation. A prohibition written as "do not report internal inconsistency" would have discarded them.

Behaviours to pin:

- The rendered prompt names every resolved checklist item, and the recordable-shape section still instructs the reviewer to echo each item exactly once.
- Changing the resolved checklist changes `prompt_digest` and therefore `contract_digest` — a frozen packet compiled before this change cannot silently satisfy the new contract.
- A previously imported result whose checklist does not cover the newly required items loses incremental eligibility and the next round falls back to `full-change`. This is the existing coverage rule in `resolve_source_baseline`; it must degrade to a full re-read, never to a crash or a silent partial target.
- `code-reviewer` prompts keep their current wording for the prior-findings and pass-with-open blocks.

## Task 3 — Documentation says what plan review is now asked to do

`docs/concepts.md:87` · `docs/getting-started.md:407`

Both currently describe the mechanics of plan review — that any checklist fail rejects — without saying what the reviewer is asked to judge. State the four questions and the consequence test, so an adopter configuring `.harness/review-checklists.yaml` knows what they are replacing.

## Done when

`pytest tests/ -q && ruff check src tests && mypy src && super-harness doc check && super-harness verify` all pass, and a plan-review `review prepare` freezes a prompt whose checklist section carries the four definitions.

---

## Out of scope

**Running the same review twice and taking the union.** Two identical runs of the improved prompt overlapped on under half their findings, so a union is measurably richer than either. It is not here because it cannot be shown to beat simply running another round — the two buy comparable findings per dollar, and the one thing that would separate them, whether a larger fix batch manufactures proportionally more new defects, was never measured.

**Passing prior findings into plan review.** `review_contract.py:206-210` hardcodes them empty for `plan-reviewer`. Four runs, half with prior findings and half without, showed no difference that survives the observed variance. Neither adding nor keeping this is supported by evidence, so it stays as it is.

**Mechanical checks at `plan ready`.** Several findings recovered in the replay — a declared scope missing a test file, a document instructing an edit to a section whose title does not match — are decidable by `grep` and should never reach a reviewer. That belongs to its own change, and it is the reason `scope-declared` can leave the checklist without anything being lost.

**Reasoning effort and reviewer model.** Effort was measured and moves nothing; a cheaper model was measured and is both worse and more expensive. Neither is changed here.
