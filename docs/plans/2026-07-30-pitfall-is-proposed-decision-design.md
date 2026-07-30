---
change: 2026-07-30-pitfall-is-proposed-decision
stage: design
---

# Negative knowledge lives as a `proposed` decision

**Goal:** Ratify the norm that "pitfall"-shaped knowledge (don't do this / here be
dragons) is recorded as a `proposed` decision record, and that super-harness will
not grow a parallel negative-knowledge corpus.

## Why this cut exists

Self-hosting has accumulated a large body of "don't do this" knowledge that lives
entirely outside the product (cross-session agent memory, gitignored `private/`
files). Adopters get none of it, and — more importantly — the project has no
stated home for the next such item, so each one lands wherever the author happens
to be standing.

The question "should we build a pitfall registry?" was researched before designing.
The answer is no, and the reasoning is load-bearing for this decision's body, so it
is recorded here rather than lost in a chat log.

## What the research says

Three forms of negative-knowledge record survive in the wild; each has a hard
entry requirement, and the form that lacks one has a documented failure record.

| Form | Content template | Entry requirement |
| --- | --- | --- |
| lint rule (Clippy / Semgrep / ESLint) | What it does / Why is this bad? / Example / **Known problems** | **an executable detector** |
| "Don't Do This" (PostgreSQL wiki, 17 items) | **Why not?** / **When should you?** | subject is a **stable public API** |
| AntiPattern (Brown et al. 1998, 40 items) | Name / **Refactored Solution Name** / symptoms / causes | **must name the replacement** |
| ~~lessons-learned repository~~ | free-form, numbered, tagged | **none** — the failed form |

- Weber & Aha, *Intelligent lessons learned systems*: such systems "are usually
  ineffective because they invariably introduce new processes when, instead, they
  should be embedded into the processes that they are meant to improve." Their
  remedy is an *active lessons delivery architecture* — push at the point of work,
  not a repository to be searched. Their open-problems list names *obsoletism*
  explicitly. Their demonstration system, HICAP, is a **plan-authoring** decision
  support tool — the same moment `plan ready` occupies.
- NASA LLIS (OIG audit IG-12-012) is the large-scale instance of the failed form:
  outside JPL, no center contributed consistently 2005–2010; NASA's own Chief
  Knowledge Architect could not find answers in it; the Chief Engineer conceded
  that efforts to drive adoption produced "little measurable improvement."
- PostgreSQL's "Don't Do This" page carries **no version markers and no
  obsolescence indicators**. It survives because its subject (the SQL surface)
  does not move and its readership is large enough to self-correct. Neither
  condition holds for a single project's internal notes, so it cannot be cited as
  evidence that a hand-maintained pitfall list survives.

## Why the existing `Decision` record is already the right vessel

`core/decisions.py` already carries the ingredients the surviving forms require,
and two the surviving forms lack:

| Requirement from the field | Field in `Decision` |
| --- | --- |
| executable detector + honest ceiling | `check` + `counterexample` + **bite-test at `ratify`** |
| names the replacement | body prose + `supersedes` / `superseded_by` |
| "why not" / "when should you" | `review` block (`acceptance`) |
| **obsolescence mechanism** (PostgreSQL lacks) | `proposed → ratified → superseded / retired` |
| **staleness detection** (all three lack) | `reconciled_anchors: {file: sha256}` |

The bite-test is strictly stronger than Clippy's model: Clippy has no admission
test proving a lint actually fires on the pattern it claims to catch.

The decisive fit is `status: proposed`. It is exactly "we got burned but have not
yet settled the rule": verified below to gate nothing, and it has an **exit** —
`ratify` (it became a rule) or `retire` (it stopped being true). A standalone
pitfall file has no exit, which is why such files sit at `draft` forever.

## Verified preconditions

Read before designing; all three hold on `3e9014f`:

- `core/decision_check.py:79` — `if d.status != "ratified" or d.ratified_text_hash is None: continue`.
  A `proposed` decision is skipped, so it **cannot fail `decision check`** and
  cannot block CI.
- `cli/decision.py:354,366` — the `hard:context` tally counts only `status == "ratified"`.
  Proposed records do not distort the tier ratio.
- `cli/decision.py:133` — `ratify` accepts `proposed` and `ratified`. The exit path
  from proposed to ratified is open.

Because the mechanism already works end to end, this cut adds **no lifecycle code**.
What is missing is only that nothing in the product states the norm, so nobody —
human or agent — knows the vessel exists.

## Scope of this cut, and what is deliberately deferred

**In scope (Cut 1):** state the norm where it binds — one ratified decision record,
one line in the generated AGENTS.md section, one paragraph in the narrative docs.

**Also shipped, discovered during execution.** Adding the anchor sentinel made
`d-dangling-check` suspect and forced a reconcile that rewrote its own `.md`, a file
nobody had planned to touch and which `attest verify` matches by set membership. Two
consequences are in scope:

