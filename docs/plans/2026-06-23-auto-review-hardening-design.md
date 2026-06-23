# Auto-review hardening — design

> Status: design (brainstorming output, pre-plan) — revised after adversarial review round 1
> Date: 2026-06-23
> Change slug: `2026-06-23-auto-review-hardening`
> Intent line: ①b "content-corruption closed loop" — see
> `private/CAPABILITY-CONVERGENCE-LEDGER.md` (①b row, 🔴), VISION Axiom 4,
> HARNESS-GAPS HG-02 / HG-12.

## 1. Problem

The harness today enforces **order** (the PreToolUse gate + the 10-state
machine: you cannot implement without an approved plan, cannot merge without a
recorded review verdict). It does **not** enforce **content quality**. The only
thing that produces a content verdict at the `AWAITING_CODE_REVIEW` boundary is
a human or the code agent's own reviewer subagent, **voluntarily**, by following
the `AGENTS.md` review protocol. The gate only checks that *some*
`code_review_passed` event exists — it does not verify a real review happened.

In the vibe-coding flow the user walks away and the **same agent that wrote the
code drives the whole lifecycle**. At the code-review boundary nothing stops it
from emitting `review approve` without looking at the diff. The content gate is
the agent grading its own homework, and the grading step is silently skippable.
`done`'s `VerificationRunner` covers the *computational* floor (tests/lint), but
tests-green ≠ not-garbage: architecture, plan-adherence, edge cases are exactly
what an *inferential* review must catch, and that review is today effectively
hollow.

## 2. What this is (and what it is not)

**`auto review` = the harness forces the code agent to actually run a review at
the right moment, against a deterministically-assembled context, and to leave a
structured record — making a skipped review cost something and leave a trail.**

The harness does **not** run the LLM. *How* the review judges, and *whether* the
judgement is genuine, stays inferential — the agent's reviewer subagent (or a
human) owns it. The CLI cannot and must not control that (see
`project-harness-never-spawns-agent`).

Three-layer responsibility (the spine of this design):

| Who | Owns | Nature |
| --- | --- | --- |
| harness / CLI | review **must happen** (order gate) + must be **against the assembled inputs** (prepare) + must leave a **structured record** (verdict shape) + a skipped review **must be a deliberate, gate-visible act** | mechanical, deterministic |
| code agent's reviewer subagent | actually read, judge, write findings | inferential, not mechanizable |
| human / cross-actor (future HG-12) | verify findings are true; provide independence | absent in solo; deferred to v0.2 |

### Honest ceiling (NOT a TODO — a bedrock limit)

A solo repo owner controls everything in-repo, so a determined agent can
**fabricate** a complete-looking verdict and pass the emit-time checks, or sign
its own skip-override. The CLI validates *shape, coverage, and deliberate
acknowledgement*, never the *truth or quality* of findings. So what this slice
buys is **detection-grade / process-grade** hardening — it moves the content
gate from "silently skippable, free" to "skipping requires either a fabricated
verdict or a deliberate, committed, disclosed override, both of which leave an
auditable trail" — **not** independence. Real independence requires a genuinely
different actor (HG-12, mathematically unreachable for solo). This is accepted
and recorded, not papered over. In particular the skip-override (§4.E) is itself
self-signable by a solo owner; its value is that it forces a *conscious, logged*
choice that reaches the committed attestation, not that it is un-forgeable.

## 3. Goals / non-goals

**Goals**
- A bare `review approve --reviewer code-reviewer` (no structured verdict) is
  **rejected at emit time**.
- The review is performed against a harness-assembled bundle (diff ∩ scope,
  out-of-scope changes flagged, spec/plan paths, the stage checklist), not the
  agent's mental picture.
- Every verdict leaves a structured record **inlined in the event payload** (so
  it is reproducible from the event stream and reaches the committed
  attestation).
- Approving against a **stale** review (diff changed since `prepare`) is rejected
  via a content digest.
