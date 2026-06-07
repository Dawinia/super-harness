# Design: Decision-conformance governance — what super-harness actually guards

Date: 2026-06-05
Status: converged (brainstorm) — design-time gaps closed at first-cut level (§12):
the serious 3 (upper links / false-positive flood / checkability) + the conceptual
half of #4 (attention routing); #5 subsumed by §12.2–3; #6 (migration) deferred to
build. Still **engineering-incomplete** — first cuts carry residuals and there is no
implementation plan (no TDD / task breakdown) yet. Supersedes the open question parked
in memory `project-next-doc-code-harness`.
Reframes `private/INTENT-VS-BUILT.md` intent ② and the existing `@capability`
anchor machinery (see §8).

> NOTE: like the sibling design docs in this folder, this file deliberately
> carries **NO** `change:` / `stage:` frontmatter. The repo self-hosts on the
> SuperpowersAdapter, which *discovers changes by that frontmatter in
> `docs/plans/`*. With it present, the next `adapter scan-once` would auto-emit
> `intent_declared` / `plan_ready` for this doc. It stays an inert design artifact
> until `change start` is run explicitly. (Same precedent as
> `2026-06-04-review-identity-substrate-design.md`.)

---

## 1. The thesis (why this tool exists)

The world this tool is built for: **the AI does all the work, the human only makes
decisions.** In a spec-driven flow the agent writes the spec draft, the plan, the
code, and the docs; the human's contribution shrinks to *judgment at decision
points*.

You cannot hand that to the AI unsupervised — it forgets, skips, drifts, and will
cheerfully report "done" when it isn't. So something has to make the human's
decisions *actually bind* the AI's work. **That something is this tool.**

> super-harness is the **control force that makes a human's decisions govern the
> AI's work** — the part that ensures the AI cannot silently drift away from what
> the human decided.

This is Böckeler's "harness as a cybernetic governor" (§9) made specific: the thing
it regulates the codebase *toward* is **the set of human-ratified decisions**.

The control force is exactly three obligations:

1. **No skipping** — the agent cannot skip producing/justifying a layer (spec,
   plan, review, doc). The required check must *happen*, or the change is blocked.
2. **Conform to the layer above** — every layer must show it still satisfies the
   one above it (plan ⊨ spec, code ⊨ plan, doc ⊨ code).
3. **Betrayal must surface, never be laundered** — when a downstream change
   contradicts an upstream *human decision*, it is kicked **back up to the human**,
   not quietly auto-patched into consistency.

## 2. The conformance chain (Spec → Plan → Code → Doc)

Every artifact is checked against its parent, so by induction the leaf (code)
conforms to the root (the human-aligned spec):

| Layer | Who authors | Checked against parent | Nature of the check |
|-------|-------------|------------------------|---------------------|
| **Spec** | human-aligned (AI drafts, human ratifies) | — (this *is* the root of truth) | the only human-pinned artifact |
| **Plan** | AI | complete vs Spec **+** fits current architecture | review (inferential) → human ratifies |
| **Code** | AI | implements Plan | review (AI or human, inferential) |
| **Doc**  | distilled from Code + Spec + Plan | matches Code / explains the decision | ground-truth *or* proxy (§5) |

"Doc-code drift", the problem we started from, is just the **last link** of this
chain. The links *above* it (spec↔plan, plan↔code) are the same pattern and the
more valuable ones, because that is where a human decision can be silently
overridden.

**Architecture-fitness** (does the plan fit the current architecture?) is itself one
of Böckeler's three regulation dimensions — it lands naturally as the plan-review
check, not a separate subsystem.

## 3. The unit is a *decision*

The granularity question ("one capability? one file? one function?") is answered by
the thesis: the smallest thing the tool guards is **one decision** — the unit of
human judgment. Not a free-floating "capability", not an arbitrary file.

A decision is a **thread through all four layers**:

```
Spec:  the decision, written down and ratified by a human   ← the thread's anchored end
Plan:  how to implement it (AI, reviewed)
Code:  the function(s)/region(s) that implement it, tagged with the decision
Doc:   the doc that explains it, tagged with the decision
```

The tool's whole job is **keeping each thread intact** and **escalating when a
thread breaks at the human-ratified end.**

Reality check: it is a **many-to-many web**, not clean separate threads — one
decision may be scattered across many code sites, and one code site may carry
several decisions. This is exactly why the no-ground-truth side (§5) can only
*force a re-review* on change, not mechanically decide pass/fail.

