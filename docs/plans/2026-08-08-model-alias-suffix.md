---
change: 2026-08-08-model-alias-suffix
stage: plan
scope:
  files:
    - docs/plans/2026-08-08-model-alias-suffix.md
    - src/super_harness/cli/review.py
    - tests/unit/cli/test_review_runs.py
tier_hint: Micro
---

# A legitimate review result is refused as tampering whenever the model alias carries a bracketed suffix

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop refusing completed, honest review receipts because the requested alias and the reported canonical id are the same model written two ways.

**Architecture:** One predicate, `cli/review.py:_model_contradicts`. No new state, no config, no event, no caller changes. The guard keeps its teeth on the case it exists for — a genuinely different model — and stops firing on a naming shape it was never designed against.

**Tech Stack:** Python 3.10+, pytest. No new dependencies.

## How it was found

Three repositories, one month, and a paid receipt that cannot be imported.

`_model_contradicts` accepts a requested/reported pair when either identifier is a case-insensitive substring of the other. That is true for the case it was written for — `opus` requested, `claude-opus-4-1-20250805` reported — because the alias sits contiguously inside the canonical id.

It is structurally impossible for an alias carrying a bracketed context-window suffix. The alias is shaped `<family><suffix>`, the canonical id is shaped `<vendor>-<family>-<version><suffix>`, and the version between them breaks contiguity:

```
'opus[1m]'  vs  'claude-opus-5[1m]'         -> judged a contradiction   (wrong)
'opus'      vs  'claude-opus-4-1-20250805'  -> consistent               (right)
'opus'      vs  'claude-sonnet-5'           -> judged a contradiction   (right)
```

Verified rather than reasoned: `claude --print --model 'opus[1m]'` runs here, exits `is_error: false`, and self-reports `claude-opus-5[1m]`. The CLI honours the alias; the configuration is correct and the harness is wrong.

`tapscribe` currently holds a frozen plan round with a completed claude receipt — `is_error: false`, `total_cost_usd: 0.68` — that `review result import` refuses. Two other repositories worked around the same defect by writing the fully-qualified id into `.harness/review-profiles.local.yaml`, which is why a month passed without anyone naming it. It was recorded as a known-unfixed observation when #87 shipped.

Two regression tests already cover this predicate end-to-end through the CLI — `test_import_accepts_dated_model_variant` and `test_import_rejects_contradictory_model` (`tests/unit/cli/test_review_runs.py:1121,1134`, both tagged PR#79 finding #6). Neither names the function, which is why a symbol grep reports no coverage and why an earlier draft of this plan asserted there was none. The new cases **extend** that pair; they do not replace it, and both existing assertions must keep their current answers.

## Task 1 — Compare a bracketed suffix separately from the base identifier

`src/super_harness/cli/review.py:1398-1414` · tests in `tests/unit/cli/test_review_runs.py`

Split a single trailing bracketed suffix off both identifiers, then decide on the two parts.

**The suffix rule is asymmetric, and the asymmetry is the whole point.** The existing dated-variant doctrine — a more-specific reported id is an honoured request — only licenses the direction where the *report* carries more than the request. A requested suffix that the report drops is the opposite: the contract froze a 1M-context variant and the producer answered with the standard one. That is a producer answering with a different model than the one frozen, which is exactly what this guard exists to catch, and it must not be waved through just because it happens to be a *missing* qualifier rather than a *different* one.

Behaviours to pin, both directions — a one-sided assertion passes under an inverted implementation:

- An alias and a canonical id that differ only by vendor prefix and version, carrying the **same** suffix, are consistent.
- An alias and a canonical id whose **bases are disjoint** contradict, with or without suffixes — the guard must not be softened into always-true.
- **Different** suffixes on both sides contradict: a request for one context-window variant answered by another is not the request being honoured.
- **The report omitting a suffix the request carried contradicts** — same failure as answering with the wrong variant, and today it would import clean while recording an `actual_model` that silently ran at a different context window.
- **The request omitting a suffix the report carries is consistent** — that is the more-specific-report case the existing doctrine already covers.
- The two pre-existing regression cases keep their current answers: the dated-variant pair stays consistent, the disjoint pair stays a contradiction. An empty identifier on either side stays non-blocking.

## Done when

`pytest tests/ -q && ruff check src tests && mypy src && super-harness doc check && super-harness verify` all pass, and `tapscribe`'s stranded receipt imports against its existing frozen round without that repository changing any configuration.

---

## Out of scope

**Normalizing model identifiers generally.** A vendor could invent a third naming shape tomorrow and this predicate would need touching again. Building a model-identity abstraction to pre-empt that is speculative: two shapes are known, both are handled after this change, and the guard's job is narrow — catch a producer answering with a different model than the one frozen into the contract.

**Receipts that are refused leaving no trace.** The reason this defect cost money three times rather than once is that a refused import emits nothing at all, so the spend is invisible to `report`. That is #103 and is not fixed here.

**The second blocker in `tapscribe`.** Its `codex` source is unusable for two unrelated reasons — the account rejects the configured model, and its quota is exhausted until 2026-08-28 — so that repository cannot reach `min_independent: 2` regardless of this fix. That is a configuration decision for its owner, not a defect here.
