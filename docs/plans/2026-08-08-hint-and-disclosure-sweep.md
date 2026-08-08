---
change: 2026-08-08-hint-and-disclosure-sweep
stage: plan
scope:
  files:
    - docs/plans/2026-08-08-hint-and-disclosure-sweep.md
    - docs/concepts.md
    - docs/getting-started.md
    - src/super_harness/cli/attest.py
    - src/super_harness/cli/review.py
    - src/super_harness/core/review_checklist.py
    - tests/unit/cli/test_attest.py
    - tests/unit/cli/test_review.py
    - tests/unit/cli/test_review_runs.py
    - tests/unit/core/test_review_checklist.py
tier_hint: Micro
---

# Four places the harness knows the answer and does not say it

**Goal:** Close the four disclosed-open debts left by the last three merged changes
(#98, #101, #107), each of which is a case of the harness holding information and
failing to act on it: a block that names a route the caller cannot walk, a count that
is computed and thrown away, a config validator with a hole next to its other guards,
and a comparison whose semantics nothing pins.

**Architecture:** No new machinery, no new state, no config migration, no new files.
Four localised edits plus the tests and prose that pin them. Each is independently
revertible; nothing here is sequenced against anything else here.

**Tech Stack:** Python 3.10+, pytest. No new dependencies.

## Issues, and what each one turns out to be

### A. A refusal names a route, not a state — GitHub #94

`_validate_reviewer_state_or_exit` (`cli/review.py:186`) is the shared front door of
`review begin`, `authorize`, `skip`, `confirm` and `approve`. When the caller is in the
wrong state it says:

```
plan-reviewer cannot record a verdict from state 'PLAN_REJECTED'
  Hint: Expected state: AWAITING_PLAN_REVIEW.
```

That names the destination and not the road. The observed failure is exactly that gap:
the round-budget block told the human to run `review authorize`, the human ran it after
the rejection had landed, and got the message above with nothing to do about it.

**The issue's own proposed location is wrong, and the correction is load-bearing.**
It asks for the hint at the budget block inside `review begin` to be made state-aware.
That block is unreachable except from `AWAITING_PLAN_REVIEW`: `_REVIEWER_STATES`
(`cli/review.py:180`) admits `plan-reviewer` from that one state, and the state check
runs before the budget check in the same command. A state-aware hint there could only
ever print the state it already assumes. The message the human actually hit comes from
the shared guard, in a *later* command — so that is where the fix belongs, which also
satisfies the issue's closing request to check every other hint naming a state-gated
command, because they all pass through this one function.

**Design.** The guard gains a small mapping from `(reviewer, current_state)` to the
command that reaches the expected state from *there*, derived from `core/transitions.py`:

| reviewer | current state | route named |
| --- | --- | --- |
| `plan-reviewer` | `PLAN_REJECTED` | `plan ready … --scope @<path>` (`PLAN_REJECTED --plan_ready-> AWAITING_PLAN_REVIEW`) |
| `plan-reviewer` | `INTENT_DECLARED` | `plan ready … --scope @<path>` |
| `code-reviewer` | `IMPLEMENTATION_IN_PROGRESS` | `done` |
| `code-reviewer` | `PLAN_APPROVED` | `implementation start` |

`--scope` is part of the two `plan ready` rows, not decoration. `cli/plan.py:207` sends
an empty artifact list when the flag is omitted and `core/reducer.py:149` **always
replaces** rather than merges, so a bare `plan ready` revokes the HG-PLAN-AUTHORING
carve-out and leaves the caller unable to edit their own plan document after the next
rejection. A hint that unsticks someone by silently taking a permission away is this
issue's own defect wearing a different hat, so the flag is named even though the guard
cannot know the value.

The placeholder is `@<path>` and not `<files>` because `--scope` parses its argument as
YAML and rejects anything that is not a list (`cli/plan.py:105`), so a caller who reads
`<files>` as "a filename" types `--scope docs/a.md` and gets exit 2. A change whose whole
thesis is that a refusal must name a route the caller can walk cannot leave its own hint
one step short of walkable — the placeholder has to be the form that actually works.

Every row names exactly one command — the *first* step, never a route. `PLAN_APPROVED`
is two transitions away from `AWAITING_CODE_REVIEW` and still names only
`implementation start`, because a hint that spells out a multi-command route rots against
the state machine and one correct step is all the caller needs to stop being stuck. Any
state not in the table keeps today's `Expected state: …` wording.

**Constraint the implementation must respect:** the route is a hint, not a claim about
reachability. `_validate_reviewer_state_or_exit` sees `current_state` and the reviewer,
and deliberately nothing else — it must not start loading governance, packets or events
to decide what to say, because it is the cheapest guard in the module and every caller
runs it before anything is loaded.

### B. A checklist id can carry a newline into the frozen prompt — GitHub #100

`_render_checklist` interpolates each id into the prompt as `  - {item}`. An id
containing a newline emits free-standing lines into the region the reviewer reads as
harness-authored instruction. The verdict schema's `enum` still holds the multi-line id,
so the round does not fail closed on it.

**Design.** Reject the id at `resolve_checklist`, beside the existing non-string and
empty-list guards, rather than escaping at render time — escaping silently accepts a
malformed config, and every other branch of that function fails loud on one.

The test is `str.isprintable()`, not a hand-written control-character set. A checklist id
is a slug that goes into a prompt line, a JSON schema `enum` and a digest; anything
non-printable in it — C0, C1, a line separator, a stray zero-width character — is a
config error rather than an id, and `isprintable()` names that class exactly (ASCII space
stays printable, so multi-word ids keep working).

`isprintable()` alone is not the whole guard: `"".isprintable()` is `True`, so an empty
or whitespace-only id would pass it and still reach the prompt as a bare `  - ` bullet
and the verdict schema as an empty `enum` value. That is the degenerate case sitting
right beside the one this guard closes, and it is rejected on the same footing — a
present-but-blank id is the same kind of mistake as a present-but-empty list, which this
function already refuses.

**Scope note.** This is a widened pre-existing exposure, not a new one, and the
repository's threat model already holds that a solo owner can forge anything in-repo.
The value is failing loud on a config mistake, and that is the claim the change should be
judged against — not a security claim it cannot support.

### C. The merge gate computes the round-budget count and discards it — GitHub #96

`derive_independence` returns `review_budget_rounds_held`; `cli/attest.py:181-189`
spreads only the nested `["code_review"]` sub-dict into each `independence` item, so the
count reaches neither the human line nor the `--json` envelope. The change that
introduced the counter argued in its own plan that shipping only the `report` half drops
the half an agent cannot wash — and then shipped exactly that.

`docs/getting-started.md:373` already tells the reader that `report` **and the merge
attestation** both surface it. The doc is currently false; this makes it true.

**Design.** The count is **role-agnostic** and the independence disclosure is not, so
they must not share a line. `derive_independence` counts every `review_budget_exceeded`
event whatever reviewer raised it (`attestation.py:359`), and `report` already renders it
as its own standalone bullet (`cli/report.py:125`). Attaching it to `_independence_line`,
whose classification is scoped to code review by design §4.1, would print
`review independence: independent — alice (1 round held)` for a change that was held only
at *plan* review — a true number reading as a claim about a different reviewer.

So: a second per-slug list in the verify envelope, `budget_holds`, each entry
`{slug, rounds_held}` and present only when the count is non-zero; and a separate human
line that borrows `report`'s vocabulary verbatim rather than inventing a second phrasing
for the same number:

```
round budget: held 2 automatic round(s) for a human funding decision
```

**One line per holding attestation, never a sum.** `attest verify` covers a whole
base..head range and `verdict.attestations` can hold several slugs, so this line sits in
the same per-slug loop as `_independence_line` and the `gate bypass:` line and behaves
like them. Summing across slugs would present two changes held three times each as one
change held six times, and would also contradict the per-slug `budget_holds` entries in
the envelope printed from the same data.

Deliberately *not* lifting the whole `derive_independence` dict, which the issue offers
as the alternative: that would nest the existing keys under `code_review` and add
`author`, changing the shape of a published `--json` envelope for a rendering fix. And
deliberately not adding the key to the `independence` item either, for the same reason
it does not go on the independence line — every other key on that item is code-review
scoped, and a role-agnostic sibling among them is the same confusion moved into JSON.

**Out of scope, decided rather than inherited.** The same issue raises that a
`plan_approved` carrying `skipped: true, override: true` is written to the attestation
but is invisible to `attest verify`, because `derive_independence` is scoped to
`code_review` by design §4.1. Keep that scope. Making plan-path disclosure first-class
means deciding what a plan-review verdict *is* in the ledger, which is GitHub #102's
subject; doing it here would be a semantic change smuggled into a rendering cut. This
paragraph exists so the next reader finds a decision instead of an oversight.

### D. Nothing pins the qualifier comparison's semantics — GitHub #106

`_model_contradicts` compares bracketed qualifiers with `req_quals <= act_quals` on a
`frozenset`. If that `frozenset` is ever "simplified" to a list, `<=` silently changes
from subset to lexicographic ordering, raises nothing, and every existing matrix row
stays green.

**The row the issue proposes does not bite.** It suggests requested `opus[1m][beta]`
against reported `claude-opus-5[beta][1m]`. Under a list, `["[1m]", "[beta]"] <=
["[beta]", "[1m]"]` compares `"[1m]"` against `"[beta]"`, and `'1' < 'b'`, so it is
`True` — the same verdict as the set gives. The row passes under both semantics and pins
nothing.

**Design.** Pin it with a pair where the two semantics disagree: requested `opus[beta]`
against reported `claude-opus-5[1m][beta]`. Subset is `True`, so the correct verdict is
"no contradiction" — a report that carries the requested qualifier plus one more is the
more-specific-report case. Under a list, `["[beta]"] <= ["[1m]", "[beta]"]` compares
`"[beta]"` against `"[1m]"` and is `False`, flipping the verdict to "contradiction" and
failing the row. That is the property worth owning: the test fails if the container type
changes.

Verified by swapping the `frozenset` for a list and running the matrix. Doing so also
corrects the issue on a second point: it claims every existing row stays green, and the
pre-existing `opus[1m]` / `claude-opus-5[200k]` row fails too, so the hazard was already
half-covered by accident. The new row is the one that covers it on purpose, and it
covers the subset direction — requested qualifiers being a strict subset of reported —
that the `[200k]` row does not reach.

**Second half of the same issue: an id that reduces to an empty base.**
`_model_contradicts("opus", "[1m]")` returns `False`, because stripping qualifiers leaves
`act_base == ""` and `"" in "opus"` holds.

Decide it is correct and pin it, rather than add a guard. The function's stated doctrine
is that only an explicit *contradiction* invalidates a receipt, and an identifier with no
model identity in it is contentless, not contradictory — which is why the two
already-pinned empty-string rows return `False` too. Making empty-after-stripping block
would put the doctrine at odds with itself for a degenerate input no known producer emits.
So: a matrix row and one sentence in the docstring, no behaviour change. This closes #106
rather than leaving half of it open.

## Acceptance criteria

1. All four route-table rows are pinned, each naming exactly one command: `plan-reviewer`
   from `PLAN_REJECTED` and from `INTENT_DECLARED` names `plan ready` carrying
   **`--scope @<path>` exactly** — not `<files>` or any other placeholder, because the
   flag parses YAML and exits 2 on a bare filename, so the pinned form must be one the
   caller can type as-is; `code-reviewer` from `IMPLEMENTATION_IN_PROGRESS` names `done`
   and from `PLAN_APPROVED` names `implementation start` and nothing after it. A state
   with no mapped route still prints today's `Expected state: …` line, and the guard
   still loads nothing.
2. A checklist id containing a newline (or any other non-printable character), and an id
   that is empty or only whitespace, each make `resolve_checklist` raise
   `ReviewChecklistError` naming the reviewer, on the same footing as the existing
   empty-list and non-string errors. Ordinary slug ids and multi-word ids with ASCII
   spaces are unaffected.
3. `attest verify` prints one `round budget: held N automatic round(s) …` line, in
   `report`'s wording, **per attestation that hit the budget** — never a sum across
   slugs — and `--json` carries a matching per-slug `budget_holds` list beside
   `independence`. A change that never hit the budget prints exactly what it prints
   today and contributes no `budget_holds` entry — a `0` must not become a new always-on
   line. The `independence` item and `_independence_line` keep their current shape and
   wording exactly, so nothing role-agnostic lands on a code-review-scoped disclosure.
4. The model-contradiction matrix contains a row that fails if `_model_qualifiers`
   returns a list instead of a `frozenset`, and a row pinning that an id reducing to an
   empty base does not block.
5. `docs/getting-started.md` and `docs/concepts.md` describe the checklist-id constraint
   as the guard actually enforces it — **non-blank**, printable, single-line — because
   `""` is printable and single-line, so prose that stops at "printable single-line text"
   tells a reader a blank id is legal. And the getting-started sentence about the merge
   attestation surfacing the budget count is true.
6. `ruff`, `mypy`, `pytest`, `decision check` and `doc check` are clean.

## Explicit non-goals

- Not touching the round budget's *behaviour* — #95 (whether human-confirmed rounds
  consume it) is a separate decision, not a rendering fix, and answering it here would
  change what criterion 3 is even counting.
- Not harvesting plan findings into the ledger (#102), not adding a consequence gate to
  code review (#99), not changing what a refused receipt records (#103), not adding cost
  to `status` (#104). Each is its own cut with its own measurement.
- No new files, so no new package `__init__.py`, so no scope surprise at the merge gate.
