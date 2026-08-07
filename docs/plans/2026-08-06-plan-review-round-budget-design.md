---
change: 2026-08-06-plan-review-round-budget
stage: design
---

# The plan-review loop is a memoryless sampler, not a convergent loop

> This document diagnoses one problem and designs all three cuts that answer it. It is
> filed under the change that implements Cut 1 — `2026-08-06-plan-review-round-budget` —
> because that is the change that introduces it, and because a design artifact a change
> declares in scope must be identifiable as that change's artifact by both mechanisms that
> key on the change id: its frontmatter and its filename. Cuts 2 and 3 read it; neither
> needs to edit it.

A `pantheon` change (`2026-08-04-stage5b-pack-versions`) ran **12 plan-review rounds**
for a single plan document, burned ~14M reviewer tokens, and never converged. This
document is the diagnosis and the design. Every number below is read from that repo's
`.harness/events.jsonl` and the frozen `prompt.md` files it kept, not estimated.

## The loop converges on five of eight changes, and cannot on the rest

`blocker + major` findings per imported plan round, all eight `pantheon` changes:

| change | per-round blocker+major | outcome |
| --- | --- | --- |
| `remove-card-select` | 3, 2, 0 | converged, 3 rounds |
| `stage2b-interaction` | 2, 1, 0, 0 | converged, 4 rounds |
| `stage5a-pack-input` | 3, 2, 2, 1, 0, 0 | converged, 6 rounds |
| `stage2a-session-state` | 3, 1, 0, 1, 1, 0 | converged, 6 rounds |
| `stage3a-revision-lifecycle` | 2, 1, 2, 0, 1, 1, 1, 0, 0 | converged, 9 rounds |
| `stage3b-rebuttal` | 3, 2, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0 | 18 rounds; majors cleared at round 4, 14 more spent on minors |
| `stage1-ranking-foundation` | 3, 4, 2, 2, 2, 1, 2, 2, 1, 1, 3, 1, 0 | 13 rounds, non-monotone |
| `stage5b-pack-versions` | 3, 2, 3, 2, 2, 1, 2, 3, 1, 1 | **10 rounds, flat, cut off without converging** |

The mechanism is not "the plan was bad". Each round is an **independent full review of a
whole document**, and the expected finding count of such a review is roughly constant
regardless of document quality. The curve is flat because every round re-samples.

## Four mechanisms, each verified in the source

### 1. Every round re-reviews the entire document from the branch point

The frozen inspection argv for all 12 rounds of `stage5b`:

```
round 1  : git diff f914175..d61f46f8 -- docs/plans/2026-08-04-stage5b-pack-versions.md
round 2  : git diff f914175..99d14242 -- docs/plans/2026-08-04-stage5b-pack-versions.md
...
round 12 : git diff f914175..8ee451cc -- docs/plans/2026-08-04-stage5b-pack-versions.md
```

The base never advances. `f914175` is the branch point, at which the plan document did not
exist — so the "delta" is the entire 657-line document, added from nothing, every round.
The prompt's instruction `Review only the assigned target delta` is vacuous here. Round
12's reviewer sees input of exactly the same shape as round 1's: a document it has never
seen, no round number, no history.

### 2. Plan review is denied the memory the code path has

`cli/review.py:1088-1095` freezes `open_finding_ids` as `[]` for any non-code reviewer,
`engineering/review_contract.py` gates the `Open prior findings` prompt section to
`code-reviewer`, and `core/review_verdict.py:210-215` harvests only code-review results.
So `check_disposed` (`cli/review.py:1759`) never enforces anything on the plan path, and
nothing stops a round from re-adjudicating a section three earlier rounds accepted.

The `prior_findings` that do appear in `stage5b` verdicts came from the reviewer reading
the `F-5B-NN` list the author had written into the plan's own prose. The protocol
contributed nothing. Those ids are bare (`F-3B-01`) while ids the harness records are
namespaced (`claude/run_01KYV34.../F-3B-10`, `cli/review.py:1795`) — they could not have
matched even if the ledger had been reading them.

### 3. The exit condition is unreachable, and the one knob that would fix it is a no-op

`core/review_verdict.py:239-270` — a code-review verdict blocks on finding severity vs
`blocking_severity`; a plan-review verdict blocks on `any(item["status"] == "fail")`, with
severity ignored entirely.