## 4. The anchor in code

A code-side tag on a syntactic unit (default: a function/class) declaring **"this
implements decision D"**, e.g. `@implements: D-auth-stateless`.

**Why in the code (not an external map):** the edit-time reminder ("you're touching
D, mind its doc/decision") only fires if the mark is *on the code being edited*.

**The one thing that fixes today's mess:** `D` **must reference a real, ratified
decision in the Spec.** The current `@capability:X` tags float free — `X` is a
label rooted in nothing, which is why all 34 of them dangle (point at no ratified
decision, carry no doc). Rooting the tag in a ratified decision turns a floating
label into an anchored thread.

Scanning the code then yields three signals for free:

- **Dangling up** — tag names `D`, but Spec has no ratified `D` → illegal, block.
- **Dangling down** — Spec has `D`, but no code tags it (or no doc) → unmet, nag.
  *(Today's 34 are these two failure modes.)*
- **Changed** — the tagged code's content differs from the last reconciled
  snapshot → trigger a re-check.

**Boundary knob (decided):** default to tagging a whole **function/class** (clean,
survives rename/refactor, content easy to hash); fall back to explicit
`start`/`end` region markers only for **cross-cutting** decisions that genuinely
scatter (e.g. "all endpoints must authenticate" across twenty files).

## 5. The checker: ground-truth vs proxy

There are not two *kinds* of sensor — there is **one** concept (a link + a checker)
with a single axis: **does the checker have ground truth?** Both checkers do the
same thing — compare current state to a recorded baseline — and differ only in
whether the baseline is *truth*:

- **Ground-truth checker (regen-and-diff).** A deterministic generator exists that
  re-emits the doc from code (CLI `--help` → reference, signatures → API doc). CI
  regenerates and diffs against the committed copy; mismatch → **hard fail**, and it
  can **auto-fix** (re-generate). `cli-reference-drift` is exactly this and already
  works; it just needs to be wired into CI and *required* (today the repo has no
  branch protection, so even it does not block — see §8).
- **Proxy checker (change-since-reconcile).** No generator possible (the decision is
  prose: "why we chose X", a constraint). The baseline is a **snapshot of the
  tagged code at last reconcile**; when that code changes, the doc/decision becomes
  *suspect* — "code moved" does **not** prove the doc is wrong. So it can only
  **force a re-review** ("does this still implement D?"), never auto-decide, and the
  gate is softer (it has false positives by construction).

**Which one a link uses is declared, not detected.** When the link is created the
author either attaches a generator command (→ ground-truth) or does not (→ proxy).
You always use the strongest checker available for that link. Where the tool *knows*
a generator exists for a doc-type (e.g. CLI reference), it can **require** the
ground-truth checker and refuse the lazy proxy.

## 6. What the control force can and cannot do (the honest boundary)

Carried forward from the bedrock truth in
`2026-06-04-review-identity-substrate-design.md` and memory
`project-bedrock-solo-owner-unforgeable`:

**It can deterministically force:** that the checks/reviews *happen*, that no anchor
dangles, and that the mechanically-checkable links (ground-truth) actually match.

**It cannot guarantee:** that the AI's or human's *judgment* is correct — "is this
review honest / is this the right code under the tag" is semantic, inferential.
Two concrete leaks:

1. An agent can dodge a ground-truth checker by declaring a *proxy* link where a
   generator was possible. **Partially closable:** for doc-types the tool knows are
   generatable, force ground-truth.
2. A proxy "reconcile" can be a **fake** — code changed, the agent re-snapshots
   ("I looked") without truly updating the doc, silencing the alarm. This is the
   rubber-stamp / "bless" risk from the prior art (§9). **Not closable
   mechanically** — same class as "code review can be forced to happen, but its
   verdict's honesty cannot."

And the solo-owner ceiling still bounds everything: a determined repo owner controls
the CI and can disable the gate. So the honest positioning is **raise the floor,
make laziness and drift impossible to hide, leave a trail** — decisive against an
agent/human that is *fallible but not adversarial*, only cost-raising against one
that is determined to bypass.

The tags themselves are AI-placed, so the tool verifies their *structure* (the `D`
is real, the code changed) — never their *semantics* (the tag is on the right
code).

## 7. The three mechanism pieces (designed 2026-06-07)

### 7.1 Decision birth & ratification (top of the thread)

A decision is an **ADR-like record** with a stable ID, a one-line decision, a
ratification stamp (who/when), and optional rationale:

    D-auth-stateless
      decision: Authentication must be stateless (JWT, no server sessions)
      status: ratified    ratified-by/at: <identity> / <when>

- **Birth:** when the AI authors/updates a Spec it MUST enumerate its decisions as an
  explicit list — not bury them in prose (a structural requirement the tool enforces).
  The AI *proposes* the list.
- **Ratification:** a human marks each decision; the tool records it as a durable,
  **attributable** fact (reuse the PR #37 reviewer-identity substrate). Only a
  ratified decision is anchorable; anchoring to an unratified/nonexistent `D` is the
  "dangling up" failure (§4) → block.
- **Directing attention** (Böckeler): the AI flags which decisions are
  load-bearing / novel / risky so the human looks hard at those and bulk-approves the
  routine ones — avoids drowning the human into rubber-stamping.
- **What counts as a decision** is judgment, not mechanical: **AI proposes the list,
  the human adds/removes at ratification time.**
- *Honest limit:* the tool forces "decisions are explicitly listed + carry an
  attributable ratification stamp"; it cannot force the human to actually think (bulk
  rubber-stamp is possible) — but it makes each decision visible, attributed, and
  attention-directed.

### 7.2 Betrayal escalation & anti-rubber-stamp (the teeth)

Flow when tagged code changes:
1. **Mechanical trigger:** code under `D` differs from the last reconciled snapshot →
   `D` is *suspect*, merge blocked until re-checked.
2. **Re-check** (inferential): does the changed code still satisfy `D`?
   - Yes → **reconcile**: re-snapshot + record (who, justification, diff).
   - No → **betrayal** → escalate.
3. **Escalate:** hard block; **human-only** resolution — either re-ratify an updated
   `D` (human owns the new decision) or reject the code. The AI cannot self-resolve,
   and must NOT silently edit `D` to match the new code.

Anti-rubber-stamp has **two hardnesses** — state them separately:
- **Mechanically welded:** the decision record itself is change-protected. Editing a
  ratified `D`'s text **auto-invalidates its ratification → blocked until a human
  re-ratifies.** So "AI silently rewrites the decision to launder bad code" is
  *closed* — the tool mechanically detects "D changed without new ratification." The
  top of the thread is hard.
- **Cost-raising only:** the re-check lying "still satisfies D" (a fake reconcile)
  has no ground truth, so it can only be made expensive, not impossible:
  (a) the re-check must produce a **justification referencing D's specific claims**,
  not a bare "LGTM" — fakeable but on the record and reviewable;
  (b) the re-check should be **independent** — a fresh agent / the CI reviewer, not
  the context that just made the change (PR #37 substrate + the P3 default-independent
  reviewer); **tiered** — cheap self-check by default, escalate to an independent
  re-check for load-bearing decisions or large/risky changes;
  (c) everything **attributable + diff-trailed**, so fakes are spottable after.

*Honest limit:* "rewrite the decision" is welded shut; "lie that the code still
conforms" is only cost-raised (a determined AI+owner can fabricate a plausible
justification or edit the CI reviewer). Decisive against fallible-not-adversarial,
cost-raising against determined.

### 7.3 Where the gates wire — two rails

- **Edit-time (PreToolUse hook) = feedforward, soft.** In-loop reminders: "touching
  `D` — mind its doc/decision"; "`D` is suspect / unratified — fix now or CI blocks."
  **May fail-open** (daemon/latency) — fine, CI backstops; only works for hooked
  agents (Claude Code) — a bonus layer, not the floor.
- **CI / merge boundary (cold path) = the hard floor.** Agent-agnostic, covers any
  agent and direct human edits, unbypassable except by the repo owner. Runs: dangling
  anchor (unratified/nonexistent `D`); decision-record integrity lock (§7.2);
  un-reconciled drift; ground-truth regen-and-diff (auto-fix where possible); the
  re-check verdict (self by default, independent for load-bearing/risky); betrayal →
  block → human resolves (CLI/UI, attributed).

Why this split, not arbitrary: (1) the hook fails open, so it cannot be the hard
gate — CI is binding; (2) agent-agnosticism requires the floor to be git+CI (the hook
is Claude-Code-only); (3) feedforward belongs in-loop, hard enforcement at the merge
boundary.

**Honest caveat + concrete first step:** the CI gate is only as hard as branch
protection / required-checks, which a **solo owner controls** — and this repo
currently has **no branch protection at all** (even `cli-reference-drift` is not
required), so the entire CI rail is presently advisory. The first build step is to
**enable branch protection and mark these checks required**; otherwise "hard gate" is
just words. (Against the owner it remains cost-raising, never unforgeable — the
bedrock ceiling.)

## 8. How this reframes the existing build

- **`@capability` anchors** (`core/anchor_scanner.py`, the 34 sentinels) → become
  **decision anchors**, valid only when rooted in a ratified Spec decision. The 34
  dangling ones are the "dangling up/down" failures of §4, not assets.
- **`l1_updater` / freshness** (the inert "auto-deposit" pipe, short-circuiting at
  `sensors/l1_updater.py:213` on empty `affected_anchors`) → its input should be
  *decisions*, and most of what it tried to do splits cleanly into the two checkers
  of §5.
- **The process/lifecycle gates** we earlier called "duplicative of superpowers +
  git": the resolution is that a gate which only tracks *which step you're on* is
  duplicative — but a gate that checks *whether this layer still obeys the human
  decision above it* is precisely what superpowers and git do **not** do. That
  conformance check is the tool's non-duplicative core.
- **`cli-reference-drift`** is the one working ground-truth checker — the template
  for §5, to be generalized and actually made `required`.

## 9. External grounding (so the next session doesn't re-derive or re-err)

- **Harness = everything in an agent except the model** (`Agent = Model + Harness`),
  organized as **guides (feedforward)** + **sensors (feedback)** × **computational
  (deterministic, reliable)** + **inferential (LLM, non-deterministic)**, acting as
  a **cybernetic governor**. Authoritative source: Birgitta Böckeler, "Harness
  engineering for coding agent users", martinfowler.com (2026-04-02), + the "first
  thoughts" memo. *Correction banked:* "harness = a hard gate, skills don't count"
  is **wrong/too narrow** — skills/AGENTS.md are harness (inferential guides); hard
  enforcement is one cell, not the definition.
- Böckeler's memo names **"garbage collection: agents that run periodically to find
  inconsistencies in documentation"** as a harness category — i.e. doc-inconsistency
  detection is a blessed, named part of a harness, not something we invented outside
  it. (We choose the harder, computational, merge-gate form over her soft periodic
  agent.)
- **Doc-drift prior art:** our stance is **Docs-as-Tests** (Manny Silva) — docs make
  falsifiable assertions, fail the build on mismatch — not Docs-as-Code. The
  regen-and-diff / golden-file pattern (Stardoc, oasdiff, snapshot tests) is the
  mature ground-truth baseline. The single precedent for *anchor + drift detection*
  is **Java hybrid `@snippet`** (the doclet verifies inline copy == external region;
  Oracle warns it's a maintenance burden — "adopt late, when code has stabilized").
  Anchoring a doc to an abstract **decision/capability** unit (vs symbol/region/
  file/endpoint) is **not matched by any named tool** — genuinely novel; the closest
  coarse-unit precedent is the **ADR** (one record = one decision; Nygard), which no
  tool mechanically enforces.

## 10. One-line summary

> The AI does all the work, the human only ratifies decisions. The tool makes those
> decisions bind: one decision = one thread through spec→plan→code→doc; every link
> is checked against the link above; mechanically-checkable links hard-fail (and
> auto-fix), the rest force a re-review; a break at the human-ratified end is kicked
> back to the human, never laundered. Structure can be welded shut; semantics can
> only be forced to be re-examined; a determined owner can still bypass — so it welds
> the floor and leaves a trail, nothing more, nothing less.

## 11. Open / not-yet-clear (honest gaps — do NOT read §1–§10 as "done")

The *concept* is coherent, but several engineering pieces are still fuzzy or
un-designed. Worst first.

**Could break the design** (first cuts now in §12, 2026-06-08):

1. **Upper links (Spec↔Plan, Plan↔Code) are not actually designed.** §2 asserts
   "same pattern"; §3–§7 only worked out the *lower* links (code↔decision,
   code↔doc). Yet these upper links are the *more valuable* ones (where a human
   decision is most easily overridden). *(Being dug 2026-06-08. First cut: the Plan
   is decision-tagged just like code/doc, so the same dangling / changed signals
   apply one layer up; "Plan covers the whole Spec" = "every ratified decision has a
   plan-item" is **mechanical** (dangling-down); only "this plan-item correctly
   elaborates D" + "fits architecture" is the inferential review; and architecture
   itself is just a *class of decisions*, so arch-fit folds into the same betrayal
   check against arch-type decisions.)*
2. **Many-to-many web + false-positive flood.** §3 flags the web but does not solve
   it. A decision spread over many sites — and a site carrying several decisions —
   means "tagged code changed → re-check" fires on *every* trivial edit (even a
   rename), drowning re-checks in noise → rubber-stamp. "Tiered" (§7.2) only gestures
   at this; the noise / false-positive control is undesigned.
3. **"Checkability" of a decision is undefined.** A decision is prose; "auth must be
   stateless" is checkable, "code should be elegant" is a dead anchor. No bar exists
   for how concrete a decision must be to be anchorable. Too-vague decisions = the
   34-dangling problem wearing a ratification stamp.

**Fuzzy but not fatal** (#4 conceptual half → §12.4; #5 subsumed by §12.2–3; #6 deferred to build):

4. **Ratification mechanics + AI-controls-attention.** The human's actual ratify
   *action* is unspecified (§7.1 says how identity is *recorded*, not the act). And
   "the AI flags which decisions are load-bearing" is itself an AI judgment —
   under-flag a risky one and the human bulk-approves it.
5. **Hard-vs-soft coverage is thinner than it feels.** Only two things are
   mechanically welded (ground-truth regen-diff; decision-record integrity lock);
   most decisions are prose → the soft proxy. For the primary user (all-AI + a solo,
   possibly lazy human) the residual *hard* protection is a narrow band.
6. **Migration from the current state is undesigned.** Specs don't enumerate
   decisions, nothing is ratified, 34 `@capability` dangle. Adoption means
   retro-creating + ratifying decisions for everything — exactly the cost Oracle's
   `@snippet` "adopt late, when stabilized" warning (§9) is about.

Tracked in `private/OPEN-ITEMS.md` #7. The design is **conceptual-complete,
engineering-incomplete** — §1–§10 describe the intended shape, not a built/airtight one.

## 12. First cuts for the three serious gaps (2026-06-08)

Worked out in brainstorm; these resolve §11.1–§11.3 at concept level (residuals noted).

### 12.1 Upper links (Spec↔Plan, Plan↔Code) — same thread, one layer up

Not a new mechanism: **everything threads on the decision.** The Plan is
decision-tagged like code/doc — each plan-item declares "I elaborate decision D" — so
the same three signals apply at the Plan layer:
- dangling up: a plan-item cites a non-ratified `D` → illegal.
- **dangling down: a ratified decision with no plan-item → silently dropped — this is
  MECHANICAL** (set difference: ratified decisions vs decisions-with-a-plan-item). So
  "does the Plan cover the whole Spec?" — which felt inferential — is a hard check.
- changed → re-check that the plan-item still elaborates `D`.

Refinements:
- **Decisions are minted at both layers:** the Spec holds what/why decisions; the Plan
  mints its own *how* decisions (library, structure), human-ratified at plan time. A
  decision is born where the human ratifies that choice and threads downward.
- **Architecture-fitness folds in:** "the architecture" is a *class of decisions*
  ("layered, no upward imports"); "fits architecture" = passes the betrayal check
  against arch-type decisions. No separate architecture subsystem.
- **Plan↔Code needs no separate anchor:** both point at the same decisions. "Code
  implements plan" = code-review against those decisions (inferential); "every plan
  decision has code" = mechanical dangling-down again.

The win: **silent-drop of a decision (decided but never planned/built) is caught
mechanically at every layer** — the most valuable thing, and it is *hard*. Residual
(soft): "plan-item correctly elaborates D" / "code correctly implements plan" is
inferential (rubber-stamp ceiling) — the mechanical check catches "nobody even claimed
to do it", not "claimed but did it wrong/hollow." Leans on §12.2 / §12.3.

### 12.2 False-positive flood — watch the rule, not the bytes

The flood comes from the weakest signal ("text changed"). Fix = a signal-strength
ladder; push decisions to the top rung:
1. **Decision has an executable check** (test / structural rule / lint): a change fires
   only when the check *fails*. **No flood**; many-to-many dissolves (one global
   invariant covers N sites; one site's edit runs only the relevant checks). This is
   the target — a link should *prefer* an executable check. *(This adds a third checker
   kind to §5: the executable invariant, beside regen-and-diff.)*
2. **No invariant, tight anchor:** tag the narrowest stable region; normalize (ignore
   formatting/comments/internal renames) before diffing. Less noise, not zero.
3. **Pure rationale:** a cheap relevance-filter absorbs the flood (escalate only "maybe
   relevant") — and these arguably should not hard-gate at all (context, not contract).

Lever: don't "stop the flood" — **minimize the population stuck on weak signals.**
Residual: an executable check is AI-written → could be hollow (see §12.3 anti-hollow);
the relevance-filter can false-negative.

### 12.3 Checkability — the decision must arrive with its check

Definition (handed over by §12.2): a decision is "checkable" to the degree you can
write an executable check for it. **Enforced at birth: the AI must propose the check
*alongside* the decision; the human ratifies decision + check together.** Outcome by
what it carries:
- executable check → **hard anchor** (can block merge).
- concrete acceptance criterion, no automatable check → **reviewable anchor** (the
  re-check judges against the criterion, not vibes).
- nothing checkable → **recorded as context** (surfaced in feedforward), **never
  gates** — a wish, not a contract.

Birth rule: **no check → no hard anchor.** (Also explains the 34 dangling: no decision
and no check behind them.)

**Anti-hollow-check lever:** require the check to *demonstrably fail on a
counterexample* ("show it biting"). An always-pass check is exposed; the runnable
counterexample is reviewable.

Residuals (honest): check + counterexample are AI-written (can be weak, but runnable /
reviewable beats a prose "I checked"); **important-but-inherently-fuzzy decisions
("intuitive API", "elegant") get the weakest enforcement** — unavoidable; and **watch
the hard-anchor : context ratio** — if most decisions fall to "context", the system has
silently gone mostly-advisory.

### 12.4 Attention routing must not be AI-controlled (conceptual half of §11.4)

The model rests on "human judgment is the ground truth" — but if the AI flags which
decisions the human should scrutinize ("these are load-bearing, bulk-approve the rest"),
the unreliable element controls the overseer's attention: under-flag a bad decision and
it slips through bulk-approval. Fix: **the AI must not own the human's attention budget.**

- **The AI's flag may only add emphasis — never auto-approve or hide.** Every decision
  still needs an explicit human ratification; there is no AI-triggerable "silent
  bulk-pass." Under-flagging can fail to highlight, but cannot remove a decision from the
  ratification queue.
- **Scrutiny routing is driven by mechanical signals the AI does not control**, not its
  opinion:
  - **Overriding a prior ratified decision = top priority, and is mechanically detected**
    (the §12.3 / §7.2 re-ratification lock). Overturning past human judgment always
    surfaces, regardless of AI flagging.
  - **An uncheckable (rung-3) decision escalates to *more* human attention, not less** —
    the machine cannot guard it, so "the AI could not write a check" is itself a
    call-the-human signal (inverts the tendency to ignore the fuzzy ones).
  - plus blast radius (many sites), sensitive category (auth/data/money), no-precedent
    novelty.
- **Independent second-opinion on the flagging** (fresh agent / CI reviewer: "anything
  risky under-flagged?") as a cost-raising backstop.

Two load-bearing inversions: **uncheckable → more eyes** (not fewer); **overriding a
prior decision always surfaces** (mechanical, AI cannot suppress).

*Honest limit:* still cannot force the human to actually look (they can rubber-stamp a
clearly-flagged danger — the bedrock human ceiling). But the AI's lever to route
attention *away* from danger is removed; the residual is "the human chose not to look at
a visibly-flagged risk" — on the human, visible, not hidden by the AI.

**Disposition of the other medium gaps:** the rest of §11.4 (the human's concrete ratify
*action* — command / UI) is deferred to build time. §11.5 (hard-vs-soft coverage) is
subsumed by §12.2–§12.3 — the hard slice *grows* as decisions are made checkable, so it
is a ratio to watch (§12.3), not a mechanism to design. §11.6 (migration) is deferred to
build (must be done against the real codebase). **Design-time brainstorm closed here;
remaining work is build-time.**
