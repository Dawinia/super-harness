# Design: Layer-2 CI merge gate (attestation-based lifecycle verifier)

Date: 2026-06-03
Status: approved (brainstorm), pending implementation plan
Tracks: HG-DF item **C** (the binding, agent-agnostic enforcement layer)

> NOTE: this file deliberately carries NO `change:` / `stage:` frontmatter — it
> must not be auto-picked-up by the SuperpowersAdapter scan before its change is
> formally declared.

> Revision history: v2 (2026-06-03) reworked after an independent design review
> (NEEDS-REWORK verdict). Corrected the gitignore mechanism, narrowed the
> security claims, and pinned diff/scope/path edge cases. See §10.

## 1. Problem

The first full gated-lifecycle dogfood run (PR #35) produced an honest finding,
recorded as HG-DF: under single-agent self-hosting the **edit-time** PreToolUse
gate is both **leaky** (Bash `cat >` / `tee` / `sed -i` / `python -c open(w)`
bypass it entirely — it only intercepts Edit/Write/MultiEdit/NotebookEdit) and
**self-signed** (the implementer emits its own `review approve`). What the gate
deterministically delivers today is "some step was *recorded*", not "some step
was *actually done*". The genuinely binding enforcement — one the author cannot
bypass locally and that is agent-agnostic — is a **Layer-2 CI merge gate**, and
it has not been built. This design is the first bounded slice of it.

### The central tension (why this is hard)

`.harness/events.jsonl` is gitignored (it is one explicit entry in init's
managed-ignore allowlist — see §3), so a CI checkout **does not have it**. The
existing machinery that looks like a merge gate — `pr validate` (`cli/pr.py`) —
runs three lifecycle checks (`find_ordering_violations`, `derive_state ==
READY_TO_MERGE`, metadata-block completeness) but **all three read the absent
`events.jsonl`** (`cli/pr.py:142,147`), so in CI they are either vacuously-true
(`find_ordering_violations` on a missing file returns `[]`) or perpetually false
(`derive_state` on a missing file finds no change → "not READY_TO_MERGE"). The
PR-body metadata block (`build_metadata`, `pr_metadata.py:264`) is also derived
from that same absent file in CI and does not carry the full event sequence. So
the verifier doesn't lack logic — it lacks an **evidence source CI can read**.

## 2. What this slice does and does NOT prove (read this before §4)

**Proves (the binding guarantee):** for every changed file in a PR, there
*exists* a committed attestation that (a) declares that file in its scope, and
(b) encodes a **complete, correctly-ordered** lifecycle that reaches
`READY_TO_MERGE` including a genuine `code_review_passed` milestone. If any
changed file lacks such covering evidence, the merge is blocked — in CI,
agent-agnostically, where the author cannot locally bypass it.

**Does NOT prove (explicit non-goals / deferred):**
- That the file was actually *edited through the gated path*. There is no signal
  binding a committed file's bytes to a gated edit. An actor who bypasses the
  editor (Bash heredoc) **and also runs a trivial covering lifecycle** (`plan
  ready --scope '[that-file]'` → approve → … → `attest write`) will PASS. The
  edit↔lifecycle binding is part of deferred forgery-resistance.
- That the recorded review actually happened or was correct (self-signed) →
  **HG-DF item B**.
- That the reviewer is a different actor than the author → **HG-12**.
- Tier-aware friction scaling (Micro changes skip plan-review) → **HG-DF item D**.

**So the honest bar this slice raises:** from *"merge anything, no record
required"* to *"merge requires a committed, complete, ordered, scope-covering
lifecycle attestation for every changed file."* It catches the realistic common
case — a file changed with the lifecycle **skipped entirely** (accidental Bash
bypass, lazy "just commit it", any non-Claude agent with no edit-time hook). It
does not catch an actor who fully fabricates a covering lifecycle; that actor is
the deferred forgery/cross-actor problem, and the committed attestation is
precisely the substrate HG-12 signing will later attach identity to.

## 3. Evidence model — committed per-change attestation

Decision (Q1): commit a **purpose-built per-change attestation**, not the
volatile, all-changes-interleaved `events.jsonl`.

- **What**: at `attest write` time, extract every event line for `change_id ==
  <slug>` from `.harness/events.jsonl` and write them **verbatim** (raw JSONL) to
  the committed path `.harness/attestations/<slug>.jsonl`.
- **Why raw JSONL (not a curated summary)**: the verifier reuses
  `find_ordering_violations` and `derive_state` **unchanged** (both take a file
  path and parse event lines) — zero new parsing/validation surface.
- **No gitignore change needed.** Init's managed block (`gitignore_injector.py`
  `_CANONICAL_PATHS`) is an **allowlist of specific runtime paths**
  (`state.yaml`, `events.jsonl`, `sensor-results/`, …) — NOT a blanket
  `.harness/` ignore. `.harness/attestations/` is therefore **already
  committable** (verified: `git check-ignore .harness/attestations/x.jsonl` →
  not ignored). The earlier draft's "exempt attestations from a broad ignore"
  was wrong; there is nothing to exempt. (Implementation note: the plan must
  still confirm no `_CANONICAL_PATHS` glob accidentally matches the dir.)
