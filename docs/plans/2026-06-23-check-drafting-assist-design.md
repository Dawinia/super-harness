# Check-drafting assist + decision-birth prompt — design

- Date: 2026-06-23
- Status: draft (pre-plan); revised after adversarial review round 1
- Slice: post-v0.1 self-host line, successor to `arm-decision-teeth` (PR #45)
- Prior art: `docs/plans/2026-06-08-decision-records-anchors-design.md` (Tool A/B,
  the decision/check machinery this slice sits on top of)

## 1. Why

PR #45 armed 3 of our own ratified decisions with executable checks (teeth went
0 → 3) and drew first blood. The maintenance-friction anecdote it produced
(n=3, single-author — a signal, not data) was sharp and one-directional:

> The costly step was writing a non-false-positive check, NOT the lifecycle. The
> lifecycle ran clean once the CLI arg syntax was right.

A live walkthrough on this repo (2026-06-23, throwaway decision
`d-walkthrough-scratch`, run against real `src/`) reproduced and decomposed that
friction. **Both load-bearing claims below were independently re-confirmed by an
adversarial reviewer who re-ran the walkthrough** (round 1, 2026-06-23). The full
friction map:

| # | Friction (observed live) | Nature | Tool-addressable? |
|---|---|---|---|
| A | `decision new` emits a bare file — no hint that teeth are possible, no block format | discovery + mechanical | yes (guidance) |
| B | A toothless decision ratifies silently as tier-3 — zero nudge | feedforward | yes (birth prompt) |
| C | Arming means copying the ` ```check `/` ```counterexample ` block format from another decision | mechanical (recall format) | yes (guidance shows the format inline) |
| **D** | **Inventing the brittle one-token violation signature** (`^import requests`, not `requests`) | **semantic judgment** | **no** |
| **E** | **After a false positive, knowing how to narrow** (dodge prose / yaml) | **semantic judgment** | no (feedback half-helps) |
| F | Iterating means a markdown round-trip (edit file → `ratify --dry-run` → read → edit) | mechanical (round-trip) | marginal (`--dry-run` already covers one round) |
| **G** | **Knowing the coarse-grained ceiling** (`^import` misses `as` / `from`) | **semantic judgment** | no |

Two hard conclusions from the walkthrough (both reviewer-confirmed):

1. **The iteration loop is already instrumented.** `bite_test` feedback already
   names the offending `file:line:content` on a false positive and distinguishes
   "check fails on current code" from "did not bite". A `check try` command would
   only save the markdown round-trip (F) — convenience, not capability.
2. **The biggest unaddressed gap is at birth.** `decision new` is completely
   silent about arming — no format, no nudge, no recipe pointer. The tier-3
   dry-run path says only `no check block (tier-3 context) - nothing to
   bite-test` — informational, no pointer.

The semantic core (D / E / G) is the bulk of the cost and is exactly the layer
super-harness's positioning says must NOT be mechanized — it belongs to the
agent / human. So the value is not "generate a check"; it is "transfer the craft
of writing a check that bites, and stop decisions from landing toothless by
accident."

## 2. Scope

Two pieces, both living on the `decision` CLI surface + AGENTS.md guidance.
**Zero PreToolUse / hook surface. No new CLI command. No exit-code change.**

1. **Check-drafting assist = a guidance recipe** in AGENTS.md (extends the
   existing `### Decision conformance` section), carrying the D/E/G craft and the
   block format inline.
2. **Decision-birth prompt** = two-directional CLI advisories at `decision new`
   and `decision ratify --dry-run` that point a toothless decision at the recipe
   — leading with "context-only is a valid outcome", so they never pressure
   everything to be armed.

### DROPPED after review round 1 (a finding, not an omission)

- **`decision scaffold <id>`** (was the third piece). Adversarial review, grounded
  in re-running the CLI, concluded it does not clear the anti-gilding bar:
  - The block format is ~6 lines, already copy-pasteable from any armed decision,
    and the recipe (§3.1) shows it inline anyway — so friction C is eaten by the
    recipe *without* a command.
  - A scaffold must emit a concrete placeholder, which bakes in repo-specific
    craft (`src/...` path, grep/POSIX flavor). For a non-Python consumer that is
    authoritative-looking but wrong boilerplate they must delete — negative value
    for an agent-agnostic tool.
  - Residual value after the recipe ships = "saves a copy-paste into the file you
    are already editing" — the textbook gilding §5 pre-authorized dropping.
  This is recorded in `private/CAPABILITY-CONVERGENCE-LEDGER.md` as the slice's
  honest finding: the tool-addressable slivers (A/C) are closed by guidance; a
  scaffold command is gilding because the real cost is the irreducible semantic
  core (D/E/G).

### Explicitly OUT (recorded non-goals, unchanged from R0)

- **Edit-time PreToolUse reminder** (the "edit-time reconcile" feedforward).
  CC-only, fail-open, touches the hot-path hook surface — higher risk, and the
  friction evidence does not point here. Stays deferred.
- **`decision check try`** (candidate iterator without a file write). The loop is
  already covered by `ratify --dry-run` + good bite feedback; gilding.