`engineering/review_governance.py:201-208` loads and validates `blocking_severity` for
**every** role including `plan-reviewer`, and `cli/review.py:1081` freezes it into the
round payload. It is then never read on the plan path — a configured value is silently
inert. `engineering/review_contract.py:352` sets `pass_with_open = reviewer ==
"code-reviewer"`, so the plan reviewer never sees the sentence explaining that a
sub-threshold finding may pass with the finding left open. Its only channels are `pass`
and `fail`.

Across all eight changes, 69 imported plan rounds: had the plan path honoured a `major`
threshold, **24 rounds (35%) would not have run**. Note what this does *not* fix —
`stage5b` and `stage1` save zero, producing at least one `major` every round. The
threshold treats the long tail, not the pathological case. A `blocker` threshold is not
an option: real blockers surfaced at rounds 3, 4 and 2 of `stage5b`, `stage1` and
`stage3b`, all of which it would have skipped.

### 4. The round budget is wired to an event that re-fires

`engineering/review_runs.py:11-14` sets the epoch boundary to `plan_ready` for
`plan-reviewer` and `implementation_complete` for `code-reviewer`.
`core/transitions.py:37` is `("PLAN_REJECTED", "plan_ready"): "AWAITING_PLAN_REVIEW"` —
the normal revise-and-resubmit path — so it re-fires the plan epoch boundary and
`automatic_rounds_used` resets to zero on every rejection.

Measured on `stage5b`: **12 epochs, 1 round each, 0 authorizations.** Repo-wide: 76
`plan_ready` events, 7 `review_round_authorized`. The circuit breaker never fired once
during the 12-round run it was designed for.

The code path is not symmetric: `implementation_complete` has no edge out of
`CODE_REVIEW_REJECTED`, so its budget bites — `stage2a` shows a real
`review_round_authorized` at its third code round. (`implementation_complete` does fire
twice on three changes, but only via `plan redeclare` sending the change all the way back
to re-planning — a legitimate full restart, not a reject-loop reset.)

## The self-inflicted chain, under a microscope

`stage3b`'s round-7 blocker is not a late discovery:

```
R5  raises F-3B-12 (minor): the declaration is permanently immutable; let revisions edit it
R6  fixes F-3B-12 -> adds `declaration` to PublishRevisionCommand
R6  therefore raises F-3B-17 (minor): unstated whether `declaration` enters submissionContent
R7  fixes F-3B-17 -> puts `declaration` into submissionContent
R7  therefore raises F-3B-18 (BLOCKER): `declaration` may be `undefined`, resolved inside the
    store transaction, but submissionHash is computed in the application layer before that
```

Each step is the direct consequence of the previous step's fix. A reviewer that remembered
proposing the `declaration` field in R6 would have been reasoning about the blast radius of
its own proposal; the memoryless reviewer can only ask "what is wrong with this document
now", and so it bills for the damage one round after causing it.

Fresh eyes are not worthless — `stage3b`'s round-8 `F-3B-21` (button copy contradicting an
approved ROADMAP decision) looks like a genuine early defect noticed late. But by volume,
memory wins.

## Cost accounting is blind by ~86x

`engineering/value_report.py:212` (`_usage_tokens`) sums `total_tokens`, else
`input_tokens + output_tokens`. Cached prompt tokens are where reviewer CLIs actually
bill. For `stage5b`: **13,986,935 real** vs **161,701 counted**.

Per-round `cache_creation` runs 77k–107k with **zero cross-round reuse** — every round
rebuilds the cache, ~860k tokens of pure waste. `prompt.md` opens with `run_id:`, which
changes every round, so prefix caching breaks at the first token. Reordering alone will
not recover it: measured round-start gaps are 5–8 minutes, straddling the default
5-minute cache TTL. **The way to spend fewer tokens is to make each round's input smaller,
which requires memory** — not to make a large identical prompt cache better.

## Structure of the fix: three cuts, brake first

Twelve changes fall out of the diagnosis. Shipping them as one change would reproduce
exactly the pathology under study, so they are three, ordered by dependency and by
strength of evidence.

**Cut 1 — the brake.** Touches no finding semantics. Zero external dependencies. The only
cut that bounds `stage5b` and `stage1`, and the only one whose benefit is *exactly*
computable by replay.

**Cut 2 — the ledger.** Makes plan findings first-class objects.

**Cut 3 — the exit condition.** Severity threshold plus calibration. Must follow Cut 2:
the objection this behaviour was defending against — "a plan finding passed with it open
vanishes silently" — is dissolved by Cut 2's ledger, not by argument.

