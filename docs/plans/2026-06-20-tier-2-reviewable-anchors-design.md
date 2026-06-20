# Design: Tier-2 reviewable anchors + change→route-to-review

Date: 2026-06-20
Status: converged (brainstorm) — aligned interactively with the user (product owner).
No TDD/task breakdown yet.

> NOTE: like the sibling design docs in this folder, this file carries **NO**
> `change:` / `stage:` frontmatter — the repo self-hosts on the SuperpowersAdapter,
> which discovers changes by that frontmatter in `docs/plans/`. It stays an inert
> design artifact until `change start` is run explicitly.

> SSOT for the strength ladder and the two-arm model is the umbrella design
> `2026-06-05-decision-conformance-harness-design.md` (§7.2, §12.3, §12.4, §6). This
> doc builds the **middle rung** of that ladder; it does not restate it.

## 0. Where this sits (read first)

The umbrella strength ladder (§12.3):

- **tier-1 — executable check → hard anchor.** Shipped (Tool A text-lock PR #40 +
  Tool B executable checks PR #41). A decision carries a runnable `check` + a
  `counterexample`; `decision check` runs it; failure hard-blocks (exit 2).
- **tier-2 — concrete acceptance criterion, no automatable check → reviewable
  anchor.** *This slice.* The decision arrives with a criterion a human/agent judges
  against; the machine cannot decide pass/fail, so it **forces a re-review to happen
  and leaves a trail** — it does not pretend to judge correctness.
- **tier-3 — nothing checkable → recorded as context.** Already exists (a ratified
  decision with neither `check` nor criterion); surfaced, never gates.

tier-2 is *stronger than prose, weaker than executable*. The whole job of this slice:
make a decision that **cannot** carry a runnable check still bind — by routing its
anchored code's changes into a recorded re-review, mechanically forcing the review to
*occur* without faking a verdict on its correctness.

Pairing (umbrella §4 "Changed" / §5 proxy-checker / §7.2): the re-review subject is
**the decision D**, triggered **on change of D's anchored code** — NOT the
change-lifecycle entity. (Earlier brainstorm error — reaching for the change-level
`review approve` substrate — corrected: tier-2 gets its own decision-scoped verdict.)

## 1. The end-to-end picture

Scenario: a solo owner + an AI agent that runs largely unattended.

1. **Birth (AI proposes, human ratifies).** The AI hits a decision that has a concrete
   acceptance criterion but no runnable check ("error responses must not leak internal
   stack traces to the client"). It writes the criterion into a ` ```review ` block in
   the decision body and proposes it. The human runs `decision ratify <id>`, which —
   exactly as today — freezes a fingerprint of the **whole body** (the criterion is
   inside the body, so it is locked for free) and stamps who/when.
2. **First reconcile (baseline set).** Once code anchors the decision
   (`@decision:<id>`), the agent runs `decision reconcile <id>` to record the
   **baseline**: a content fingerprint of D's anchored files at this moment, plus
   who/when and a self/independent marker. D is now reconciled.
3. **The AI works and self-checks.** It changes code, refactors, edits rationale. At
   checkpoints it runs `decision check` locally. As long as D's anchored files are
   unchanged since the baseline, tier-2 is silent.
4. **Anchored code moves → D goes *suspect* (mechanical).** A content-fingerprint
   mismatch on any anchored file flips D to suspect. `decision check` **surfaces this
   as a warning** — never an error — and the agent cannot suppress it (it is computed,
   not asserted). This is the *routing*, not a gate.
5. **Re-review (the verdict).** A human or an independent agent judges "does the
   changed code still satisfy D's criterion?" and records the verdict:
   - **reconcile** → still satisfies; re-stamp the baseline + who/when/justification +
     self/independent. D leaves suspect.
   - **betray** → no longer satisfies; baseline NOT advanced; escalated. The human must
     re-ratify an updated D or reject/fix the code. The AI cannot self-resolve and must
     not silently edit D to match the code.
6. **The teeth (merge boundary).** At the merge gate, a **suspect** tier-2 decision →
   **fail-closed**. Because `reconcile` re-stamps the baseline (which is exactly what
   clears suspect), "suspect" already means "no reconcile covers the current code" — the
   gate needs no separate verdict-existence predicate (see §5). This forces the *act* of
   re-review + its trail before merge. It does NOT gate on whether the code is correct
   (tier-2 cannot judge that) — only on whether the anchored code's change was reconciled
   at all.

## 2. Declaration — how a decision becomes tier-2

A new fenced block in the decision body, sibling to ` ```check ` / ` ```counterexample `:

    ```review
    Acceptance criterion: <concrete, judgeable statement>.
    Re-review protocol: <what to look at to decide "still satisfies">.
    ```

Tier classification (first match wins):

| body carries | tier | gate behavior |
|---|---|---|
| ` ```check ` (+ ` ```counterexample `) | 1 — hard anchor | check fails → exit 2 |
| ` ```review ` (no `check`) | 2 — reviewable anchor | suspect+no-verdict at merge → exit 2 |
| neither | 3 — context | never gates |

- `Decision` gains `acceptance: str | None`, parsed by a `parse_review(body)` mirroring
  `parse_check` (at most one block; stripped; empty → None).
- An **empty** ` ```review ` block strips to `""` → `parse_review` returns `None` →
  the decision falls through to **tier-3 context, NOT a toothless tier-2** (mirrors
  `parse_check`'s empty contract). State this so an empty criterion never classifies as
  tier-2.
- **Implementation note (not free):** `Decision` also gains the reconcile fields below
  (§3). `serialize_decision` currently writes a **fixed whitelist** of frontmatter keys
  (decisions.py:193–201, typed `fm: dict[str, str]`, guarded `if val:`) and
  `parse_decision_file` reads a fixed set — both must be **explicitly extended** to
  round-trip `reconciled_anchors` (a **nested mapping — assigned as a dict, NOT
  stringified**; the `fm` type annotation widens to `dict[str, object]`) +
  `last_reconciled_by/at` + `last_reconcile_kind` (+ the betray fields in §6, all plain
  strings), or the baseline silently evaporates on every `write_decision`. An empty
  `reconciled_anchors` dict is falsy → correctly skipped by `if val:`. This is a named
  code step, not a stamp that comes for free. `yaml.safe_dump`/`split_frontmatter`
  round-trip nested dicts fine.
- The block is **inside `body`** → covered by `ratified_text_hash`. So the criterion is
  **welded shut** the same way the tier-1 check is: editing it without re-ratifying =
  integrity violation (exit 2). One hash locks the teeth, tier-1 and tier-2 alike.
- A decision with **both** `check` and `review` is tier-1 (executable wins; the prose
  criterion is then redundant rationale). A lint-level warning may flag it; not blocking.

## 3. Baseline — what "last reconcile" stores, and where

**Where: the decision's frontmatter** (not the gitignored `.harness/pending-reviews/`,
which CI cannot see; not a separate ledger). Rationale: `ratify` already stamps mutable
frontmatter (`ratified_by/at`, `ratified_text_hash`); reconcile is the *same kind of
act* — a stamp the harness writes at a lifecycle verb. No new file type, no new loader,
no "ledger points at a deleted decision" dangling mode. The attributable, diff-trailed
history (§7.2c) is **git history of the decision file** (`git log docs/decisions/<id>.md`),
exactly as `ratified_text_hash`'s history already lives in git, not in an in-file log.

**What:**

    reconciled_anchors:
      src/foo/handler.py: "sha256:<hex>"
      src/foo/render.py:  "sha256:<hex>"
    last_reconciled_by: alice@example.com
    last_reconciled_at: 2026-06-20T12:00:00Z
    last_reconcile_kind: self            # self | independent

- Fingerprint is **sha256 of raw file bytes** (shares the `sha256:` prefix convention of
  `compute_body_hash` but is **deliberately NOT normalized** — body-hash CRLF/whitespace-
  normalizes prose; the fingerprint is byte-exact so any change to anchored code, incl.
  whitespace, re-routes the review). NOT a git blob hash or commit SHA. Reasons: no git
  dependency for the compare (works on any checkout / dirty tree); "changed then changed
  back" does not false-positive (content equality is content equality), where a commit-SHA
  baseline would.
- **Granularity: whole anchored file**, for the MVP. This reuses the tier-1 `--changed`
  file×anchor-map machinery and needs no region extraction (the scanner yields
  `(file, line)`, not function bodies). It is coarse by construction — an unrelated edit
  elsewhere in an anchored file marks D suspect — which §5 explicitly accepts: the proxy
  gate "has false positives by construction" because it only *forces a re-review*, never
  auto-fails. Region-level precision is a deferred upgrade (§9).

## 4. Suspect — the standing invariant

For each **ratified tier-2** decision D with a recorded baseline, in `run_check`'s pure
layer:

- Resolve D's current anchored files (from the existing anchor scan).
- D is **suspect** iff *any* current anchored file has `sha256(content) != stored`, OR a
  current anchored file is absent from `reconciled_anchors` (a new anchor never
  reviewed).
- A stored file that is no longer anchored is simply dropped from consideration (not
  suspect — the claim on it was removed).
- A ratified tier-2 decision with anchors but **no baseline at all** → suspect
  (unreconciled; needs a first `decision reconcile`).
- A ratified tier-2 decision with **no anchors** → dangling-down (warn), never suspect.

This is a **standing whole-repo invariant** like tier-1 body-hash integrity: evaluable
on any checkout from stored-vs-current, with no dependency on a base branch or
`--changed`. tier-2 therefore does not use `--changed`; it always compares current vs
baseline. (`--changed` could later prune which files to re-hash — an optimization, not
correctness; §9.) Fingerprinting reads file content via `Path.read_text` on the
scanner-discovered paths (no git), matching the scanner's binary-skip semantics — so it
works on a non-git checkout (`scan_sentinel_locations` already walks the filesystem when
not a git repo).

`CheckResult` gains `suspect_tier2: list[SuspectDecision]` (id + the changed files) and
`unreconciled_tier2: list[str]`. Both must also be threaded into the `--json` envelope
`data` block in `check_cmd` (decision.py:296–313), or JSON consumers won't see the new
state. (Layer note: this computation reads file content via `Path.read_text` but spawns
**no subprocess and shells out to no git** — so it belongs in the `run_check` pure layer,
exactly where the tier-1 body-hash integrity check already lives; the "pure" boundary in
this codebase means *subprocess/git-free*, not *IO-free* — `run_check` already reads
files via `scan_sentinel_locations`. The dirty `check_runner.py` layer is only for the
subprocess-spawning tier-1 executable checks.)

**Lifecycle interactions (explicit, so an implementer cannot quietly defeat the first
re-review):**
- **Only `status == "ratified"` tier-2 decisions are suspect-eligible.** `superseded` /
  `retired` decisions exit suspect entirely (retire is the documented "stop anchoring"
  exit; supersede hands the thread to the successor).
- **`ratify` does NOT auto-reconcile.** A freshly-ratified tier-2 decision with anchors
  has no baseline ⇒ suspect until the first explicit `decision reconcile`. Do not wire
  ratify to stamp a baseline — that would skip the first re-review.
- **tier-1 ↔ tier-2 flip.** Removing a `check` block and adding a `review` block edits
  the body ⇒ integrity violation (exit 2) ⇒ must re-ratify. Re-ratify makes it tier-2
  with no baseline ⇒ suspect until first reconcile. Correct by construction; stated so
  ratify is not "fixed" to auto-reconcile.
- **Corruption / deletion edge (minor, noted):** a stored anchored file that becomes
  unreadable/binary, OR is deleted-but-still-tracked (scanner returns it from
  `git ls-files` but `is_file()` is False → skipped), is dropped from consideration ⇒
  silent suspect-clear. Acceptable for MVP; region-level + explicit-missing handling is a
  §9 defer.

## 5. Routing vs teeth — the two exit semantics

This is the load-bearing split (umbrella §12.4: attention routing must not be a gate the
AI controls; §7.2/§6: the review must be *forced to happen* + leave a trail).

**Routing and teeth read the *same* predicate — `suspect` — and differ only in exit-code
policy.** This is the key simplification: there is no separate "a verdict exists"
predicate. `reconcile` re-stamps the baseline, and re-stamping the baseline is *exactly*
what flips `suspect` back to false. So "suspect" already means "the current anchored code
has not been reconciled" — checking "is there a verdict for the current code state" would
be the same computation. One predicate, two policies:

- **Default `decision check` (local feedforward + day-to-day CI) — WARN, never exit 2 on
  tier-2.** Suspect and unreconciled tier-2 decisions print as warnings (alongside
  `dangling-down` / `unhashed`). Exit stays governed by tier-1 only: integrity violations
  / tier-1 check failures / dangling-up → exit 2; everything tier-2 → warn. Pure
  surfacing; the signal is mechanical (sha256 compare), so the AI cannot under-flag to
  hide a decision that should be re-reviewed — §12.4 satisfied.
- **Merge-boundary mode `decision check --gate-reconcile` (CI merge gate only) — exit 2
  on any suspect tier-2.** Same predicate, blocking policy. A distinct invocation run
  only at the merge boundary (wired into the existing `decision-check.yml`, no new
  workflow), so the default sensor the AI runs constantly stays warn-only (routing) while
  the merge boundary blocks (forcing). It does **not** evaluate criterion satisfaction —
  it blocks suspect-not-reconciled, i.e. "anchored code changed and no `reconcile`
  recorded it."

Where does `betray` fit? `betray` records "code no longer satisfies D" and deliberately
does **not** advance the baseline — so the decision **stays suspect**, the gate **stays
closed**, and resolution is forced to the human (re-ratify an updated D, or fix/reject the
code). `betray` therefore adds no new *gate state* (suspect already blocks); its value is
the **escalation record + justification trail**, distinguishing "not yet looked at" from
"looked at, judged broken, awaiting human."

Why a separate mode and not exit-2-by-default: "routing not gate" (§12.4) requires the
standing check the AI runs constantly to *not* hard-fail on the inherently-fuzzy,
false-positive-prone-by-construction tier-2 signal. The forcing belongs at the merge
boundary — the "hard floor" rail of §7.3 — where the human-or-CI actually integrates.

Decided exit-code semantics:

| invocation | tier-2 suspect (incl. unreconciled) | tier-1 violation |
|---|---|---|
| `decision check` (default) | warning, exit 0 | exit 2 |
| `decision check --gate-reconcile` (merge boundary) | exit 2 | exit 2 |

## 6. The verdict — reconcile / betray

The harness does **not** run the review ([[project-harness-never-spawns-agent]]). A human
or an independent agent produces the judgment; a verb records it; the gate forces that
*some* verdict exists. This mirrors the existing change-level `review approve` exactly —
the harness forces a verdict to exist, the actor produces its content.

New verbs under the `decision` group:

- `decision reconcile <id> [--justification TEXT] [--kind self|independent]` — the "still
  satisfies" path. Re-stamps `reconciled_anchors` to current fingerprints + stamps
  `last_reconciled_by` (`resolve_identity`) / `last_reconciled_at` / `last_reconcile_kind` /
  `last_reconcile_justification` (persisted symmetrically with betray's, per §1 step 5 +
  §7.2c "the re-check must produce a justification referencing D's specific claims").
  Writes via `write_decision` (CLI side effect, like `ratify` — not intercepted by the
  PreToolUse gate; `reconcile`/`betray` must be on the same allowlist as the other
  `decision` verbs, else the agent's own self-sensor flow is blocked). Requires D to be
  ratified tier-2. **First-reconcile-on-suspect is the normal path** (a freshly-ratified
  or freshly-changed D is suspect — the verb must not gate itself on `not suspect`); on
  an already-reconciled, non-suspect D it is an idempotent no-op refresh.
- `decision betray <id> --justification TEXT` — records that the code no longer satisfies
  D. Does NOT advance the baseline (so D stays suspect by construction). Stamps
  `last_betrayed_by` / `last_betrayed_at` / `last_betray_justification` into frontmatter
  (same whitelist extension as the reconcile stamps) so the escalation is current-state,
  not git-archaeology-only — this is what backs "betray surfaces in `decision check`
  output until resolved." Resolution is human-only: re-ratify an updated D (re-locks the
  body hash) or reject/fix the code. The agent must not edit D's body to launder. A later
  `reconcile` clears the betray stamps (the standard supersession: a fresh verdict
  replaces the stale one).

**Self vs independent disclosure (the identity-agnostic knob).** `--kind` defaults to
`self`. The marker is a structured disclosure, the same pattern as `review skip`'s
`payload["skipped"]=True` (HG-12): it lets the merge gate and post-hoc audit distinguish
a self-reconcile (same actor that changed the code) from an independent one. Crucially we
do **not** bake the solo assumption into the mechanism — see §8.

Where the verdict trail lives: in the decision file's frontmatter (current state) + git
history (the durable, attributable, diff-trailed log). betray escalations additionally
surface in `decision check` output until resolved.

## 7. Agent-callability — who runs what

Every step is a CLI verb the agent can invoke; that is the harness-never-spawns design,
not a leak. The honest map:

**Agent-owned by design** (running these *is* the intended flow):
- `decision new` (propose the criterion), `decision check` (self-sensor at checkpoints),
  `decision reconcile` (record the re-review — tiered: cheap self-check by default,
  escalate to an independent reviewer for load-bearing decisions, per §7.2b). This is the
  same trust model as the agent already calling `review approve --reviewer code-reviewer`.

**Mechanical, AI cannot suppress:**
- the suspect flag (sha256 compare, not an AI assertion);
- the merge-boundary `--gate-reconcile` block (a CI exit code the agent does not control).
  The agent's only evasions are *not anchoring the code* (→ dangling-down warn; the known
  "tags are AI-placed" §6 leak) or *the owner disabling CI* (§8).