- The reject → rework → re-review loop has teeth: an approve coming out of
  `CODE_REVIEW_REJECTED` must dispose of the prior rejects' findings.
- A skipped code review is no longer a silent merge — `attest verify` blocks it
  unless a deliberate `--override` was recorded.
- The checklist is configurable per repo (with a built-in default).

**Non-goals**
- The harness running an LLM (`claude -p` etc.) — out, violates the core
  constraint; remains a v0.2 CI-fallback question.
- Review independence / un-forgeable reviewer identity — HG-12, deferred.
- Verifying the *truth* or *quality* of findings — inferential, not us.
- A daemon `Sensor` subclass for review (see §7 decision).
- Multi-stage reviewer pipeline — VISION says v0.2.
- Anchors in the review bundle — no per-change anchor data source exists yet
  (§4.A note); deferred.
- Hard emit-time teeth on `plan-reviewer` — this slice hardens only the
  code-review boundary (§4.C note, §11).

## 4. Components

This document is the **umbrella design**; the work ships as **two slices**
(decided after adversarial review round 2 — the full set is a 5-deep dependency
chain plus a parallel attest change, too large for one self-hosted PR):

- **Slice 1 (`2026-06-23-auto-review-hardening`)** = **A + B + C (incl. digest) +
  configurable checklist**. The self-contained core: a real review performed
  against a harness-assembled bundle, with a structured verdict inlined in the
  event payload, and bare/incomplete/stale approves rejected at emit. Ships and
  is valuable on its own.
- **Slice 2 (follow-up, separate plan + PR)** = **D (rework-loop teeth) + E
  (skip-override + attest gate)**. The heavier, riskier teeth that both touch
  `attest verify`. Built on top of slice 1's verdict shape.

Build-order dependency (no cycle): checklist-loader → A (bundle embeds checklist)
→ B (verdict references digest + checklist) → C (validates verdict vs bundle).
D depends on B's finding ids + a stream walker + the reducer change; E is an
independent attest-verify branch. Slice 1 is the closed prefix of this chain.

Each component is independently testable.

### A. `review prepare <change> --reviewer code-reviewer|plan-reviewer`

A new CLI verb that deterministically assembles a **review bundle** and writes
it to `.harness/pending-reviews/<change>/<reviewer>.bundle.json` (this path stays
gitignored — the bundle is a transient *input aid*, NOT the record of review;
the record is the inlined verdict in the event payload, §4.B). No LLM.

Bundle contents:
- `diff_in_scope`: changed files ∩ declared `scope.files`. Reuse
  `core.source_scope.load_source_scope` and the scope-set computation extracted
  from `scope-vs-plan-final` (see §4.C / §6 on the helper split).
- `out_of_scope`: changed files **not** covered by declared scope — surfaced
  explicitly so the reviewer cannot miss drift.
- `spec_paths` / `plan_paths`: resolved via the active framework adapter.
- `checklist`: the resolved checklist for this reviewer stage (see
  "configurable checklist"). Embedded so the verdict's coverage check is against
  exactly what `prepare` presented.
- `bundle_digest`: see §4.C for the pinned semantics.

Output: writes the artifact, prints its path (+ `--json` envelope). The agent
reads it and hands it to its reviewer subagent.

**Note — anchors deferred:** the original design put declared anchors in the
bundle, but verified: `anchor_scanner.scan_sentinels` is whole-repo, and there is
no per-change "declared anchors" datum in `ChangeState` (src carries no
`@implements`/`affected_anchors`). A whole-repo sentinel dump is noise to a
reviewer, so anchors are **out of the bundle this slice** and tracked with the
anchors line.

### B. Structured verdict — inlined in the event payload

`review approve` / `review reject` gain `--verdict-file <path>` pointing at a
structured verdict the agent's reviewer subagent produced:

```yaml
bundle_ref: .harness/pending-reviews/<change>/<reviewer>.bundle.json
bundle_digest: <digest copied from the bundle being reviewed>
checklist:
  - item: spec-compliance      # every checklist item present, no blanks
    status: pass | fail | na
    note: "..."
findings:                       # may be empty ONLY when no checklist item failed
  - id: f-001                   # stable id, needed for D
    severity: blocker | major | minor
    file: path/to/file.py
    summary: "..."
prior_findings:                 # required when emitting from CODE_REVIEW_REJECTED (§4.D)
  - id: f-001
    disposition: resolved | wontfix
    note: "..."                 # required for wontfix
```

**Storage decision (resolves the round-1 blocker):** both `.harness/events.jsonl`
and `.harness/pending-reviews/` are gitignored; the **committed** artifact that
reaches the merge boundary is the attestation (`attest write` snapshots the
change's event slice to `.harness/attestations/<slug>.jsonl`). Therefore the
verdict-of-record is **inlined into the emitted event's payload** (a normalized
`verdict` object: `checklist`, `findings`, `prior_findings`, `bundle_digest`).
Consequences:
- D's open-findings derivation reads the event stream directly (events.jsonl is
  present locally during the lifecycle run) — fully deterministic, no dependency
  on the gitignored bundle file.
- The audit trail reaches the merge boundary because `attest write` snapshots the
  payload-bearing events into the committed attestation.
- Slice 1 inlines the verdict in the payload and needs **no reducer/state
  change** — C recomputes the digest from the current `HEAD` diff and compares it
  to the verdict's `bundle_digest` carried on the `--verdict-file`. The reducer
  extension to retain the latest code-review `verdict` on `ChangeState`
  (mirroring `scope` retention from `plan_ready`, respecting the §3.8.5
  last-write-wins / idempotent invariants) is a **slice-2** convenience for D;
  D's open-finding walker reads the raw stream regardless. (Findings are small —
  ids + severity + one-line summary — so payload size is a non-issue.)

### C. Emit-time teeth (the enforcement)

The enforcement point is **`review approve` at emit time**, NOT the PreToolUse
gate. Verified: `PreToolUseGate` maps `ChangeState` → allow/block and never
inspects payload content (`gates/pre_tool_use.py`). So:

- **PreToolUse gate (unchanged)**: enforces *order*.
- **`review approve` emit validation (new)**: enforces *shape + freshness*.
  `review approve --reviewer code-reviewer` refuses to emit `code_review_passed`
  unless an attached structured verdict:
  1. **covers every checklist item** from the bundle (no item blank) — this
     teeth **always bites**, independent of scope;
  2. references a bundle whose digest it can recompute (so `prepare` must have
     run — there is no gate-level ordering for this, the emit verb enforces it).
     The biting check is the **digest match**, not mere file existence: a
     re-`prepare` overwrites the transient bundle, which is fine; and
  3. carries a `bundle_digest` matching the **current** in-scope diff (freshness
     — see digest semantics below). When declared scope is empty / there are no
     in-scope changed files, the digest is over an empty set and is therefore
     **inert** (matches trivially); in that case only the coverage teeth (1)
     bite. This is documented, not hidden.

  A bare `review approve` (no `--verdict-file`) → rejected, `EXIT_VALIDATION` (2)
  + a hint. This is "空盖章被挡".

**`bundle_digest` pinned semantics (resolves round-1 M2):**
- Computed over the **committed `HEAD` tree** content of the in-scope paths
  (`git rev-parse HEAD:<path>` blob hashes, or equivalently a hash over `git
  diff <base>...HEAD` for the in-scope set) — **committed state only**, which is
  reproducible and tamper-evident. Working-tree hashing is rejected (racy,
  bricks the happy path on incidental edits).
- `prepare` and `approve` both require a **clean working tree** for the in-scope
  paths (no uncommitted changes); otherwise they error with a hint to commit.
  This makes "review the committed diff" the contract and removes the
  uncommitted-staleness hole.