Cut 1 precedes Cut 2 deliberately. Cut 2 attacks the root cause, but **its benefit cannot
be validated by replay** — the recorded corpus can show which findings a ledger would have
suppressed as already-disposed, but not what a reviewer with memory would have refrained
from causing. Cut 1's benefit is a number we already have.

### Combined effect, replayed

Authorization prompts across all eight changes, threshold and budget together:

| budget | budget alone | threshold + budget | concentrated in |
| --- | --- | --- | --- |
| 2 | 53 | 29 | all 8 changes |
| 4 | 38 | 16 | 3 changes |
| **6** | 26 | **11** | **only `stage5b` (4) and `stage1` (7)** |
| 8 | 18 | 7 | only `stage5b` (2) and `stage1` (5) |

The budget alone is an alarm-fatigue machine: at 2 it interrupts a change that converged in
three rounds. Paired with the threshold at 6, the six healthy changes are **never
interrupted** and every prompt lands on one of the two that deserved intervention. The
default is therefore 6 for `plan-reviewer`. The `code-reviewer` default is settled
separately, in Cut 1 item 2 below, because per-change counting is not behaviour-neutral
there — a conclusion drawn from that role's own round counts rather than from this table,
which measures the plan path.

## Cut 1 — the brake

**1. The budget counts per `(change, reviewer)`, not per epoch.** Epochs remain for
contract freezing and retry anchoring; only the budget stops counting them. Anchoring the
epoch elsewhere was tested and rejected: `stage5b` has 12 `plan_ready` and **11
`plan_redeclared`**, so re-anchoring on `plan_redeclared` yields 11 epochs instead of 12.
In practice `plan redeclare` is part of the normal loop, not a rare restart signal.

No reset channel is provided. A change that genuinely needs to start over should be closed
and reopened, not have its counter washed inside the same change.

**2. `max_automatic_rounds_per_epoch` is renamed to `max_automatic_rounds`, and the old key
is a hard error.** No deprecation period, no silent acceptance. The value's meaning changes
(per-epoch → per-change accumulation), and a config file that reads the same while its `2`
means something entirely different is the same failure as `blocking_severity` being
silently inert on the plan path. Better to make someone edit one line.

**This rename is not behaviour-neutral for `code-reviewer`, and an earlier draft of this
document claimed it was.** The claim rested on there being no edge back to
`implementation_complete`. There are four: `plan_redeclared` and `intent_redeclared` reset
to `INTENT_DECLARED`, `implementation_restarted` returns to `PLAN_APPROVED`, and
`implementation_invalidated` returns to `IMPLEMENTATION_IN_PROGRESS`
(`core/transitions.py`). This document's own mechanism-4 paragraph already noted
`implementation_complete` firing twice on three of the ten changes that reached code review
— the code-path population, not the plan-curve one — which contradicts the claim it
appeared beside.

So per-epoch and per-change counting diverge on the code path too — on the changes that
redeclared **after** `implementation_complete`, a strict subset of those that redeclared at
all. In the corpus six changes carry `plan_redeclared` (`corpus-01`, `04`, `07`, `08`, `10`,
`11`) and three have a second code epoch (`04`, `07`, `08`). On `corpus-01` and `corpus-10`
every redeclare precedes the first `implementation_complete`, so the counters coincide;
`corpus-11` never reached code review at all, so they coincide vacuously.
Since no reset channel is provided, a change that redeclares after code review has begun
re-enters it carrying the rounds it already spent.

**The code default therefore moves from 2 to 4.** Not to soften the brake, but because
under per-epoch counting a restart already washed the counter, so the effective bound was
never "2 per change" and keeping the number at 2 would silently tighten the code path while
the commit message said "rename". Three populations appear in this document, and each figure states which one it is over:
the corpus holds **11** changes, **10** of them reached code review, and the plan-curve
table above lists **8** — the changes examined when that table was written. Nine corpus
changes have imported plan rounds; `corpus-09` (curve `1, 0`) is the ninth and is not
tabulated, so "the tabulated 8" is a named set, not a set derived from a rule. Over the 10 that reached code
review, rounds per change run 1, 1, 1, 2, 2, 3, 3, 4, 4, 4: at a budget of 2 the brake
fires 8 times across 5 of those 10, at 3 it fires 3 times, and at 4 it never fires.

Four is **not** chosen by the standard that chose 6, and the resemblance should not be
claimed. Six satisfies a two-part criterion — healthy changes never interrupted *and* every
prompt landing on one of the two pathological changes — and only does so paired with Cut 3's
severity threshold. Four satisfies the first half alone: it is the maximum observed value,
with zero margin, and it fires nowhere in the corpus.