**Human-ratified end** (`ratify`, re-ratify after betray): mechanically agent-callable, but
this is where human judgment is meant to live; the harness's response is attribute + git
trail + tier, not prevention (§8).

## 8. The honest boundary — and why "solo" is only a caveat, not a premise

Decompose the teeth by what actually depends on the deployment being a single owner:

**Does NOT depend on solo (real teeth in any deployment):**
- the criterion is body-hash-locked — editing it without re-ratify is an integrity
  violation (purely mechanical);
- the suspect flag cannot be hidden by the AI (sha256 compare);
- every recorded verdict is attributable + diff-trailed.

These three are the truly deployment-independent teeth: they hold even if CI is off,
because they live in the committed files, not the gate.

**Depends on solo (where teeth soften to disclosure):**
- **the merge gate's *forcing*** — "block suspect-not-reconciled code" is exactly as
  strong as "CI cannot be disabled". For a solo owner the gate is in-repo and forgeable
  ([[project-bedrock-solo-owner-unforgeable]]); under org-level branch protection an
  individual cannot turn it off. So the gate *forces the re-review to happen* in a team,
  and merely *strongly nudges + records the skip* solo. (The suspect flag and the lock
  above still bite regardless.)
- **owner can disable CI / the gate** — the general form of the above: any in-repo gate
  is forgeable by a determined owner;