- **`decision new --with-check` sugar.** Moot now that scaffold is dropped.

## 3. Design

### 3.1 Recipe (AGENTS.md guidance)

Extend the existing `### Decision conformance` section in
`engineering/agents_md_render.py` (the SSOT renderer; `init` + `sync` both call
it). Today it covers "treat `decision check` as a local sensor", "don't hand-edit
ratified bodies", and "`ratify --dry-run` to confirm a check bites". Add a compact
"Arming a decision with a check" sub-block that carries the D/E/G craft and shows
the block format inline (the part that closes A/C):

- Pick the **brittle one-token signature** of a violation, not a broad word
  (`^import requests`, not `requests`, which also hits prose / yaml).
- Prefer import/access patterns over bare substrings to dodge prose/yaml false
  positives.
- The check runs through the **host's `/bin/sh` and `grep`** (the runner enforces
  no regex flavor), so prefer portable patterns. (Implementation note: the shipped
  recipe phrases this as "avoid GNU-only `grep` extensions" rather than the
  jargon "POSIX BRE/ERE" — deliberate de-jargon per the project's plain-language
  rule.)
- The check **must exit nonzero on violation**; `! grep ...` inverts grep's exit.
- Know the **denylist ceiling** (`^import` misses `as` / `from` forms) and record
  it in the decision body.
- Write a **minimal counterexample**; run `ratify --dry-run` until it bites.
- **If there is no brittle signature, leave it context-only (tier-3)** — do not
  invent a hollow check.
- Show the literal block shape (so nobody has to copy it from another decision):

  ````markdown
  ```check
  ! grep -rn '<brittle pattern>' <scoped paths>
  ```

  ```counterexample path=<relative/path>
  <one minimal violating line the check above must catch>
  ```
  ````

This is also a hand-maintained mirror in `docs/ARCHITECTURE.md §7` (the
decision-conformance subsystem narrative). If the guidance changes materially,
ARCHITECTURE.md §7 must be edited by hand (it is **not** auto-regenerated). See §6.

### 3.2 Decision-birth prompt (advisories)

A toothless decision is a legitimate, common outcome (judgment-only rationale =
tier-3 is correct). The advisory is therefore **two-directional and leads with the
"valid outcome" statement**, so the default reading is "nothing to do", and arming
is the conditional branch — not an imperative with a command dangling off it.

- **`decision new`** (always lands a `proposed`, never with a check): after the
  existing `created ...` stdout line, print one advisory to **stderr** (stdout
  stays the machine-readable created-path line). At birth the author has typed one
  line of `--text` and usually cannot yet judge armability — so this is a **pure
  pointer to where the craft lives, with NO arming command** (there is nothing to
  `--dry-run` yet; dangling that command here is the nag we are avoiding):

  > Note: most decisions stay context-only — that's the norm. If this one happens
  > to state a brittle mechanical invariant, the "Arming a decision" recipe in
  > AGENTS.md shows how to add an executable check.

- **`decision ratify <id> --dry-run`** already prints
  `no check block (tier-3 context) - nothing to bite-test` **on stdout**
  (`decision.py:160`, no `err=True`). Callers may grep that line, so do NOT
  re-route it — leave it on stdout unchanged and print the pointer as a **separate
  `err=True` line after it**. This is the natural near-miss spot (the author has a
  body now), so the pointer here MAY name the next command:

  > (context-only is a valid outcome; if this states a mechanical invariant, see
  > the "Arming a decision" recipe in AGENTS.md, then re-run `--dry-run` to confirm
  > the check bites.)

  Net: the two advisories deliberately sit on **different streams** — `new`'s on
  stderr, the dry-run pointer on stderr while the pre-existing dry-run line stays
  on stdout. Tests assert each against its actual stream (§6).

- **NOT on committing `decision ratify`** (resolved from review). Re-stating the
  advisory when someone *deliberately* commits a tier-3 ratification is exactly
  the nag §4 forbids. The `new` + `--dry-run` advisories already cover the
  feedforward; the committing path stays silent.

## 4. Central tension: no hollow-check pressure

The convergence ledger's standing rule is "no hollow checks", and several real
decisions (`d-dangling-check`, `d-decision-records`,
`d-fixed-transition-matrix`, `d-identity-resolution-order`,
`d-single-gate-policy`) were honestly left tier-3 because they describe behaviors,
not brittle one-token invariants. The birth prompt and recipe optimize for a
*correct* arm/don't-arm decision, not for maximizing the armed count.

Mitigation (hardened after review):
- Every advisory **leads with** "context-only is a valid, common outcome" and
  makes arming the conditional ("if and only if … brittle mechanical invariant").
- The advisory fires only at feedforward moments (`new`, `--dry-run`), never on a
  deliberate tier-3 ratification.
- The recipe ends with an explicit "if no brittle signature, leave it tier-3"
  rung, so the craft itself teaches when NOT to arm.

Review must check that no advisory string reads as "you should have armed this."

## 5. Honest limits / non-goals

- Guidance cannot write a non-false-positive check — that is D/E/G, which is the
  point. The slice deliberately stops at transferring the craft.