That the corpus contains no pathological code change is itself the finding: code review
converges in 1–4 rounds everywhere in it, because its claims are falsifiable by an
executor. So the code budget is a ceiling against a failure mode not yet observed, set
where the observed data ends. If a divergent code review ever appears, that is the number
to revisit, on its evidence.

**3. Exceeding the budget requires authorization per round, unchanged.** Authorized rounds
still count as automatic (`cli/review.py:1074`), so each further round is a fresh decision
against a curve that has grown. With the threshold in place that totals 11 prompts across
the corpus; batching would turn them into a rubber stamp.

**4. The block carries the evidence, and so does the authorization prompt.** Five items:

- which round this is, counted per change
- the per-round `blocker+major` curve and the total-findings curve
- cumulative tokens including cache
- **which review source has been failing, and for how many consecutive rounds**
- whether the last round improved on the one before

The **block** is the load-bearing surface, not the authorization prompt. The human does not
read the CLI's stderr; the human reads what the agent says. The agent is the messenger, and
a messenger without the numbers can only say "I was blocked, please approve" — which is the
rubber-stamp path. By the time someone types `review authorize`, the decision is already
made.

No automatic divergence verdict. A rule of the form "three rounds without improvement" was
tested against this corpus and both `stage5b` and `stage1` defeat it at rounds 4-6
(locally decreasing, globally flat). A human reading `3,2,3,2,2,1,2,3,1,1` needs no rule.

**5. Hitting the budget emits an event.** Advisory instructions to the agent — stop, relay
this verbatim, do not retry — are necessary but not sufficient; this repo's own research
concluded that specifications read into context and not followed is the actual widespread
failure. So the block is recorded in `events.jsonl`, and `report` and the merge disclosure
surface "this change hit the round budget N times" whether or not the agent relayed it.
`gate-blocks.jsonl` is deliberately not reused: its schema serves pre-tool-use blocks and
it lives outside the event stream to protect that fail-open hot path, neither of which
applies inside `review begin`, which already emits events.

**6. `_usage_tokens` counts `cache_read_input_tokens` and `cache_creation_input_tokens`.**
Rough magnitude is the goal: the harness does not price tokens and does not estimate USD.
It does stop discarding a cost the producer states about itself — `claude --print` returns
`total_cost_usd` at the top level and we throw it away — which is the same class of repair
as item 8, writing down what is already in hand, and not an estimate. Item 4 depends on the
token count being right.

**7. A missing source is reported at every round close, not only at the wall.** The data is
already in the `review_round_closed` payload and nothing reads it. Close ordering
(`cli/review.py:1463-1475`) checks rejection **before** missing sources, so a round where
one reviewer rejected and the other died closes as a clean `rejected` and the death is
never mentioned. That ordering is correct — a rejection is a rejection regardless of quorum
— so only the reporting changes. `stage5b`'s codex source was dead from round 1 (quota
locked until 2026-08-28) and eleven consecutive single-source rounds passed unremarked.
This information is worth most at round 1 and decays to nothing by round 7.

**8. `adapters/reviewer/claude_cli.py` captures `session_id`.** Pure recording of a field
the CLI already returns and we currently discard — which is why all ten `stage5b` claude
receipts read `session_id: null`. Same class as item 6: write down what is already in hand.

**9. A redacted replay corpus enters the repo as a fixture.** Exported from `pantheon`:
event types, reviewer roles, per-finding severity, round/epoch structure, usage numbers,
timestamps. Stripped: finding summaries and file paths, plan document paths, commit hashes.
`change_id` becomes `corpus-01`…`corpus-NN` in first-appearance order (11 as exported), finding ids become `c01/f-07`.

Hand-written fixtures are not an option here — validating our assumptions against curves
we invented is circular, and the specific shape of `stage5b` and `stage1` (a stable 1–3
majors per round, indefinitely) is not something we would have thought to fabricate. The
corpus is also a durable regression asset: any future change to review policy can be
replayed against the same eight curves.

## Cut 2 — the ledger

**1. `derive_open_findings` harvests plan-review results**, keyed per reviewer role so the
two ledgers never cross-contaminate.

**2. Plan rounds freeze real `open_finding_ids`**, the prompt carries the `Open prior
findings` section, and `check_disposed` enforces per-id disposal at import — the mechanism
`code-reviewer` has run under for two releases.