- The **base branch is explicit**, not the hardcoded `main`: a `--base` option
  defaulting to a configured base branch. The plan must specify *where* that
  config lives (a `.harness` key) so the implementer does not silently re-hardcode
  `main` as `verification_runner` currently does.
- On any git error the digest computation **fails closed** (the approve is
  rejected with a clear error) — it does NOT inherit the `scope-vs-plan-final`
  baseline's advisory fail-open behavior. The shared helper (§6) is split so only
  the scope-set computation is reused, not the fail-open policy.

`review reject` records its verdict (with findings) the same inlined way but does
not require full checklist coverage — a reject can stop early on a blocker.

### D. Rework-loop teeth (findings must be addressed)

The state machine already supports the loop:
`AWAITING_CODE_REVIEW --code_review_failed--> CODE_REVIEW_REJECTED`, and
`CODE_REVIEW_REJECTED --code_review_passed--> READY_TO_MERGE`. The self-loop
`CODE_REVIEW_REJECTED --code_review_failed--> CODE_REVIEW_REJECTED` means there
can be **N reject verdicts** (`transitions.py`). No state-machine change needed.

Division of labor with the reducer field (§4.B): **C's freshness check uses the
latest-verdict field on `ChangeState`** (single last write, like `scope`);
**D's open-finding set MUST be derived by the stream walker** — the single
ChangeState field cannot express the multi-reject open-set, so do not try to
derive open findings from it.

New teeth: a `review approve` emitted **from `CODE_REVIEW_REJECTED`** must
dispose of every **open finding**. Open-finding derivation (deterministic, reads
the event stream in append order — the only causal truth, `reducer.py`):
- Walk **all** `code_review_failed` events for the change in append order;
- the open set = the union of findings introduced by any reject, **minus** every
  finding id given a `resolved`/`wontfix` disposition in any **later** verdict
  (a finding resolved in one re-review then reopened by a later reject is open
  again — the multi-reject case is handled explicitly).
- The new approve's `prior_findings` must dispose every currently-open id;
  `wontfix` requires a note. Emit of `code_review_passed` from
  `CODE_REVIEW_REJECTED` is rejected if any open finding is left undisposed.

This is the heaviest component; §6 lists the event-stream walker explicitly.

### E. Skip-override + attest gate (close the skip bypass)

Verified round-1 hole: `review skip` emits the **same** `code_review_passed`
event (only `payload["skipped"]=True`), and `attest verify` does not block on it
— so `skip` bypasses C+D entirely and still merges. Fix:

- `review skip --reviewer code-reviewer` **without** override stays the
  deadlock-park escape, but its terminal presence is now a **merge-gate
  blocker**: `attest verify` classifies the change's terminal code-review verdict
  and, if it is a non-overridden skip, emits a blocker.
- `review skip --reviewer code-reviewer --override --reason "<why>"` stamps
  `payload["override"]=True` (alongside `skipped=True`). `attest verify` treats
  an overridden skip as **pass-with-disclosure** — it surfaces the override +
  reason in the verdict output (and the override is snapshotted into the
  committed attestation, so it is loudly visible at the merge boundary).
- No new event type / no transition change — only a payload flag + an `attest
  verify` classification rule. The existing `skipped` disclosure (HG-12 cut-1)
  composes with this.

Honest note (§2 ceiling): a solo owner can self-sign `--override`. The value is
the **deliberate, committed, disclosed** act replacing a silent pass — not
un-forgeability.

### Configurable checklist

Resolution order, per reviewer (`plan-reviewer` / `code-reviewer`):
1. `.harness/review-checklists.yaml` → `checklists.<reviewer>` if present;
2. else the built-in default checklist for that reviewer.

Built-in `code-reviewer` default (illustrative): `spec-compliance`,
`scope-adherence`, `code-quality`, `edge-cases`. Loader is tolerant
(absent/corrupt file → default), matching `source_scope` / `reviewer_policy`
conventions. The resolved checklist is embedded in the bundle.