- `d-dangling-check` is re-reconciled (its criterion re-reviewed, not rubber-stamped).
- The trap is filed as a second record, `d-tier2-reconcile-touches-scope` — **left
  `proposed`**, which is the first real use of the vessel this cut establishes. Its body
  carries the decided direction: `plan ready` should warn and name the exact decision
  documents to declare, while having `attest verify` treat a reconcile stamp as implied
  in-scope is **rejected** — telling a stamp-only change from a body change needs a
  semantic frontmatter diff, which adds a laundering vector to a gate whose rule is
  "every changed file is in `scope.files`, no exceptions". That rejected alternative is
  recorded in the decision body, not here, because a plan document is a snapshot and the
  decision record is what survives. Registered in `private/OPEN-ITEMS.md`; retire the
  record when the warning ships.

**Deferred (Cut 2): active delivery.** The research points at a second half: an
`applies_to: [glob]` frontmatter field matched against the declared `scope.files`
at `plan ready`, so a relevant record is pushed at authoring time instead of
waiting to be searched. This is what Cursor ships as `globs:` + `alwaysApply:
false`, and what the AGENTS.md v1.1 proposal calls progressive disclosure.

It is deferred because its necessity rests on an unverified premise: Weber & Aha
and LLIS both concern **humans**, who decline to search. An agent may simply grep
`docs/decisions/` every time, in which case delivery is not a gap and `applies_to`
would be gilding. The observation is cheap — after this cut lands, start a change
without prompting and see whether the agent reads `docs/decisions/` unaided.
Registered in `private/OPEN-ITEMS.md` as DOABLE-NOW-BUT-UNVERIFIED.

Two constraints recorded now so Cut 2 cannot get them wrong later:

- `applies_to` is **not** a substitute for `reconciled_anchors`; they are orthogonal.
  Anchors are reactive (`{exact path: sha256}` — is this record still true?);
  `applies_to` is feed-forward (`[glob]` — is this record relevant to the change I
  just declared?). Both are needed.
- The glob matcher **must** reuse `core/scope_match.py` or `PurePath.match`. Writing
  a third one is how you rediscover that `fnmatch` is not glob: its `*` crosses `/`
  and its `**` carries no recursive meaning.

## Tier: why tier-2, not tier-1

`decision_tier` (`core/decisions.py:103-109`) is a strict ladder — a `check` makes a
record tier-1, and tier-1 records cannot use `reconcile` or `betray`, both of which
require `tier == 2` (`cli/decision.py:194,231`). Arming and anchoring are therefore
mutually exclusive, and this record must choose.

A tier-1 check is available: a denylist for a parallel corpus
(`docs/pitfalls/`, `docs/gotchas/`, …), bite-testable because `counterexample` adds
a file and the check fails when that file exists.

It is nonetheless the wrong choice, because it guards the lesser failure. The two
ways this decision can break:

1. Someone grows a parallel negative-knowledge corpus. **Visible in any PR diff** —
   a new directory of prose is not a subtle change.
2. `core/decision_check.py`'s status filter changes so `proposed` records begin to
   gate. Then "record the pitfall as a proposed decision" becomes advice that
   **breaks CI** — the norm turns actively harmful while still reading as true.

(2) is the failure this whole cut exists to prevent, and it is silent. Only an
anchor catches it. So: tier-2, `review` block, **exactly one anchor — on
`core/decision_check.py`**, at the status filter itself.

**Why one anchor and not more.** Two other files carry preconditions and are
deliberately left un-anchored:

- `core/decisions.py` (the four-state lifecycle and the `decision_tier` ladder) is
  **already anchored by `d-decision-records`**, whose entire subject is that shape.
  A second anchor on the same file would spend reconcile budget without adding a
  signal the first anchor does not already raise.
- `cli/decision.py:133` (`ratify` accepts `proposed`, i.e. the exit path out of
  proposed) churns for unrelated reasons. Anchoring it would spend the reconcile
  budget on noise, which is the tier-2 reconcile tax this project has already paid
  once and learned from.

One anchor, on the one line that makes a `proposed` record free.

**Recorded ceiling.** A general check is impossible, not merely inconvenient:
`counterexample` can only *add* a file, so a check can only ever be
"no file contains/creates X". A behavioural invariant such as "proposed decisions
do not gate" cannot be bite-tested by that mechanism, and an un-bite-tested check
is precisely what `ratify` refuses. This is a real ceiling of the counterexample
design, not a shortcoming of this record.

## Delivery for Cut 1

The norm reaches an agent through the generated AGENTS.md section
(`engineering/agents_md_render.py`), as one line in the existing **Decision
conformance** block. One line, phrased as a constraint.

This is not a reversal of the "no pitfall content in AGENTS.md" position. What is
added is the **address of the vessel** ("negative knowledge goes here"), which is a
constraint and bounded at one line. What is refused is the accumulating *contents*
— the thing that dilutes an always-on file, and the thing the AGENTS.md v1.1
proposal says compaction should drop first when it keeps "constraints and
prohibitions" over "explanatory or motivational prose".

## Explicit non-goals

- No `docs/pitfalls/` or any parallel corpus — that is the LLIS-shaped form.
- No `pitfall` record type, numbering scheme, or `maturity` / `ref_count`
  hand-maintained metadata. Hand-maintained usage counters drift; a prior
  exploration of this shape reached 6-of-7 records at `ref_count: 0`.
- No new CLI verb. `decision new` already creates a `proposed` record.
- No accumulating pitfall section in AGENTS.md.