**3. The ledger starts at a protocol version stamp.** `review_round_started` records
`finding_ledger_version: 1`, and `review_result_imported` copies it forward at import so
`derive_open_findings` stays a single-pass fold. Only stamped rounds are harvested.

Without this the upgrade wedges in-flight work: `pantheon` holds **176 historical plan
findings** and `stage5b` is still in flight with 37 of them, so the first post-upgrade
import would demand disposal of ids the reviewer was never shown — and the historical
`prior_findings` are bare ids that cannot match the namespaced ones anyway. This mirrors
the exclusion already documented in `derive_open_findings`, which excludes the legacy
`review_verdict_recorded` flow for the same reason. A version number rather than a boolean,
so the next change of ledger semantics does not need a second stamp.

**4. An open plan finding is recorded, reported, and disclosed at merge — never blocking.**
`engineering/value_report.py`'s `_is_code_verdict` gates the report as well, so plan open
findings need their own count, separate from code's. The merge attestation lists them the
way an undisclosed bypass is listed: visible at the moment of merge, not an obstacle.

The original objection was that a passed-with-open plan finding vanishes silently. The
answer to *vanishes* is *make it visible*, not *make it block* — blocking to guarantee
visibility is precisely the reasoning that produced twelve rounds.

**5. `prompt.md` puts invariants first** — instructions, checklist, schema — and the
per-round binding (`run_id`, `target_head`, digests) last. This cut already rewrites the
prompt, so the reordering rides along.

Note that prompt text feeds `contract_digest` (`review_contract.py:344-390`), so every cut
here invalidates any in-flight frozen contract: a round in progress at upgrade time cannot
be completed and a new one must be started.

## Cut 3 — the exit condition

**1. `verdict_blocks` honours `blocking_severity` on the plan path** and `pass_with_open`
stops being hardcoded to `code-reviewer`.

**2. Severity tiers and checklist items ship with definitions.** `core/review_checklist.py`
currently ships three bare strings; `design-soundness` — the item that failed 10 of 10
rounds on `stage5b` while the other two passed — is defined nowhere, so each round's
reviewer reinvents it. If severity is going to gate, severity has to mean something:

- **blocker** — following this plan produces something wrong
- **major** — an implementer gets stuck, or two implementers build different things
- **minor** — wording, ordering, readability; changes nobody's actions

Defaults ship with the built-in checklist and adopters may override them; shipping
undefined terms in our own default is the bug. This makes the gate *predictable*, not
looser — spot-checking `stage5b`'s late findings against these tiers leaves them at
`major`, which is why it is Cut 3 and not the fix.

## Deferred, with a decision rule

**Session continuity (reviewer resumes its previous round's conversation).** Both shipped
CLIs support it natively — `claude -r/--resume`, `-c/--continue`, `--fork-session` (help
text confirms resume works under `--print`, the mode we use); `codex exec resume <id> |
--last`, already parsed at `adapters/reviewer/codex_cli.py:125`.

It would give the reviewer real memory and shrink each round's input to the delta, and it
resolves the soundness objection to incremental plan review — editing §5 can break §9 and
§9 is not in the diff, but it is in the session.

It costs *offline recomputability*: the real model input would include conversation state
the harness does not own. The verdict stays bound to `target_head`, `contract_digest` and
`bundle_digest`, and every prompt and verdict is retained, so the chain stays
reconstructible; recording the session-id chain and requiring a resumed id to equal the
previous round's recovers most of the rest. Bounded, but real.

**Not scheduled now, because after Cut 2 the corpus answers it directly**: if the curves
start decaying, the missing memory was only "which conclusions are already accepted" and
the ledger sufficed. If they stay flat, the missing memory is "what my own proposal
touches", which only a session provides — and we will know which finding classes recur,
which is what Cut B's design needs.

**Machine-checkable claims should not reach an LLM reviewer.** Eight of `stage5b`'s
findings were "this type is never defined" / "these two signatures disagree" — `tsc`
reports those in seconds. A way to declare "this class of assertion is covered by
verification X, do not re-litigate it" is the deepest fix available. Excluded because it
needs new contract vocabulary and, unlike everything above, **cannot be validated by
replaying the recorded verdicts**: it rests on a behavioural assumption whose failure mode
(under-reporting a real defect) is silent.

## Verification

Cut 1's every claim is checkable by replay against the redacted corpus: which rounds a
threshold retires, at which round the repaired budget fires, how many authorizations result,
and when a source went dark. That corpus is the acceptance evidence, not a hand-written
fixture.