- `decision scaffold` was dropped as gilding (see §2). If a future measurement
  shows the recipe alone does NOT close A/C (e.g. agents keep mis-shaping the
  blocks despite the recipe), revisit a minimal, language-agnostic scaffold then —
  but only on evidence.
- No new gate, no exit-code changes, no PreToolUse surface.
- agent-agnostic: all of this is plain CLI output + committed guidance; nothing
  here is Claude-Code-only.
- **Fail-safe property worth stating:** the existing bite-test at `ratify` already
  catches an unfinished/placeholder check (it won't bite) — so guidance can never
  smuggle a hollow check past the gate. The recipe relies on this, it doesn't
  weaken it.

## 6. Test + self-host plan

- **Birth-prompt advisories** (the only `src/` behavior change): unit tests in
  `tests/unit/cli/test_decision.py` (Click 8.4.1 — `Result` exposes separate
  `.stdout` / `.stderr`; assert **per-stream, never via `r.output`**, which is the
  combined stream and would pass regardless of routing):
  - (a) `decision new` advisory is in `r.stderr`, NOT in `r.stdout`; the
    `created ...` line is in `r.stdout`.
  - (b) both advisories **lead with the "valid outcome" framing** and the `new`
    advisory carries **no arming command** (assert the `--dry-run` string is
    absent from `new`'s output).
  - (c) `ratify --dry-run` on a tier-3 still emits
    `no check block (tier-3 context) - nothing to bite-test` **on stdout** and
    exit code stays 0 (pin the existing contract — callers may grep it); the
    pointer is a separate line in `r.stderr`.
  - (d) the committing `ratify` of a tier-3 does NOT print any advisory (assert
    absent from both streams).
- **Recipe render:** extend `tests/unit/engineering/test_agents_md_render.py`
  (e.g. alongside `test_outer_section_has_decision_conformance`) to assert the new
  "Arming a decision" sub-block strings appear in the rendered section.
- **AGENTS section version stamp is NOT bumped.** The begin-marker stays
  `v{version}` = the package version (`0.1.0`); `run_sync_check` exact-diffs the
  rendered text, so the recipe's **text change alone** drives the drift guard.
  Bumping the stamp is unnecessary and would create a stamp mismatch.
- **Two-file atomic commit (ordering matters):** editing the template in
  `agents_md_render.py` and the regenerated `AGENTS.md` must land in the **same
  commit** — run `super-harness sync` after the template edit and stage both, or
  the self-host `sync --check` gate (`core/sync_check.py` exact-diff) bounces.
- **`docs/cli-reference.md` is NOT in scope** — it is pure click-tree
  introspection (`scripts/gen_cli_reference.py`); no command is added/removed and
  it does not embed the AGENTS section, so it does not change. (Corrected from R0,
  which wrongly listed it.)
- **`docs/ARCHITECTURE.md §7`** IS in scope as a hand edit (the prose mirror of
  this subsystem) if the recipe changes the guidance materially. **Its drift is
  unguarded** — no test or gate catches a stale §7, so the code reviewer must
  eyeball it against the recipe. (§6 does not enforce this; it is an accepted,
  human-checked limitation.)
- **Self-host merge gate** (mirrors PR #45): branch → `change start` →
  `plan ready --scope '[...]'` covering every touched file → `review approve
  --reviewer plan-reviewer` → `implementation start` → TDD → `done` →
  `review approve --reviewer code-reviewer` → `attest write` + commit →
  `attest verify --base main --head HEAD` → push → PR → `on-merge`.
  Touched-file scope (expected): `src/super_harness/cli/decision.py`,
  `src/super_harness/engineering/agents_md_render.py`, `AGENTS.md`,
  `docs/ARCHITECTURE.md`, the relevant test files, this design doc, and the plan.
- **Dogfood the slice on itself:** after the recipe ships, note in the ledger
  whether arming the *next* real decision is visibly cheaper with the recipe in
  hand (the before/after that would justify — or keep buried — a future scaffold).

## 7. Open questions — resolved by review round 1

1. ~~Does `decision scaffold` earn its keep?~~ **No — dropped (§2).** Evidence:
   recipe eats friction C; scaffold ships repo-specific placeholder (negative
   value); residual benefit is a saved copy-paste.
2. ~~`scaffold` on a ratified decision?~~ **Moot (scaffold dropped).** (For the
   record, review confirmed: injecting a check into a ratified body would trip the
   integrity-lock — `compute_body_hash` hashes the whole body including fenced
   blocks — so refuse-with-hint would have been correct anyway.)
3. ~~Birth prompt on committing ratify?~~ **Drop it from committing ratify; fire
   only at `new` + `--dry-run`.**
4. ~~Recipe as AGENTS.md prose vs a skill?~~ **AGENTS.md** — the section exists in
   the SSOT renderer; a skill is heavier and CC-flavored (violates
   agent-agnostic).
5. ~~Counterexample-path placeholder too repo-specific?~~ **Moot (scaffold
   dropped); the concern is precisely why it was dropped.** The recipe shows the
   shape with `<...>` placeholders, not a concrete `src/...` default.
