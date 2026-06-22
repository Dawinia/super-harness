# Design: Arm the decision teeth — first genuine bite on our own decisions

> **Status:** design (brainstormed 2026-06-23). Next: writing-plans → TDD/dogfood.

## 0. Where this sits (read first)

Over PRs #38→#44 the conformance arm built the full strength ladder for decision
teeth: referential integrity (#38) → text-lock (#40) → executable tier-1 checks
(#41) → AGENTS.md/sync conformance (#43) → tier-2 reviewable anchors (#44). The
capability arm has **converged** (zero reworks since the 2026-06-10 rebuild, only
hardening).

But a fact in `private/CAPABILITY-CONVERGENCE-LEDGER.md` exposes the gap this slice
closes: the repo holds **9 ratified decisions and every one is tier-3 lazy-warn —
tier-1=0 / tier-2=0 / text-locked=0**. The teeth have **never bitten on our own
work**. The merge gate has never blocked a substantive error across 5 PRs. We built
the teeth and never armed them.

**This slice arms 3 of those decisions so the conformance teeth draw first blood.**

### What this slice is NOT (positioning — settled in brainstorm)

- **Not a CLI feature.** Zero new mechanism code ships. `pip install super-harness`
  users get an identical product. The teeth (tier-1/2, text-lock, bite-test, CI gate)
  already exist and are live.
- **Not a test of the CLI.** `tests/` is untouched. No unit/integration tests added.
- **Not an example/demo.** These are **this repo's real governance records** in
  `docs/decisions/`, constraining **real source** (`gh.py`, `attest.py`, `reducer.py`).
  (the anchored files are `engineering/gh.py`, `cli/attest.py`, `core/reducer.py`).
  Their anchors point at real `src/`; CI scans the repo root. Moving them to
  `examples/` would kill the arming (anchors detach from real code; CI stops seeing
  them). dogfood ≠ demo.

It **is** a **self-host / dogfood** change: the product biting its own repo. The
beneficiary is this repo's health and **future contributors** (whoever later edits
`gh.py` to add raw REST, `attest.py` to add network, or `reducer.py` to break the
fold will be blocked at merge) — not the CLI's end users.

### Why a non-shipping change is worth doing — the value is a *direction decision*

Before investing in the more expensive next steps (the sedimentation arm, the
AI-reviewer loop), this is **the cheapest possible front-test of "are the teeth we
already built actually useful?"** Arming a tier-1 decision **is** the first genuine
bite by construction: ratify runs the bite-test, which must make the check
**demonstrably fail** on a real counterexample before it accepts. The slice's real
product is **a judgment about where super-harness should go next**, carried on the
repo's own decision records.

## 1. Scope — arm exactly 3, both arms draw blood

| Decision | Tier | Why this tier |
|---|---|---|
| `d-gh-cli-not-rest` | **1** (executable) | A crisp, greppable invariant exists (no raw-REST host literal) |
| `d-merge-gate-pure-git` | **1** (executable) | Gate modules are network-free; a scoped grep proves it |
| `d-state-pure-fold` | **2** (reviewable) | Purity is not cleanly greppable; honest fit for a review anchor |

2 tier-1 + 1 tier-2 → **both arms (executable + reviewable) get a first bite**,
focused enough to learn before extending. All 3 are already singly-anchored in
`src/` (verified), so no new anchors are needed.

## 2. `d-gh-cli-not-rest` → tier-1

**Decision text (unchanged):** "GitHub access goes through the gh CLI, never raw REST."

**The semantic trap (this is the crux of writing a non-false-positive check):** the
anchored code at `src/super_harness/engineering/gh.py:313` literally calls
`gh api -X PATCH /repos/{owner}/{repo}`.
That is **REST *through* gh — which is allowed.** "Raw REST" means bypassing gh to
hit GitHub's API host directly (e.g. `requests.get("https://api.github.com/...")`).
The faithful mechanical signature of *bypassing gh* is **naming the API host**;
`gh api` only ever uses relative `/repos/...` paths and never the host.

**Check (exit 0 = satisfied):**
```
! grep -rn 'api\.github\.com' src/
```
- **Pass side (real tree, repo root):** verified clean today — `grep -rn
  'api.github.com' src/` returns nothing (the only github URLs in src are
  `cli.github.com` install hints, not the API host). Pass side passes.
- **No pass-side pollution:** the inline counterexample lives in
  `docs/decisions/d-gh-cli-not-rest.md` (under `docs/`, outside `src/`), so the
  `src/`-scoped grep never reads it. The §4.2 over-wide trap does not fire here.

**Counterexample** (a *new* file — full src is scanned, so a new path is seen):
````
```counterexample path=src/super_harness/_ce_raw_rest.py
import requests
requests.get("https://api.github.com/repos/owner/repo")
```
````
- **Bite side (sandbox):** `build_sandbox` copies the in-scope tree, then writes this
  new file under `src/`. The grep finds `api.github.com` → grep exits 0 → `!` inverts
  → check exits non-zero → **not satisfied → it bites.**

**Honest limit:** this catches the host literal, not a host assembled from a
variable / `base_url` concatenation. That is the coarse-by-construction tradeoff
(record in ledger). The check forces the *common, honest* violation to surface; it is
not a sandbox-proof guarantee against a determined bypass.

## 3. `d-merge-gate-pure-git` → tier-1

**Decision text (unchanged):** "The merge gate verifies committed evidence with pure
git — no network, no runtime trust."

**The gate's actual modules (verified by tracing `attest verify`):** `cli/attest.py`
(uses `subprocess` only for `git diff`; `attest_verify` calls `_git_name_status` here)
+ `engineering/attestation.py` (pure; `attest_verify` calls `parse_name_status` /
`verify_attestations` / `independence_for_attestation` / `write_attestation` here;
imports `json`/`posixpath`/`pathlib` + core modules; zero network). Both are
network-clean today. **Record this dependency trace in the plan** so a future reader
can re-verify the scope hasn't silently grown — nothing guards the check's *own* scope
against drift (e.g. if verify logic later moves into a new `engineering/diff.py`, the
check becomes unfaithfully narrow and the suspect invariant won't catch it).

**Scope is the whole game here (the §4.2 false-positive tension):** the check pins its
scan to the gate's two actual modules. The primary reason is **faithfulness** — those
two files *are* the merge gate; `gates/` is the PreToolUse policy, a different thing
(verified: `grep gates` in both scoped files → no match, so the gate doesn't reach into
`gates/`). Pinning scope also keeps the §4.2 over-wide trap shut: `gates/decisions.py:11`
has the word "socket" *in a comment* ("never drags in the daemon, socket, or CLI
stacks"). The precise import/access regex below would NOT match that comment (verified —
this is why the regex is written tight, not bare-substring), but a naïve bare-substring
grep over `gates/` would have, and either way `gates/` simply isn't the gate, so it stays
out of scope.

**Check (exit 0 = satisfied)** — match *import / module-access* patterns, NOT bare
substrings, so prose like a "validates incoming requests" comment can't false-positive
the pass side:
```
! grep -rnE 'import +(urllib|requests|httpx|socket)|(urllib|requests|httpx|socket)\.[a-zA-Z_]|api\.github\.com' src/super_harness/cli/attest.py src/super_harness/engineering/attestation.py
```
- **Pass side:** both named files verified to match none of these patterns. Passes.
- The counterexample `import urllib.request` matches `import +urllib` → bites. The
  exact regex is finalized + TDD'd in the plan; the design constraint is **precision
  over bare substrings**.

**Counterexample** (path = the anchored gate file, **overwritten** — because a grep
scoped to specific files only sees content *at* those paths):
````
```counterexample path=src/super_harness/cli/attest.py
import urllib.request  # raw network smuggled into the merge gate
```
````
- **Bite side:** `build_sandbox` overwrites `attest.py` in the sandbox with this
  content; the scoped grep finds `urllib` → check exits non-zero → **it bites.**

**Honest limit (both directions):** pattern-based. *Under-match* — a network call via
an indirect helper or a renamed import could slip the pattern list. *Over-match* — the
import/access patterns are tighter than bare substrings but still not semantic; an
exotic future construct could evade, or a deliberately adversarial token in those exact
two files could trip it. Coarse-by-construction; record in ledger.

## 4. `d-state-pure-fold` → tier-2 (the honest non-tier-1 case)

**Decision text (unchanged):** "State is a pure left-fold over the event log; never
mutated in place."

**Why not tier-1 (honest exit):** purity / append-only are **not cleanly greppable.**
`reducer.py` has no single-token signature for "pure fold"; the only tier-1 phrasing
would be "run the whole property test suite as a check" — which hits the 30s timeout
and is a behavioral test, not a structural invariant. Forcing a hollow/weak check here
is exactly what anti-hollow rejects. **tier-2 reviewable is the honest fit:** a
human-reviewable acceptance criterion + a reconcile baseline, so any change to
`reducer.py` routes a recorded re-review at the merge boundary.

**`review` block (becomes `acceptance`):**
````
```review
reducer.derive_state is a pure left-fold over the event log: it constructs and
returns a fresh state and never mutates its inputs or any module-level state in
place. On any change to reducer.py, re-review the anchored fold: confirm no in-place
mutation of the accumulator or inputs was introduced and that it stays referentially
transparent (same events → same state). Then `decision reconcile d-state-pure-fold`.
```
````
- Anchor already at `reducer.py:41`.
- **Sequencing:** (1) re-ratify (locks the body hash, now including the review block);
  (2) `decision reconcile d-state-pure-fold` to set the baseline fingerprint of
  `reducer.py`. Both are required — without reconcile it reports `unreconciled_tier2`.
- tier-2 has no bite-test at ratify (no check). Its "bite" is the standing suspect
  invariant: touch `reducer.py` after baseline → suspect fires → `--gate-reconcile`
  blocks at merge.

## 5. The 6 we do NOT arm (honest outcomes, recorded as findings)

Not arming is a result, not a failure — forcing weak/over-wide checks would create
hollow teeth. Record each in the ledger.

- **`d-events-append-only`** — `events.py` has **no direct `open()`** (writing is
  abstracted elsewhere), so append-only is not statically greppable in the anchored
  module. Honest future tier-2 (deferred), not this slice (keeps tier-2 to one
  representative; a second adds little).
- **`d-dangling-check`, `d-decision-records`, `d-fixed-transition-matrix`,
  `d-identity-resolution-order`, `d-single-gate-policy`** — each describes a *behavior*
  rather than a brittle one-token invariant; the cheapest honest check would be weak or
  over-wide. Left tier-3 lazy-warn. (The pre-text-lock lazy-warn is the expected state
  per OPEN-ITEMS SLICE-4, not a defect.)

## 6. re-ratify: accept the overwrite (no body provenance note)

Re-ratify re-stamps `ratified_by` (dawinialo@163.com → current identity) and
`ratified_at` (6/08–6/10 → now). **We accept this**, with no hand-written "originally
ratified by…" line in the body. Rationale: re-stamping identity **is** re-ratify's
designed semantics ("I, now, re-attest this holds"); the original attribution is
preserved losslessly in git history. A body provenance line would be a per-decision
one-off patch, not a mechanism, and would clutter records against the "each verb does
one thing" discipline. git history is the audit trail.

## 7. First blood — how the bite is recorded

- **By construction:** each tier-1 ratify runs the two-sided bite-test, which must
  make the check **demonstrably fail** on its counterexample before accepting. That
  failure *is* the first genuine bite. Captured in the ratify output + the slice notes.
- **Live tripwire demo (in dogfood):** temporarily plant a raw-REST call in `src/`,
  run `decision check`, observe **exit 2** (the armed check blocks a real violation on
  the real tree), then revert. Proves the armed tooth bites outside the ratify sandbox
  too. (A real regression caught in CI is left to a future PR — this slice plants the
  tripwire and draws the construction-time + live-demo first bite.)
- Arming count moves **0 → 3** (tier-1=2, tier-2=1, text-locked=3).

## 8. First-class secondary output — maintenance-friction & closed-loop gap

This slice's brainstorm surfaced a sharper product question than "arm a few more": is
arming supposed to be a *separate, user-initiated session* (like this one), or should
it **close its loop inside ordinary feature-development lifecycles**? The latter is the
right steady state. Today only the **merge boundary** is closed (CI
`decision check --gate-reconcile` blocks drift/violation/dangling at merge). The
**proactive** in-development loops are unbuilt:

| Closed-loop point | Status |
|---|---|
| merge boundary (CI gate) | ✅ built (passive enforcement) |
| edit-time reminder (touched anchored code → prompt reconcile) | 🔴 unbuilt (deferred PreToolUse feedforward) |
| decision "birth" prompt (a recordable decision arose in this PR) | 🔴 unbuilt (relies on human/agent initiative) |
| check-drafting assist | 🔴 unbuilt (agent hand-writes) |

This session is **historical-debt cleanup** (these 9 were ratified *before* the teeth
existed), which is by-nature a one-off flow — it does **not** imply arming is always a
separate session. But **you cannot close the loop on an unarmed decision**: arming is
"plant the tripwire", the loop is "the tripwire fires inside the feature flow". This
slice lays the closed-loop foundation on these 3 (future edits to the 3 files will be
caught in their feature PR's merge gate).

**Therefore this slice adds a first-class output beyond "3 armed, all green":** record,
per decision, **how much agent effort arming took and which step was the most
friction** (e.g. probing false-positives, pinning scope). **Artifact + honesty:** this
lands as a short prose block in `private/CAPABILITY-CONVERGENCE-LEDGER.md` (the slice's
ledger row), explicitly framed as an **n=3 single-author anecdote, not data** — a
qualitative signal on maintenance-model viability, not a measurement. Maintenance cost
is one of super-harness's core
**unvalidated assumptions** — if we (highest-motivation, tool-authors) find arming
heavy, external users will find it heavier, which is a stronger "rethink the product
shape" signal than "keep deepening the teeth." Deliberately **not** building the
closed-loop mechanisms now: they are real CLI features (more expensive), and building
them before measuring friction risks gilding the wrong step. The friction data decides
how/whether to build them next.

## 9. What this slice does NOT touch

- **CI:** `decision-check.yml:21` already runs `decision check --gate-reconcile` (full,
  both arms). No workflow change.
- **`src/`:** no mechanism code. No new helper anticipated (revisit only if dogfood
  reveals one genuinely needed — then TDD it).
- **`tests/`:** untouched.

## 10. Deferred — register in OPEN-ITEMS SLICE-4

- Arm `d-events-append-only` as tier-2 once region-level / abstracted-write handling
  makes the criterion meaningful.
- The closed-loop mechanisms (edit-time reminder, decision-birth prompt, check-drafting
  assist) — sequence/scope to be decided by §8's friction data.
- Coarse-check honest limits (host-via-variable for gh; renamed-import for merge-gate)
  ride the region-level / semantic-check upgrade, if ever.

## 11. One-line summary

Arm 3 of our own ratified decisions — 2 tier-1 (gh-no-raw-REST, gate-pure-git) + 1
tier-2 (state-pure-fold) — so the conformance teeth bite on our own work for the first
time (count 0→3), and record the arming friction + closed-loop gap as the cheapest
front-test of whether the teeth are worth deepening further.