- **independence (reviewer ≠ author) can only be disclosed, not enforced** — solo
  collapses all identities to the owner; a team has genuinely distinct accounts.

Design consequence: **the mechanism is identity-agnostic; enforcement strength scales
with the deployment.** We always record author identity + reviewer identity + the
self/independent marker. A team / branch-protected deployment can *promote* that marker
into an enforced gate condition (require `--kind independent` with a reviewer distinct
from the code author). A solo deployment *degrades* the same machinery to disclosure —
honest about the ceiling, without weakening the parts that don't need a team.

Positioning, unchanged: **raise the floor, make laziness and drift impossible to hide,
leave a trail.** Decisive against a fallible-not-adversarial agent; cost-raising against a
determined one. Do not over-claim: tier-2 cannot guarantee the verdict is *honest* (a
rubber-stamp reconcile is possible) — it guarantees the re-review *happened*, is
*attributed*, and is *on the record to be re-examined*.

## 9. Deferred (register in OPEN-ITEMS SLICE-4)

- **Region-level baseline** — fingerprint the anchored function/class body, not the whole
  file, to cut false-positive suspects. Needs region extraction the scanner lacks today.
- **`--changed` pruning for tier-2** — an optimization to skip re-hashing untouched files;
  correctness does not need it (standing invariant compares all).
- **Team enforcement of `--kind independent`** — promote the disclosure marker to a gate
  condition (reviewer ≠ author); needs the team/branch-protection substrate.
- **`decision show` exposing tier-2 state** — baseline, suspect, last verdict (UX; the
  check is the system of record), bundled with the existing tier-1 `show` polish defer.
- **Tier mismatch lint** — a decision carrying both `check` and `review` warns.

## 10. One-line summary

A tier-2 decision arrives with a body-hash-locked acceptance criterion but no runnable
check; its anchored code is fingerprinted at reconcile; any change flips it suspect
(mechanical, unsuppressable); `decision check` routes that as a warning while the
merge-boundary `--gate-reconcile` fails closed on suspect-without-verdict — forcing the
re-review to *happen* and leave an attributed, diff-trailed trail, never pretending to
judge whether the code is correct.