- **Why committed (vs events.jsonl)**: events.jsonl is ignored for purely
  *operational* reasons (machine-local, continuously appended, all changes
  interleaved → merge conflicts + noise), not security. The attestation is the
  per-change view the codebase's consumption layer *already assumes* (every
  reader filters by `change_id`) but the storage layer doesn't materialize. One
  file per change → no cross-change merge conflicts; frozen at attest-time →
  stable; in the PR diff → reviewer-readable provenance.

Side benefits (independent of the gate): durable, shared, cross-clone lifecycle
record (events.jsonl is per-machine and lost on clone/wipe); lightweight PR-diff
provenance (partial HG-06); post-hoc tamper-evidence via git history; the
substrate HG-12 signing attaches to.

## 4. Commands (new `attest` group; ratified `gate check` untouched)

`gate check pr-merge` is a state.yaml-driven in-process Gate-class model
(`cli/gate.py:156`) — a different input model from this diff-driven pure-git
verifier. We do **not** overload it (its "not yet implemented" message may
optionally point at `attest verify`).

### 4.1 `super-harness attest write <slug>` (writer, idempotent)
- Resolve workspace root (walk-up; missing `.harness/` → EXIT_NO_CONFIG).
- Read `.harness/events.jsonl`; select lines with `change_id == <slug>` in append
  order; write verbatim to `.harness/attestations/<slug>.jsonl` (parent mkdir;
  overwrite — idempotent).
- **Absent/empty events.jsonl, or zero lines for the slug → ERROR (non-zero
  exit), not a silent empty attestation.**
- Stored `scope.files` (already produced upstream by `plan ready --scope`) must
  be in the canonical form §4.3 defines.
- Run by the agent **after** `review approve --reviewer code-reviewer` (i.e.
  after `code_review_passed`, in `READY_TO_MERGE`) and **before** opening the PR.
  NOT folded into `done` (at `done` time `code_review_passed` hasn't happened, so
  the snapshot would be incomplete). Re-run after any reject→re-pass.
- Lifecycle verbs (including this) go through Bash → not subject to the edit-time
  gate (consistent with the existing workflow).

### 4.2 `super-harness attest verify --base <ref> --head <ref>` (CI merge gate)