## 5. Data flow

```
done → AWAITING_CODE_REVIEW
  → git commit  (the in-scope tree must be clean — see "commit obligation" below)
  → review prepare <c> --reviewer code-reviewer       (A: assemble bundle → disk;
                                                          ERRORS if in-scope tree dirty)
  → agent's reviewer subagent reviews the bundle      (inferential, agent's job)
  → review approve|reject <c> --reviewer code-reviewer --verdict-file <f>
                                                       (B: verdict inlined in event payload)
  → emit validation                                   (C: bare approve / incomplete
                                                          checklist / stale or
                                                          unverifiable digest → reject)
  → FAIL: CODE_REVIEW_REJECTED → fix code → git commit → re-prepare → re-review
                                                       (D: approve must dispose all open findings;
                                                          each fix iteration re-commits so HEAD +
                                                          digest reflect the fix)
  → PASS (full evidence): READY_TO_MERGE
  → [escape] review skip [--override --reason ...]     (E: bare skip blocks at merge;
                                                          override passes-with-disclosure)
  → attest write / attest verify                       (E: terminal non-overridden
                                                          skip → merge-gate blocker)
```

**Commit obligation (new lifecycle integration — make explicit):** because the
digest is over the committed `HEAD` tree (§4.C), the in-scope code must be
committed *before* `review prepare`. Today nothing forces a commit between `done`
and review (`done` verifies the working tree and advances state but does not
commit; AGENTS.md treats committing as an agent habit, not a gated step). This
slice does NOT change `done`'s behavior; instead the obligation is enforced at
`review prepare` / `review approve` — they **error with a clear "commit the
in-scope changes first" hint** when the in-scope tree is dirty. The D rework loop
therefore re-commits each iteration before re-preparing. This obligation is
documented in §5 here and in the AGENTS.md review-protocol update (§9). (A future
slice may make `done` refuse to advance with a dirty in-scope tree to turn this
into a hard lifecycle invariant; out of scope here.)

## 6. Architecture / reuse map

- New: `core/review_bundle.py` (assemble bundle + digest), `core/review_verdict.py`
  (verdict schema parse/validate + open-findings derivation over the raw event
  stream), `core/review_checklist.py` (checklist resolution), CLI `review
  prepare`, emit-time validation + `--verdict-file` + `skip --override` in
  `cli/review.py`, and an `attest verify` classification rule for terminal skip.
- Reuse: `source_scope.load_source_scope`; the scope-set computation extracted
  from `_baseline_scope_vs_plan` / `_covered_by_scope` in `verification_runner`
  into a shared helper — **split so the digest gate does NOT inherit the
  baseline's advisory fail-open-on-git-error policy** (digest fails closed);
  adapter spec/plan path resolution; `reviewer_policy`; the event/emit/reducer
  machinery; `attest`'s existing code-review coverage check.
- Changed: `reducer.py` (retain latest code-review `verdict` on `ChangeState`,
  small, mirrors `scope` retention); `cli/attest.py` + its verify core (skip
  classification).
- Unchanged: `transitions.py` (loop already supported), `PreToolUseGate` (order
  only).

## 7. Decision: review is NOT a daemon Sensor (resolving the Axiom-4 tension)

The codebase carries two answers to "is review a sensor": the `Sensor` base has
`determinism = "inferential"` + a `reviewer_strategy()` hook, and `sensors.yaml`
examples list `plan-reviewer` as a builtin — yet the 2026-06-02 HG-02 reframing
consciously decided **not** to build an in-harness reviewer sensor (because that
sensor would have to run an LLM in the daemon = harness spawning an agent).

