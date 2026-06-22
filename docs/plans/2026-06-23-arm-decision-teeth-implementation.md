# Arm Decision Teeth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Arm 3 of this repo's own ratified decisions with mechanical teeth — 2 tier-1 (executable check + counterexample) + 1 tier-2 (reviewable acceptance + reconcile baseline) — so the conformance teeth bite on our own work for the first time (arming count 0→3), and record the arming friction + closed-loop gap.

**Architecture:** This is a **dogfood / self-host** slice. NO new mechanism code, NO `src/` changes, NO `tests/` changes, NO CI changes. The work is: author inline ` ```check ``` / ` ```counterexample ``` / ` ```review ``` blocks into 3 existing `docs/decisions/*.md` bodies, then drive them through the already-built CLI (`decision ratify` → bite-test + text-lock; `decision reconcile` → tier-2 baseline). Verification is the bite-test itself (`ratify --dry-run`), a live tripwire demo, and a green `decision check --gate-reconcile`. The whole change ships through the self-host merge gate.

**Tech Stack:** The installed `super-harness` CLI (run via `PATH="$(pwd)/.venv/bin:$PATH" super-harness …`), the existing `decision` subgroup, plain `git`.

**Design ref:** `docs/plans/2026-06-23-arm-decision-teeth-design.md` (2 adversarial review rounds; both tier-1 bite-tests verified to bite end-to-end against the real tree).

---

## Conventions for every command in this plan

- Always prefix the CLI with the project venv: `PATH="$(pwd)/.venv/bin:$PATH"` from the repo root. Never `uv run` inside the project (clobbers `.venv` dev deps).
- `private/` is gitignored — append to it with Bash heredoc/`>>`, never the Edit tool (Edit trips the out-of-scope gate; private files never enter git).
- The branch `2026-06-23-arm-decision-teeth` already exists and is checked out.
- The two counterexamples live **inline in the decision `.md` bodies**; no counterexample file is ever written to the real `src/` tree (the sandbox injects them at bite-test time only).

---

## Task 0: Open the self-host lifecycle (BEFORE any edits)

The merge gate requires the change be declared and its plan approved *before* implementation. Commit the planning docs first so they are in the branch, then open the change.

**Files:**
- Commit: `docs/plans/2026-06-23-arm-decision-teeth-design.md`, `docs/plans/2026-06-23-arm-decision-teeth-implementation.md`

- [ ] **Step 1: Commit the design + plan docs**

```bash
git add docs/plans/2026-06-23-arm-decision-teeth-design.md docs/plans/2026-06-23-arm-decision-teeth-implementation.md
git commit -m "docs(plan): design + implementation plan for arming decision teeth

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Declare the change (intent)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness change start 2026-06-23-arm-decision-teeth`
Expected: emits `intent_declared`; state → `INTENT_DECLARED`.

- [ ] **Step 3: Declare the plan scope (all 5 files this branch changes)**

`--scope` takes a **single value parsed as a YAML list** (not space-separated paths). Run:
```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness plan ready 2026-06-23-arm-decision-teeth --scope \
'[docs/decisions/d-gh-cli-not-rest.md, docs/decisions/d-merge-gate-pure-git.md, docs/decisions/d-state-pure-fold.md, docs/plans/2026-06-23-arm-decision-teeth-design.md, docs/plans/2026-06-23-arm-decision-teeth-implementation.md]'
```
Expected: emits `plan_ready`; state → `AWAITING_PLAN_REVIEW`. (No `src/`, `tests/`, CI, or `docs/cli-reference.md` — CLI surface is untouched, so no derived-doc regeneration.)

- [ ] **Step 4: Approve the plan (plan-reviewer)**

The role is `--reviewer` (required; `--as` is the optional identity string). Run:
`PATH="$(pwd)/.venv/bin:$PATH" super-harness review approve 2026-06-23-arm-decision-teeth --reviewer plan-reviewer`
Expected: emits `plan_approved`; state → `PLAN_APPROVED`. (If a missed file surfaces later, the only supported path to revise scope post-approval is the bounded re-root `plan_redeclared` — see OPEN-ITEMS HARNESS GAP; avoid by getting scope right here.)

- [ ] **Step 5: Start implementation**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness implementation start 2026-06-23-arm-decision-teeth`
Expected: emits `implementation_started`; state → `IMPLEMENTATION_IN_PROGRESS`.

---

## Task 1: Arm `d-gh-cli-not-rest` (tier-1)

**Files:**
- Modify: `docs/decisions/d-gh-cli-not-rest.md` (body only; `ratify` rewrites the frontmatter)

- [ ] **Step 1: Add the check + counterexample blocks to the body**

Replace the body line with the line plus two fenced blocks. Use Edit with:

old_string:
```
GitHub access goes through the gh CLI, never raw REST.
```
new_string:
````
GitHub access goes through the gh CLI, never raw REST.

`gh api /repos/...` (REST *through* gh, relative paths, never the host) is allowed.
"Raw REST" = bypassing gh to hit the API host directly. The faithful mechanical
signature of that bypass is naming the host `api.github.com` in source.

```check
! grep -rn 'api\.github\.com' src/
```

```counterexample path=src/super_harness/_ce_raw_rest.py
import requests
requests.get("https://api.github.com/repos/owner/repo")
```
````

- [ ] **Step 2: Run the bite-test only (dry-run) — this is the by-construction first bite**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-gh-cli-not-rest --dry-run`
Expected: prints `bite-test: bites` and exits 0. (Pass side: `grep -rn api.github.com src/` finds nothing on the real tree → check satisfied. Bite side: sandbox gets `src/super_harness/_ce_raw_rest.py` injected → grep finds the host → check fails → it bites.)

If instead it prints `BITE-TEST FAILED: check fails on current code …`, stop: something in `src/` now names `api.github.com`; investigate before proceeding.

- [ ] **Step 3: Ratify for real (re-runs bite-test, re-stamps identity, text-locks body)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-gh-cli-not-rest`
Expected: prints `bite-test: bites` then `ratified d-gh-cli-not-rest (by …)`. The file now carries a fresh `ratified_by`/`ratified_at` and a `ratified_text_hash` (the original 163.com attribution is preserved in git history — accepted per design §6).

- [ ] **Step 4: Confirm it is now tier-1 and clean**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness --json decision check 2>/dev/null | python -c "import json,sys; d=json.load(sys.stdin)['data']; print('hard:context', d['hard_context']); print('check_failures', d['check_failures']); print('integrity', d['integrity_violations'])"`
Expected: `hard` incremented by 1, `check_failures []`, `integrity []`.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/d-gh-cli-not-rest.md
git commit -m "feat(decision): arm d-gh-cli-not-rest as tier-1 (no raw-REST host literal in src)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Arm `d-merge-gate-pure-git` (tier-1)

**Files:**
- Modify: `docs/decisions/d-merge-gate-pure-git.md` (body only)

- [ ] **Step 1: Add the check + counterexample blocks to the body**

The check is pinned to the gate's two actual modules and matches *import / module-access* patterns (not bare substrings), so prose like a "incoming requests" comment cannot false-positive. Use Edit with:

old_string:
```
The merge gate verifies committed evidence with pure git — no network, no runtime trust.
```
new_string:
````
The merge gate verifies committed evidence with pure git — no network, no runtime trust.

The merge gate is `cli/attest.py` (subprocess only for `git diff`) + `engineering/attestation.py`
(pure). Both must stay free of network clients. The check scans exactly those two files —
`gates/` is the PreToolUse policy, not the merge gate, and stays out of scope.

```check
! grep -rnE 'import +(urllib|requests|httpx|socket)|(urllib|requests|httpx|socket)\.[a-zA-Z_]|api\.github\.com' src/super_harness/cli/attest.py src/super_harness/engineering/attestation.py
```

```counterexample path=src/super_harness/cli/attest.py
import urllib.request  # raw network smuggled into the merge gate
```
````

- [ ] **Step 2: Run the bite-test only (dry-run)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-merge-gate-pure-git --dry-run`
Expected: prints `bite-test: bites` and exits 0. (Pass side: the pattern matches nothing in the two real files. Bite side: the sandbox overwrites `cli/attest.py` with `import urllib.request …` → the scoped grep matches `import +urllib` → check fails → it bites.)

- [ ] **Step 3: Ratify for real**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-merge-gate-pure-git`
Expected: `bite-test: bites` then `ratified d-merge-gate-pure-git (by …)`.

- [ ] **Step 4: Confirm tier-1 + clean**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness --json decision check 2>/dev/null | python -c "import json,sys; d=json.load(sys.stdin)['data']; print('hard:context', d['hard_context']); print('check_failures', d['check_failures'])"`
Expected: `hard` now 2 higher than the start-of-slice baseline, `check_failures []`.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/d-merge-gate-pure-git.md
git commit -m "feat(decision): arm d-merge-gate-pure-git as tier-1 (gate modules network-free)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Arm `d-state-pure-fold` (tier-2)

Purity has no honest one-token grep signature, so this is a reviewable anchor: a body-hash-locked acceptance criterion + a reconcile baseline over the anchored `reducer.py`. Sequencing matters — **ratify first (locks the body hash incl. the review block), then reconcile (sets the baseline).**

**Files:**
- Modify: `docs/decisions/d-state-pure-fold.md` (body only)

- [ ] **Step 1: Add the review block to the body**

old_string:
```
State is a pure left-fold over the event log; never mutated in place.
```
new_string:
````
State is a pure left-fold over the event log; never mutated in place.

```review
reducer.derive_state is a pure left-fold over the event log: it constructs and
returns a fresh state and never mutates its inputs or any module-level state in
place. On any change to reducer.py, re-review the anchored fold: confirm no in-place
mutation of the accumulator or inputs was introduced and that it stays referentially
transparent (same events -> same state). Then `decision reconcile d-state-pure-fold`.
```
````

- [ ] **Step 2: Confirm dry-run reports tier-3-style "no check" (tier-2 has no bite-test)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-state-pure-fold --dry-run`
Expected: prints `no check block (tier-3 context) - nothing to bite-test` and exits 0. (The `--dry-run` branch only special-cases the check; a tier-2 review block carries no runnable check, so there is nothing to bite-test at ratify — its teeth are the standing suspect invariant, exercised in Task 4.)

- [ ] **Step 3: Ratify (stamps identity + text-locks the body including the review block)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-state-pure-fold`
Expected: `ratified d-state-pure-fold (by …)`.

- [ ] **Step 4: Verify it is now an unreconciled tier-2 (gate would block until reconciled)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness --json decision check 2>/dev/null | python -c "import json,sys; d=json.load(sys.stdin)['data']; print('unreconciled_tier2', d['unreconciled_tier2'])"`
Expected: `unreconciled_tier2 ['d-state-pure-fold']`. This is the tier-2 "no baseline yet" state — Step 5 clears it.

- [ ] **Step 5: Reconcile to set the baseline fingerprint of the anchored file**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision reconcile d-state-pure-fold --kind self --justification "Baseline: reducer.derive_state is a pure left-fold at arming time (initial dogfood baseline)."`
Expected: `reconciled d-state-pure-fold (1 file(s), kind=self, by …)`. The `reconciled_anchors` map now pins `src/super_harness/core/reducer.py`.

- [ ] **Step 6: Confirm no longer unreconciled / not suspect**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness --json decision check 2>/dev/null | python -c "import json,sys; d=json.load(sys.stdin)['data']; print('unreconciled', d['unreconciled_tier2']); print('suspect', d['suspect_tier2'])"`
Expected: `unreconciled []` and `suspect []`.

- [ ] **Step 7: Commit**

```bash
git add docs/decisions/d-state-pure-fold.md
git commit -m "feat(decision): arm d-state-pure-fold as tier-2 (reviewable + reconcile baseline)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Whole-repo green + live tripwire (first-blood evidence)

- [ ] **Step 1: Full gate run is green**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check --gate-reconcile; echo "exit=$?"`
Expected: `exit=0`. There will still be `warning:` lines for the 6 unarmed decisions (lazy-warn / dangling-down) — those are warnings, not failures. `hard:context` should read `2:7` (the ratio counts only `check`-bearing decisions as "hard"; the tier-2 `d-state-pure-fold` has no check, so it sits in "context" — that is expected, not a miss).

- [ ] **Step 2: Live tripwire — prove an armed check blocks a REAL violation (then revert)**

This demonstrates the tooth bites outside the ratify sandbox, on the real tree. It must be fully reverted and never committed.

```bash
printf 'import requests\nrequests.get("https://api.github.com/repos/x/y")\n' > src/super_harness/_tripwire.py
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check --gate-reconcile; echo "TRIPWIRE_EXIT=$?"
rm -f src/super_harness/_tripwire.py
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check --gate-reconcile; echo "RESTORED_EXIT=$?"
```
Expected: `TRIPWIRE_EXIT=2` (with a `CHECK-FAILED @decision:d-gh-cli-not-rest` line — the armed tier-1 check caught a real raw-REST call), then `RESTORED_EXIT=0` after removal. Confirm `git status` shows no `_tripwire.py` left behind.

- [ ] **Step 3: Capture the first-blood evidence for the slice notes**

Record (for use in Task 5): the `bite-test: bites` lines from Tasks 1–2, and the `TRIPWIRE_EXIT=2` result. These are the first genuine bites; arming count moved 0→3 (tier-1=2, tier-2=1, text-locked=3).

---

## Task 5: Record friction + closed-loop gap (private) + memory

Private files are gitignored — use Bash append, not Edit, and do not stage them.

- [ ] **Step 1: Append the slice row + friction anecdote + remapped intent matrix to the ledger**

```bash
cat >> private/CAPABILITY-CONVERGENCE-LEDGER.md <<'EOF'

## Slice 2026-06-23 — first genuine bite (arm 3 of our own decisions)

**Capability delta:** none (zero src/CLI change). **Type:** dogfood / value-realization,
not capability. **Arming count 0 → 3:** d-gh-cli-not-rest (tier-1), d-merge-gate-pure-git
(tier-1), d-state-pure-fold (tier-2). text-locked 0 → 3.

**First genuine bite (the exit condition):** both tier-1 ratify bite-tests bit by
construction (pass on real tree, fail on injected counterexample). Live tripwire: a real
raw-REST call planted in src/ made `decision check --gate-reconcile` exit 2, then exit 0
after removal. The conformance teeth bit on our own work for the first time across 6 PRs.

**Honest non-arming (recorded findings, not failures):** d-events-append-only — events.py
has no direct open() (writes abstracted) → no clean static signature; future tier-2.
d-dangling-check / d-decision-records / d-fixed-transition-matrix /
d-identity-resolution-order / d-single-gate-policy — describe behaviors, not brittle
one-token invariants; cheapest honest check is weak/over-wide → left tier-3 lazy-warn (no
hollow checks). Coarse-by-construction limits on the 2 armed tier-1 checks (host-via-variable
for gh; renamed-import for merge-gate) recorded; ride a future semantic-check upgrade.

**Maintenance-friction anecdote (n=3, single-author — a signal, NOT data):** the costly
step was writing a non-false-positive check, not the lifecycle. gh: the work was the
*semantic* call that `gh api /repos/...` is allowed but the host literal is the bypass
signature. merge-gate: the work was pinning scan scope to the 2 real gate modules and
choosing import/access patterns over bare substrings (a bare `requests` would false-positive
on prose). tier-2 was low-friction (write criterion, ratify, reconcile). Implication: if
check-authoring is the friction, the highest-value closed-loop investment is
*check-drafting assist*, not edit-time reminders.

**Closed-loop gap (the sharper finding):** arming should close inside ordinary feature
lifecycles, not a dedicated session. Today only the merge boundary is closed (CI
--gate-reconcile). edit-time reminder / decision-birth prompt / check-drafting assist are
unbuilt. This slice was historical-debt cleanup (these 9 predate the teeth) — by-nature a
one-off — and lays the closed-loop foundation on these 3 (future edits to the 3 files are
caught at their feature PR's merge gate).

**Intent × built (remapped):** ③ decisions-constrain-AI — teeth now ARMED on 3 real
decisions (was: built, armed=0). ②a conformance — converged + first value realized.
①b content-corruption loop, ②b sedimentation, agent-agnostic breadth — still unmoved.
**Direction call:** value axis moved off zero for the first time. Next-step question the
friction data informs: build check-drafting assist (close the loop on the costly step)
vs. open the ②b/①b arms.
EOF
echo "ledger appended"
```

- [ ] **Step 2: Register the deferred closed-loop mechanisms in OPEN-ITEMS**

```bash
cat >> private/OPEN-ITEMS.md <<'EOF'

**SLICE arm-decision-teeth (2026-06-23) — armed 3 decisions (0→3), first genuine bite. DEFERs registered:**
- **Arm d-events-append-only as tier-2** — needs meaningful criterion for abstracted writes (events.py has no direct open()). DOABLE-NOW, deprioritized (kept tier-2 to one representative this slice).
- **Closed-loop mechanisms (the §8 gap)** — edit-time reconcile reminder (PreToolUse feedforward, fail-open, Claude-Code-only), decision-birth prompt, and **check-drafting assist** (friction data points here as highest-value). Sequence/scope to be decided; these are real CLI features (進產品), more expensive — build only after the n=3 friction signal is confirmed. DOABLE-NOW, deprioritized.
- **Coarse-check honest limits** — host-via-variable (gh), renamed-import (merge-gate) evade the token checks; ride a future region-level / semantic-check upgrade if ever.
EOF
echo "open-items appended"
```

- [ ] **Step 3: Update the dogfood-ledger memory pointer**

Update `/Users/dawinialo/.claude/projects/-Users-dawinialo-Work-github-super-harness/memory/project-harness-dogfood-ledger.md` body: note arming count is now 3 (was 0), first genuine bite recorded 2026-06-23, and that the costly step is check-authoring (→ check-drafting assist is the indicated next closed-loop investment). Keep it one fact; SSOT stays `private/CAPABILITY-CONVERGENCE-LEDGER.md`.

(No git staging — private + memory files are out of the repo.)

---

## Task 6: Close the self-host lifecycle + ship

- [ ] **Step 1: Emit done (runs verification → implementation_complete)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness done 2026-06-23-arm-decision-teeth`
Expected: verification passes; emits `verification_passed` + `implementation_complete`; state → `AWAITING_CODE_REVIEW`. (`done` runs the FULL configured suite — ruff + mypy + `pytest -q` per `.harness/verification.yaml`, ~600s budget — regardless of what changed. It passes because the suite is currently green and this slice touches no `src/`/`tests/`, so it cannot have broken it. Ensure the Task 4 tripwire file was removed first, or pytest collection would error.)

- [ ] **Step 2: Approve code review (code-reviewer)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness review approve 2026-06-23-arm-decision-teeth --reviewer code-reviewer`
Expected: emits `code_review_passed`; state → `READY_TO_MERGE`.

- [ ] **Step 3: Write the attestation + commit it**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness attest write 2026-06-23-arm-decision-teeth
git add .harness/attestations/2026-06-23-arm-decision-teeth.jsonl
git commit -m "chore(attest): lifecycle attestation for arming decision teeth

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Local merge-gate dry run (must pass before pushing)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness attest verify --base main --head HEAD; echo "exit=$?"`
Expected: `exit=0`, every changed file covered by the plan scope. If it reports an uncovered file, the scope in Task 0 Step 3 missed it — re-root via `plan_redeclared` with the full file set (OPEN-ITEMS HARNESS GAP) and re-attest.

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin 2026-06-23-arm-decision-teeth
PATH="$(pwd)/.venv/bin:$PATH" gh pr create --base main --title "Arm decision teeth: first genuine bite (0→3)" --body "$(cat <<'EOF'
## What

Dogfood slice — arm 3 of this repo's own ratified decisions with mechanical teeth. No `src/`/`tests/`/CI change.

- `d-gh-cli-not-rest` → **tier-1**: check `! grep -rn 'api\.github\.com' src/` (raw-REST host literal). `gh api /repos/...` allowed.
- `d-merge-gate-pure-git` → **tier-1**: check pinned to `cli/attest.py` + `engineering/attestation.py`, import/access-pattern match (no bare-substring false positives).
- `d-state-pure-fold` → **tier-2**: reviewable acceptance + reconcile baseline over `reducer.py`.

## Why

The teeth (built across #38–#44) had never been armed on our own decisions — arming count was 0, the merge gate had never blocked a substantive error. This is the cheapest front-test of whether the teeth are worth deepening. Arming count **0→3** (tier-1=2, tier-2=1, text-locked=3).

## First genuine bite

Both tier-1 ratify bite-tests bit by construction (pass on real tree, fail on injected counterexample). A live tripwire (real raw-REST call planted in `src/`) made `decision check --gate-reconcile` exit 2, then exit 0 after removal.

## Honest non-arming

6 decisions left unarmed (recorded as findings, not failures): `d-events-append-only` (abstracted writes, no static signature) + 5 behavior-describing decisions where the cheapest check would be weak/over-wide. No hollow checks. Friction + closed-loop gap recorded in the private ledger.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

(Note: this machine's token lacks `read:org`, so `gh pr edit` cannot change title/body afterward — get them right in `create`.)

- [ ] **Step 6: After merge — emit the merged event**

Run (with the squash-merge commit sha): `PATH="$(pwd)/.venv/bin:$PATH" super-harness on-merge --commit <merge-sha> --change 2026-06-23-arm-decision-teeth`
Expected: emits `merged`; change → `ARCHIVED`.

---

## Self-Review

**Spec coverage (design §1–§11):**
- §1 arm 3 (2 t1 + 1 t2) → Tasks 1–3. ✓
- §2 gh tier-1 check/counterexample/limit → Task 1 (exact blocks). ✓
- §3 merge-gate tier-1 scoped check + import/access regex + dependency trace prose → Task 2 (exact blocks; trace prose lives in the decision body). ✓
- §4 tier-2 review block + ratify-then-reconcile sequencing → Task 3 (Steps 3 then 5). ✓
- §5 6 unarmed decisions recorded → Task 5 Step 1 ledger. ✓
- §6 re-ratify accept-overwrite (no provenance note) → Tasks 1–3 ratify steps; noted in Task 1 Step 3. ✓
- §7 first blood (bite-test + live tripwire, count 0→3) → Task 4. ✓
- §8 friction + closed-loop gap as first-class output (ledger row, n=3 anecdote not data) → Task 5 Steps 1–2. ✓
- §9 no CI/src/tests change → asserted in plan header + Task 6 Step 1 reasoning. ✓
- §10 deferred items registered in OPEN-ITEMS → Task 5 Step 2. ✓

**Placeholder scan:** No TBD/TODO. Every check/counterexample/review block is the literal text to author. Every command is runnable with expected output. Counterexample paths are concrete.

**Type/name consistency:** Decision ids, file paths, and CLI verbs (`change start`, `plan ready --scope '<yaml-list>'`, `review approve --reviewer <role>`, `implementation start`, `done`, `attest write`, `attest verify --base/--head`, `on-merge --commit/--change`) match the verified CLI surface (`--scope` is a single YAML-list value; `--reviewer` is the required role, `--as` is identity only). JSON keys (`hard_context`, `check_failures`, `integrity_violations`, `unreconciled_tier2`, `suspect_tier2`) match `decision check --json`'s envelope. Counterexample block syntax (` ```counterexample path=<rel> `) and `path=` constraints (relative, no `..`) match `parse_counterexample`. tier classification (check→1, review/acceptance→2) matches `decision_tier`.

**Scope correctness:** plan_ready scope = the exact 5 files the branch changes (3 decisions + 2 plan docs); the change's `.harness/attestations/<slug>.jsonl` is self-excluded; private + memory files are gitignored. CLI surface untouched → no `docs/cli-reference.md` / AGENTS.md regeneration.
