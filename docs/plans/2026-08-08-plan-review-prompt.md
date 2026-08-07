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

**Architecture:** Two edits to the frozen prompt, no new machinery, no new state, no config migration. The checklist stops being opaque ids and becomes four defined questions; the instruction block stops being purely restrictive and asks for completeness.

**The load-bearing constraint:** `resolve_checklist` keeps its signature and keeps returning `list[str]`, so `bundle["checklist"]` keeps its shape. `review_verdict_json_schema` splices that list straight into `"enum": checklist` (`core/review_verdict.py:104`), `resolve_source_baseline` compares it as a set of strings, and `bundle_digest` hashes it. Definitions therefore live in a **separate mapping keyed by item id**, never inside the resolved list. Enriching the list into id+definition objects would break all three at once and push the repair into `cli/review.py`, which is deliberately not in scope — if an implementation needs to touch that file, the design is wrong, not the scope.

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

`src/super_harness/core/review_checklist.py:26-30` · tests in `tests/unit/core/test_review_checklist.py` and `tests/unit/core/test_review_bundle.py`

`test_review_bundle.py` is in scope to carry the regression anchor for the load-bearing constraint above: that `bundle["checklist"]` is a list of plain strings for both reviewers. Its existing `code-reviewer` assertion must keep passing byte-identical, which is what proves Task 1 left that role alone.

`plan-reviewer` currently resolves to `spec-coverage`, `design-soundness`, `scope-declared`. None of the three is defined anywhere in the codebase, documentation or configuration. In the replayed history `design-soundness` was the sole failing item in roughly half of all rejections — an unlabelled item carried the gate.

The replacement is the four questions a human asks of an implementation plan. The ids and the definition text are both specification — the ids become the frozen schema's `enum` and the vocabulary an adopter overrides in `.harness/review-checklists.yaml`, and the wording is the measured artifact, so both are given verbatim rather than paraphrased:

| id | definition, verbatim |
| --- | --- |
| `architecture` | does the design hold up? Layer ownership, dependency direction, state and who owns it, failure paths. Test: would a system built to this design be wrong, deadlock, or silently deliver the wrong value? |
| `tech-choices` | are the chosen libraries, mechanisms and data structures able to carry the responsibilities assigned to them, and do they conflict with choices already made in this repository? |
| `conventions` | does this conform to the norms, ratified decisions and established practice of THIS repository? |
| `spec-coverage` | is everything the spec/requirement asks for actually covered by this plan, and do the acceptance criteria match the body? |

Definitions live in a module-level `dict[str, str]` beside `DEFAULT_CHECKLISTS`, keyed by item id and shared across reviewers. `resolve_checklist` does not read it and does not change.

`scope-declared` is dropped because two mechanical gates already decide it and an LLM opinion adds nothing to either: `sensors/verification_runner.py:483-484` reports every changed file absent from `scope.files`, and `engineering/attestation.py:297` refuses the merge for any changed file no complete lifecycle covers. Neither `scope_sufficient` nor `compile_review_contract`'s fail-closed guard is one of those gates — the first asserts the reviewer's assigned target was adequate, the second only trips when the scope matches *no* tracked file at all — and this change does not touch either.

Behaviours to pin:

- `resolve_checklist` still returns `list[str]` for every reviewer, so `bundle["checklist"]` stays a list of plain strings.
- An id with no definition resolves normally — a checklist supplied through `.harness/review-checklists.yaml` keeps working unchanged whether or not its ids appear in the mapping.
- The existing YAML resolution and its error cases are untouched: absent or corrupt file falls back to the default, a present-but-empty list is still an error.
- `code-reviewer`'s resolved items are unchanged, and none of the five carries a definition.

## Task 2 — The prompt renders the definitions, asks for completeness, and gates on consequence

`src/super_harness/engineering/review_contract.py:115-175` · tests in `tests/unit/engineering/test_review_contract.py`

`_review_prompt` reads the definition mapping from Task 1 by import; its `checklist: list[str]` parameter does not change type. Three additions, all inside the existing frozen-prompt mechanism:

1. The checklist renders as one line per id, carrying its definition when the mapping has one and the bare id when it does not, instead of a JSON array. **This applies to every reviewer** — `code-reviewer`'s five ids render as bare lines.
2. An instruction to be exhaustive, verbatim: *Be exhaustive: work through the entire target and report EVERY distinct issue you can substantiate, not only the most severe one. Returning a single finding when more exist is an incomplete review. Do not stop once the checklist verdict is decided.*
3. A consequence test, **emitted for `plan-reviewer` only**, verbatim: *Before reporting any finding, answer this question: following this document literally, would the implementer BUILD THE WRONG THING, GET STUCK, or would TWO IMPLEMENTERS BUILD DIFFERENT THINGS? If none of the three is true, do not report it — however defensible the observation is. Wording, internal cross-reference numbering, arithmetic, line-number citations and prose consistency are NOT findings unless they change one of those three answers.*

The consequence test is deliberately phrased on outcome rather than on topic. Roughly two fifths of the findings in the replayed pathological case were internal contradictions between sections of the plan, and most of those did block implementation. A prohibition written as "do not report internal inconsistency" would have discarded them.

**The split between 2 and 3 is the rule, not a detail of this change.** An instruction that can only *add* findings is safe to give every reviewer; one that *suppresses* findings must match what it filters. The consequence test is phrased about a *document* and its *implementers* and excludes `arithmetic` — on a code delta a reviewer applying it literally can drop a real off-by-one. Its wording is a measured artefact from plan review, and inventing an unmeasured code-shaped variant is the mistake this change exists to avoid, so code review gets no consequence gate at all. Whether it needs one is deferred to **issue #99**; re-adding this one to code review as a conformance fix would re-open the exposure.

Behaviours to pin:

- The rendered prompt names every resolved checklist item, and the recordable-shape section is **reworded** to demand the bare id — the pre-change "copy each assigned checklist item exactly, once" now literally reads as "copy the whole `- id: definition` line", which is not in the `enum` spliced from the bare-id list (`core/review_verdict.py:104`) and, on the human/aggregate path, yields checklist keys `_aggregate_verdicts` cannot match to the required ids. The id must stay separable from its definition in the rendering, and the instruction must say so.
- **Both roles'** prompts change, so `prompt_digest` and `contract_digest` change for both — a packet frozen before this change cannot silently satisfy the new contract, and an in-flight code-review round must be re-prepared. That is the intended cost of changing what was asked; nothing is grandfathered.
- A previously imported result whose checklist does not cover the newly required items loses incremental eligibility and the next round falls back to `full-change`. This is the existing coverage rule in `resolve_source_baseline`; it must degrade to a full re-read, never to a crash or a silent partial target.
- `code-reviewer` keeps its current prior-findings and pass-with-open wording. It gains the checklist rendering and the exhaustiveness instruction, and it must **not** carry the consequence test.
- The role→flag wiring is pinned **where the role is known**, not only on the pure function. A compiled `plan-reviewer` prompt contains the consequence test and a compiled `code-reviewer` prompt does not — asserted on both sides, because inverting that one comparison is the exact defect this change exists to prevent and a unit test taking the flag as an argument cannot see it.

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