**Decision: the reframing is correct.** We do **not** build a reviewer `Sensor`
subclass. VISION Axiom 4's "unified review interface" is honored by the
`prepare` + structured-verdict contract living on the **review CLI verbs**, not a
daemon sensor. The `plan-reviewer` builtin-sensor slot in `sensors.yaml`
examples is **aspirational / unbuilt** and stays a v0.2 question; the example
should be annotated as such (doc-sync item, §9).

## 8. Testing (TDD)

Every new helper is TDD'd. Coverage:
- bundle assembly: diff ∩ scope, out-of-scope surfacing, checklist embedding,
  clean-tree precondition, digest stability + change-on-committed-diff-change +
  explicit base branch + empty-scope inert case.
- verdict schema: parse/validate, reject malformed, empty-findings-only-when-all-pass.
- emit validation (C): rejection paths — bare approve, incomplete checklist,
  missing bundle (prepare not run), stale digest, git-error fail-closed; plus
  happy path.
- open-findings derivation (D): single reject, **multi-reject self-loop**,
  finding resolved-then-reopened; approve blocked while a finding is undisposed;
  approve allowed once all disposed; `wontfix` requires note.
- skip-override (E): bare skip → attest verify blocker; `skip --override` →
  attest pass-with-disclosure; override surfaced in attestation snapshot.
- checklist resolution: config present / absent / corrupt → default precedence.

## 9. CLI surface + doc sync (in scope)

- New verb `review prepare`, new `--verdict-file` options, `skip --override`/
  `--reason`, `approve --base` → regenerate `docs/cli-reference.md`.
- Update `AGENTS.md` review-protocol section: the agent now runs `review
  prepare`, reviews the bundle, records a `--verdict-file` verdict; bare approve
  no longer works for `code-reviewer`; skip blocks at merge unless `--override`.
- Annotate the `sensors.yaml` `plan-reviewer` example as v0.2 / unbuilt (§7).

## 10. Self-host bootstrap (the change validates itself with the new rules)

This change is dogfooded through its own lifecycle, and it changes the meaning of
the very verbs that lifecycle uses — so the bootstrap deserves explicit care.

- **C applies to this PR itself.** Once `review approve --reviewer code-reviewer`
  requires a `--verdict-file` (§4.C), the project's normal self-host flow — which
  has always done *bare* approves (verified: every committed attestation,
  including the most recent, records `code_review_passed` with just
  `{reviewer, reason}` and no verdict) — no longer works for this change. The
  author must produce a **genuine code-reviewer verdict-file for this change**
  (a real bundle via `review prepare`, real checklist coverage) before
  `attest write`. This is intended, not a workaround.
- **Old merged attestations are NOT retroactively broken.** `attest verify`
  checks milestone *presence* (a `code_review_passed` exists for each covered
  subject), not verdict *shape* — C is emit-time only. So previously-merged
  bare-approve attestations remain valid; there is no retroactive gate failure.
  State this in the plan so no one fears a sweep.
- **E's attest-verify change — exercise on a throwaway.** The new terminal-skip
  classification is the part of `attest verify` this change edits; test it on a
  throwaway change (bare skip → blocker; `--override` → pass-with-disclosure)
  rather than on this change's own attestation.
- `plan ready --scope` must cover every changed file (src + tests + docs +
  AGENTS.md + this design doc + the implementation plan(s)). (See
  `project-self-host-pr-attest-scope`.)

## 11. Deferred / open items (recorded, not dropped)

- Hard emit-time teeth on `plan-reviewer` — this slice keeps the plan boundary
  as hollow as before (only the code-review boundary is hardened). Stated plainly
  so no one reads this as "review fully hardened."
- Anchors in the review bundle — no per-change anchor data source; deferred to
  the anchors line.
- HG-12 reviewer independence (cross-actor, un-forgeable) — bedrock ceiling, v0.2.
- Harness-run LLM reviewer (`claude -p` CI fallback) — v0.2.
- Multi-stage reviewer pipeline — v0.2 (VISION).
- Reviewer `Sensor` subclass / `sensors.yaml` slot — abandoned for now (§7).
```
