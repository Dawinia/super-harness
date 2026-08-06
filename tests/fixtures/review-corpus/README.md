# Review-replay corpus

A redacted event stream from one real repository's `.harness/events.jsonl`, exported once
so that changes to review policy can be measured against recorded behaviour instead of
against curves we invented.

Hand-written fixtures cannot do this job. Validating a policy against numbers we made up is
circular, and the shape that matters here — a change producing one to three `major`
findings per round, indefinitely, without converging — is not something anyone would think
to fabricate.

**This is a regression asset.** Any future change to review policy replays against these
same curves. It is not a snapshot to be regenerated when a test becomes inconvenient: the
source repository is private and cannot be a test dependency, so a re-export is not a
cheap correction.

## Contents

697 events across 11 changes, `corpus-01` … `corpus-11` in first-appearance order.

| corpus id | started automatic plan rounds | imported plan rounds | `blocker+major` per imported round |
| --- | --- | --- | --- |
| `corpus-01` | 14 | 13 | 3, 4, 2, 2, 2, 1, 2, 2, 1, 1, 3, 1, 0 |
| `corpus-02` | 1 | 0 | — |
| `corpus-03` | 1 | 0 | — |
| `corpus-04` | 11 | 6 | 3, 1, 0, 1, 1, 0 |
| `corpus-05` | 4 | 4 | 2, 1, 0, 0 |
| `corpus-06` | 3 | 3 | 3, 2, 0 |
| `corpus-07` | 9 | 9 | 2, 1, 2, 0, 1, 1, 1, 0, 0 |
| `corpus-08` | 19 | 18 | 3, 2, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0 |
| `corpus-09` | 2 | 2 | 1, 0 |
| `corpus-10` | 6 | 6 | 3, 2, 2, 1, 0, 0 |
| `corpus-11` | 12 | 10 | 3, 2, 3, 2, 2, 1, 2, 3, 1, 1 |

Identify changes **by shape, never by name**. The eight curves the design document
tabulates are `corpus-01`, `04`, `05`, `06`, `07`, `08`, `10` and `11`. The pathological
case the design analyses — flat, cut off without converging — is **`corpus-11`**. The
`corpus-02`, `03` and `09` rows are changes the design's table omits; they contribute zero
authorizations at any budget of 2 or more.

`started` and `imported` differ, and the difference is load-bearing rather than noise: a
round can start, cost money, and never produce a result. `corpus-04` started 11 automatic
plan rounds and imported 6 — five rounds that reviewed nothing. Any replay must say which
count it means.

## Redaction contract

**Kept**: `event_id`, `type`, `change_id`, `timestamp`, `actor.type`, `framework`, and from
the payload only `reviewer`, `epoch_id`, `round_id`, `run_id`, `source`, `automatic`,
`outcome`, `missing_sources`, `min_independent`; each verdict's `scope_sufficient`, its
checklist items and statuses, and each finding's `severity` and `id`; each receipt's
`protocol` and every integer field of its `usage`.

**Dropped**: every finding summary and file; every digest (`contract_digest`,
`bundle_digest`, `profile_digest`, `result_digest`, `target_head`); every path; every
`reason` free text; `source_results` and its nested receipts; tool traces.

**Rewritten**: `change_id` → `corpus-NN` in first-appearance order; every actor identifier
→ `corpus@example.invalid`; every finding id → `cNN/f-NN`.

Verified mechanically: the committed file contains no vocabulary from the source
repository's change names, no repository paths, no 40-hex commit ids, and no email address
outside `example.invalid`.

## What this corpus cannot test

Dropping `reason` free text costs exactly one datum. The round-budget evidence surfaces the
reason recorded on the most recent `review_run_failed`, because "the second reviewer is
unavailable" is useless without "why" — and that string is not here. That one behaviour is
pinned by a hand-written fixture instead, and the test says so in its name. **Do not weaken
this redaction to make that test easier.**

Finding summaries are likewise gone, so nothing about finding *content* — recurrence,
similarity, whether two rounds raised the same objection — can be replayed. Severity and id
survive, which is what round-count and disposal policy need.

## Deviations from the plan that authorised this fixture

Both disclosed rather than silently absorbed.

1. **11 changes, not 8.** `docs/plans/2026-08-06-plan-review-round-budget-implementation.md`
   Task 1 writes `corpus-01`…`corpus-08`. The rule it states — every change in the source
   stream, in first-appearance order — yields 11, because three changes carry review
   activity the design's table omits. Truncating to 8 to match a number written in prose
   would be a silent narrowing of the evidence, so the rule won and the count follows.
2. **The plan's published corpus identity is wrong.** It asserts `corpus-06` carries
   `[3,2,3,2,2,1,2,3,1,1]`. Under first-appearance order that curve is `corpus-11`;
   `corpus-06` is a change that converged in three rounds. The mapping in the plan was
   never derivable from anything and no review round could check it without this data.
   Tests here therefore key on the **curve**, never on a hardcoded id.