1. Compute the diff with **status**: `git -c core.quotePath=false diff
   --name-status <base>...<head>` (three-dot = changes introduced by head vs the
   merge-base). **Any git failure (unreachable merge-base, shallow fetch, etc.)
   → non-zero exit (FAIL-CLOSED), never a vacuous pass.** Parse detail: `A`/`M`/`D`
   lines carry ONE path column; rename/copy lines carry a `R<score>`/`C<score>`
   status token plus TWO tab-separated path columns (old, new) — both are
   collected as subjects (split on tab, don't assume one path per line).
2. Normalize every changed path to the canonical form (§4.3). Collect the
   **subject set** = every path on either side of the diff (added / modified /
   deleted / both ends of a rename or copy), EXCEPT paths under
   `.harness/attestations/` (the only exempt class, §5). Deletions and renames
   are subjects too: removing or moving a tracked file is a real change that must
   be declared in some change's scope.
3. Identify the **attestation files** in the diff (paths under
   `.harness/attestations/`). **v0.1 hardening: an attestation file may only be
   ADDED, not modified.** A modification (status `M`) to an existing
   `.harness/attestations/*.jsonl` → FAIL (closes the "edit an existing trusted
   attestation to fabricate" vector; a single change == single PR under the
   branch==slug convention, so new-only is sufficient for v0.1; documented
   limitation in §10 for multi-PR changes).
4. For **each** attestation file in the diff, derive `slug` from the filename and
   require ALL of (the binding check is evaluated **first / short-circuit** so a
   slug-mismatched file FAILs cleanly rather than raising `KeyError` when
   `derive_state(file)[slug]` is indexed — equivalently use
   `derive_state(file).get(slug)` and treat `None` as FAIL):
   - every event line in the file has `change_id == slug` (filename↔content
     binding; mismatch → FAIL);
   - `find_ordering_violations(file, slug) == []` (no out-of-order / no
     missing-hard-prereq — the strict integrity check that *reports* rather than
     silently skipping);
   - `derive_state(file)[slug].current_state == "READY_TO_MERGE"`;
   - milestone event types all present: `{plan_approved, implementation_complete,
     code_review_passed}` (closes the `implementation_withdrawn` shortcut at
     `transitions.py:48` that can reach READY_TO_MERGE without a real review).
   **Any attestation failing any check → overall FAIL** (fail-closed; a bad
   attestation is never silently excluded — resolves the prior step-3/step-4
   ambiguity).
5. `covered = ∪ derive_state(file)[slug].scope.get("files", [])` over all
   attestations, normalized to canonical form. **An attestation whose
   `plan_ready` carried no `--scope` has `scope == {}` → contributes nothing to
   `covered`** (so any file it claims to cover fails — declaring scope is
   mandatory for coverage).
6. **v0.1 hardening: each added attestation's `covered` set must intersect the
   subject set** (an attestation that covers nothing in this diff is a stale /
   forward-planted artifact → FAIL). Documented limitation in §10.
7. Verdict:
   - every subject file ∈ `covered` → **PASS**;
   - any subject file ∉ `covered` → **FAIL** (catches BOTH "changed file with no
     covering attestation" AND "scope drift — touched a file the plan never
     declared"). Over-declaring (a scope file not changed) is not penalized,
     subject to the §4.2.6 intersection check.

Exit non-zero on FAIL → CI job fails → a branch-protection required check blocks
merge. JSON envelope under `--json` lists per-file classification + blockers,
mirroring `pr validate`'s output contract.

### 4.3 Path canonicalization (correctness-critical)
Both `git diff` output and stored `scope.files` are normalized to: **repo-root-
relative, POSIX separators, no leading `./`, no `..`**. `git` is invoked with
`-c core.quotePath=false` so non-ASCII paths are not octal-quoted. `attest write`
and `plan ready --scope` store paths in this same canonical form. Symlink
*targets* are out of scope (the link path itself is still a subject); §10.

## 5. Exemption policy: STRICT

Only `.harness/attestations/` is exempt (the evidence itself — requiring an
attestation to cover the attestation is circular). Everything else tracked —
`docs/**`, `README.md`, `AGENTS.md`, generated files (`docs/cli-reference.md`),
`.github/**` — is **subject** and must be in some change's declared scope.
Rationale: self-hosting was motivated by "if we don't self-host, things drift
(README doc-drift)"; carving out docs reopens exactly that hole. The friction of
a one-line doc fix needing a lifecycle is acknowledged and deferred to tier-aware
scaling (HG-DF D), not solved by opening an exemption hole now. (`private/**` is
gitignored → never in a diff → no special case.)

## 6. CI wiring

- Add an `attest-verify` job to the bundled template
  `src/super_harness/templates/super_harness_workflow.yml`: `on: pull_request`,
  `actions/checkout@v4` with `fetch-depth: 0`, base/head from the PR event SHAs
  (`github.event.pull_request.base.sha` / `.head.sha`), run `super-harness attest
  verify --base <base> --head <head>`. Update the template injection-guard unit
  test (`tests/unit/templates/test_super_harness_workflow.py`).
- Add the same job to **this repo's own** `.github/workflows/` so the gate is a
  live check on super-harness itself.
- Marking it a *required* status check is a manual repo-settings action (like the
  existing test/lint checks). The first green run is on a PR where the check is
  not yet required → no deadlock.

## 7. Reuse / non-goals

- **Reuse, unchanged**: `find_ordering_violations`, `derive_state`,
  `ChangeState.scope`, `parse_event_line`. No new lifecycle/validation logic.
- **Not touched**: the `events.jsonl` single-stream storage model (per-change
  sharding is a separate v0.2 architecture question — if ever adopted, the
  attestation collapses into "commit the change's event file"); the ratified
  `gate check` family; `pr validate` (PR-body-driven, gh-dependent — coexists).

## 8. Dogfood / verification (distinguishing ritual from value)

- **The load-bearing test (real verification, not ritual)**: construct a branch
  where a source file is written via Bash heredoc (bypassing the edit-time gate)
  with NO covering attestation, then run `attest verify --base main --head HEAD`
  and assert it **FAILs** (exit ≠ 0). This proves a changed file with no covering
  lifecycle is blocked at merge. Per the don't-conflate-ritual-with-value lesson,
  the test is framed precisely as **"missing coverage fails"**, NOT "bypass is
  impossible" (see §2 — an actor who also runs a trivial covering lifecycle
  passes; that is the deferred forgery case).
- **Unit matrix**: covered subject → PASS; uncovered (bypass) → FAIL; attestation
  not READY_TO_MERGE → FAIL; out-of-order attestation → FAIL;
  `implementation_withdrawn` shortcut (no `code_review_passed`) → FAIL; scope
  drift → FAIL; empty-scope attestation covers nothing → FAIL; deletion not in
  scope → FAIL; rename new-path not in scope → FAIL; filename↔change_id mismatch
  → FAIL; modified (not added) existing attestation → FAIL; attestation-only diff
  with no subject files → FAIL (covers nothing in the diff, §4.2.6); git/merge-base
  error → FAIL-closed; path spelled `./src/x` vs `src/x` → still matches after
  canonicalization.
- **Self-referential dogfood**: the `attest-verify` job this PR adds runs on this
  PR. Its diff includes `src/`, tests, this design doc
  (`docs/plans/2026-06-03-...md`), `docs/cli-reference.md`, the workflow file,
  AGENTS.md (if re-synced), plus this change's own attestation. Under STRICT
  (§5), **every** non-attestation changed file must be in this change's
  `scope.files` — including the design doc and the generated cli-reference. The
  author must enumerate them all in `plan ready --scope`. The gate going green on
  its own introducing PR is the end-to-end self-proof. (The gate reads the
  cumulative `base...head` diff, so intermediate commit order is irrelevant.)

## 9. Deferred items to register (OPEN-ITEMS + HG-DF status update)

- HG-12: cross-actor reviewer identity / CI-run review / attestation signing
  (the edit↔lifecycle binding and fabrication-resistance live here).
- HG-DF D: tier-aware friction scaling.
- v0.2 architecture: per-change event-store sharding evaluation.

## 10. Known limitations (documented, not solved in this slice)

- **Fabricated covering lifecycle** (§2): an actor who bypasses the editor but
  runs a trivial complete lifecycle declaring the file in scope passes. Deferred
  to HG-12/B forgery-resistance.
- **Multi-PR changes vs new-only attestations** (§4.2.3): a change spanning
  multiple PRs cannot update its attestation under the v0.1 "added, not modified"
  rule. Acceptable under the branch==slug / one-PR-per-change convention; revisit
  if multi-PR changes become real.
- **Symlinks** (§4.3): the link path is a subject, but its target is not
  inspected.
- **Stale/forward attestation** (§4.2.6 mitigates the in-diff case): an
  attestation already merged on main with a broad scope could, combined with a
  future bypass, "cover" files — but only files it explicitly listed; the
  intersection check blocks planting one in the bypass PR itself.
