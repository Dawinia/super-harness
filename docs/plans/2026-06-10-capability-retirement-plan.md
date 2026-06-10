# Capability Retirement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task.

> NOTE: like the sibling design docs in `docs/plans/`, this file carries **NO**
> `change:` / `stage:` frontmatter — the SuperpowersAdapter discovers changes by that
> frontmatter, so adding it would auto-emit lifecycle events. It stays inert until
> `change start` is run explicitly.

**Goal:** Retire the `@capability:`-era machinery (the dead `affected_anchors`
lifecycle pipeline + the old anchor sensors) and re-root the surviving in-code anchors
onto ratified `@decision:` records, ending with zero `@capability:` in `src/`.

**Architecture:** This is a **retirement refactor**, not a feature. Per the v3 design
(`2026-06-09-capability-retirement-design.md`, the authority for all file:line surgery)
and umbrella §13: order = **collapse archival → retire machinery → re-root anchors →
reconcile surfaces**. The pipeline being removed has **no behaviour** (it idled on a
permanently-empty `affected_anchors`); the **one** genuine behavioural change is the
§5.1 archival collapse (merge archives directly, since l1_updater's
`l1_update_completed` was the sole `MERGED→ARCHIVED` trigger).

**Tech Stack:** Python 3.10+, pytest, ruff, mypy, click CLI, the repo's own
`super-harness decision check` + `cli-reference` drift gates.

**TDD discipline for a deletion-heavy refactor:**
- **Genuine behavioural change (Task 1, the collapse):** real TDD — write/change the
  test to the new expected behaviour, watch it FAIL against old code, change code, watch
  PASS.
- **Pure deletions (Tasks 2–7, 9):** the "test" is the **full suite + ruff + mypy +
  `decision check` staying green**. Delete code and its tests in lockstep; a stale
  import or orphaned test surfaces as a red suite immediately. Never delete code without
  deleting/adjusting its tests in the **same** step.
- **Re-root (Task 8):** `decision check` exit 0 is the gate — record + anchor land in
  the **same commit** so no dangling-up at any boundary.

**The green invariant (run after EVERY task, before EVERY commit):**
```bash
.venv/bin/pytest -q            # full suite — NOT a subset
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/super-harness decision check        # exit 0
python -m scripts.gen_cli_reference --check    # cli-reference golden in sync (CI test.yml:73)
```
All five must pass. The cli-reference drift check is in the invariant because tasks that
touch the CLI surface (3, 5, 6, the `anchor`-group/`--anchors`/`on-merge`-data changes)
stale the golden — those tasks **regenerate it in the same commit** (`python -m
scripts.gen_cli_reference`) so no commit is CI-red. This is the mandated guard (design
§2 + plan-review S-2): the refactor's risk is red tests/drift, caught here.

**Lifecycle (dogfood, design §11):** this plan is authored with **no active change**
(gate allows). Implementation runs after `change start <slug>` → `plan ready` →
independent plan review → `review approve --reviewer plan-reviewer` (→ PLAN_APPROVED,
edits allowed) → tasks below → `review approve --reviewer code-reviewer` → `done`.
Gate-relevant edits run in the **main session** (subagent edits bypass the hook —
design §11 honest scope).

---

### Task 0: Pre-flight — establish the green baseline

**Files:** none (measurement only).

**Step 1:** Run the full green invariant (all four commands above). Record: the pytest
pass count (the number to preserve), and confirm `ruff`/`mypy`/`decision check` clean.

**Step 2:** Capture the inventory the refactor must zero out:
```bash
grep -rn "@capability:" src/ | wc -l        # ~33 lines today (21 sentinel comments + 12 in docstrings/consts, incl. the KEPT anchor_scanner.py — see Task 7 B-1 fix)
grep -rn "affected_anchors" src/ | wc -l    # the pipeline sites
.venv/bin/super-harness change list         # confirm no active change blocking
```
The slice's defining exit assertion (Task 11) is **zero `@capability:` anywhere in
`src/` — including docstrings/constants, not just sentinel comments** (plan-review B-1).

**Step 3:** No commit (baseline only). Proceed only if all four gates are green; if the
baseline is already red, STOP and report — do not refactor on a red tree.

---

### Task 1: Archival collapse — the one state-machine change (§5.1)

**This is the only behavioural change. Real TDD. Handle with care.**

**Files:**
- Modify: `src/super_harness/core/transitions.py:42-43`
- Modify: `src/super_harness/core/state.py:22` (state enum)
- Modify: `src/super_harness/gates/decisions.py:31,46` (MERGED rows)
- Test: `tests/unit/core/test_transitions.py`, `tests/unit/core/test_state.py`,
  `tests/unit/gates/test_decisions.py` (or wherever the matrix is asserted)

**Step 1: Change the tests to the new behaviour (watch them FAIL).**
- Assert `(READY_TO_MERGE, "merged") → "ARCHIVED"`.
- Assert `"MERGED"` is NOT a valid state (10 states, not 11).
- Remove/replace any test asserting `(MERGED, "l1_update_completed") → ARCHIVED` or the
  `MERGED` gate verdict.

**Step 2: Run them — expect FAIL.**
```bash
.venv/bin/pytest tests/unit/core/test_transitions.py tests/unit/core/test_state.py -q
```
Expected: FAIL (old code still routes merged→MERGED).

**Step 3: Make the change.**
- `transitions.py`: `("READY_TO_MERGE", "merged"): "ARCHIVED"`; **delete** the
  `("MERGED", "l1_update_completed"): "ARCHIVED"` entry.
- `state.py:22`: remove `"MERGED"` from the states list.
- `gates/decisions.py`: remove the `"MERGED"` rows from `PRE_TOOL_USE_DECISIONS` (:31)
  and `SUGGESTIONS` (:46); **also fix the "11-state" module docstring/comments (`:1,:17`)
  → "10-state"** (plan-review N-2 — don't leave a stale count claim).
- Leave `l1_update_completed` / `l1_update_failed` event **types** defined for now —
  l1_updater still emits them (deleted in Task 2). They are now no-op events (no
  transition consumes `l1_update_completed`); harmless until Task 2.

**Step 4: Run the green invariant.** Update any other test that asserted the 11-state
count or the MERGED gate. Full suite + ruff + mypy + decision check green.

**Step 5: Commit.**
```bash
git add -A && git commit -m "refactor(lifecycle): collapse MERGED — merge archives directly

l1_updater's l1_update_completed was the sole MERGED->ARCHIVED trigger; with the
L1 write-back retired there is no post-merge step, so (READY_TO_MERGE,merged) now
goes straight to ARCHIVED. 11->10 states. Per design §5.1.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Delete l1_updater + _l1_helpers + the l1_update_* events

**Files:**
- Delete: `src/super_harness/sensors/l1_updater.py`, `sensors/_l1_helpers.py`
- Delete: `tests/unit/sensors/test_l1_updater.py`, `tests/unit/sensors/test_l1_helpers.py`
- Modify: `sensors/__init__.py:152-154` (remove `L1Updater` import + register_builtin)
- Modify: `core/events.py:34` + `core/transitions.py:23` (remove `l1_update_completed`,
  `l1_update_failed` from the event-type def + informational list — now unemitted)
- Test: events tests asserting those types

**Step 1:** Delete the two source files and their two test files together.
**Step 2:** Remove the `L1Updater` import + `register_builtin("l1-updater", ...)` from
`sensors/__init__.py`.
**Step 3:** Remove the `l1_update_completed` / `l1_update_failed` event types from
`core/events.py` and the informational tuple in `transitions.py`; update any event test.
**Step 4:** Green invariant (full suite catches any lingering importer).
**Step 5:** Commit (`refactor(sensors): delete l1-updater + l1_update_* events (dead pipe)`).

---

### Task 3: Delete anchor-index-rebuilder + anchor CLI + the index artifact

**Files:**
- Delete: `sensors/anchor_index_rebuilder.py` + `tests/unit/sensors/test_anchor_index_rebuilder.py`
- Delete: `cli/anchor.py` + `tests/unit/cli/test_anchor.py`
- Modify: `sensors/__init__.py:144-146` (remove import + register_builtin)
- Modify: the CLI group registration (remove the `anchor` group — find where `cli/anchor.py`'s group is added to the root CLI)
- Modify: `core/paths.py:121-123` (remove `anchors_index_path`)
- Modify: `engineering/gitignore_injector.py` — remove **BOTH** `.harness/anchors/index.yaml`
  AND `.harness/pending-l1-updates/` from `_CANONICAL_PATHS` (`~:76`); both writers are
  deleted (plan-review S-5: source must match the test, which drops both).
- Modify: `cli/init.py:194,198` (drop `anchors/` + `pending-l1-updates/` dir-creations);
  fix the "create all 6 sub-directories" comment (`~:192`) → "4" (plan-review N-2).
- Modify: `scripts/gen_cli_reference.py` — delete the `"anchor list"` (`:190`) and
  `"anchor sync"` (`:195`) entries from the hand-maintained `_EXIT_CODES` map
  (plan-review S-1: the generator carries them; the `anchor` group is gone).
- Test: `tests/integration/cli/test_init.py` — drop dir assertions (`~:54,58`) AND the
  `_CANONICAL_GITIGNORE_PATHS` assertion for both paths (`~:435-436`);
  `test_gitignore_injector.py` drift-guard
- Re-sync the committed `.gitignore`: `.venv/bin/super-harness sync --gitignore`

**Step 1–6:** Delete files+tests; strip registrations; remove path + both gitignore
entries + init dirs; edit the generator `_EXIT_CODES`; re-sync `.gitignore`; update
init/gitignore tests. **Step 7:** Regenerate the golden: `python -m scripts.gen_cli_reference`
(the `anchor` group left the surface). **Step 8:** Green invariant (incl. cli-reference
`--check`) — esp. confirm `super-harness anchor list` errors "no such command". **Step 9:**
Commit (`refactor: retire anchor index + anchor CLI (no consumer)`).

---

### Task 4: Delete anchor-sentinel-presence + _anchor_policy + verification baseline

**The B1 daemon-crash risk lives here — remove the WHOLE baseline or import breaks.**

**Files:**
- Delete: `sensors/anchor_sentinel_presence.py` + `tests/unit/sensors/test_anchor_sentinel_presence.py`
- Delete: `sensors/_anchor_policy.py` + `tests/unit/sensors/test_anchor_policy.py`
- Modify: `sensors/__init__.py:140-142` (remove import + register_builtin)
- Modify: `sensors/verification_runner.py` — the FULL baseline removal (design §5):
  imports `:51` (`scan_sentinels`) + `:75` (`anchor_must_pass_for_tier`); `_BASELINE_ANCHOR`
  `:294`; its `BASELINE_CHECK_IDS` membership `:301-302` (3→2); `_baseline_anchor_presence`
  `:342-383`; the `baseline_check_tasks` anchor branch `:591-607` + the function/module
  docstrings (`~:20-22`, `~:542-576`, "3 baselines"→"2")
- Test: `tests/unit/sensors/test_verification_runner.py` — `BASELINE_CHECK_IDS` set
  assertions (`~:452/465/488`), `_baseline_anchor_presence` tests (`~:921-986`), the
  `:31` import, tier-resolution (`~:368/374`), `only_ids` cases (`~:977/994`), lifecycle
  result assertions (`~:1116/1194`), and the `checks_run == 3` assertion (`~:1166`) → `== 2`
  (plan-review S-4). The full suite is the net for any site this enumeration misses.

**Step 1:** Delete the two sensor files + their two test files.
**Step 2:** Strip the `verification_runner.py` baseline per the full list above.
**Step 3:** After removal, grep the file to confirm NO surviving reference to
`anchor_must_pass_for_tier` / `scan_sentinels` / `_BASELINE_ANCHOR` / `_run_anchor`.
**Step 4:** Update `test_verification_runner.py` (all the sites above).
**Step 5:** Remove the `register_builtin("anchor-sentinel-presence", ...)` + import.
**Step 6:** Green invariant. **Critically verify the daemon imports:**
```bash
.venv/bin/python -c "import super_harness.sensors; import super_harness.sensors.verification_runner"
.venv/bin/super-harness-daemon --help  # or a daemon smoke — must not crash on import
```
**Step 7:** Commit (`refactor(sensors): delete sentinel-presence + anchor baseline`).

---

### Task 5: Strip on-merge sensor dispatch

**Files:**
- Modify: `cli/on_merge.py` — remove the `SensorDispatcher([L1Updater(), AnchorIndexRebuilder()])`
  dispatch (`:263-273`), `_l1_followup_pr_from_results`, `_SENSORS_TRIGGERED` (`:81`), the
  `sensors_triggered` / `l1_followup_pr` `data` fields. Keep `_emit_merged` (`:241`) and
  exit-code logic.
- Test: `tests/integration/cli/test_on_merge.py` — drop dispatch / L1-PR /
  `sensors_triggered` assertions; **delete** `test_frozen_sensors_triggered_matches_registered_merged_sensors`
  (`:591`); keep `merged`-emit + exit codes. Confirm the change reaches ARCHIVED (Task 1).

**Steps:** edit → update/delete tests → **regenerate cli-reference** (`python -m
scripts.gen_cli_reference` — `on-merge`'s `data` shrank) → green invariant → commit
(`refactor(on-merge): drop sensor dispatch; merged archives directly`).

---

### Task 6: Remove the affected_anchors pipeline (Part A)

**Files (design §4):**
- Modify: `core/state.py` (drop `affected_anchors` field + docstring `~:46`)
- Modify: `core/reducer.py:139-140` (drop the plan_ready branch)
- Modify: `cli/plan.py:106,148` (drop `--anchors` option + payload population)
- Modify: `adapters/framework/superpowers.py:115` (drop key from `_plan_payload`) + `:272`
  (stale prose)
- Modify: `engineering/pr_metadata.py:193-204,270,279` (drop `_derive_affected_anchors` +
  PR-body line)
- Test: `test_state.py`, `test_state_yaml.py`, `test_plan.py`, `test_superpowers.py`,
  `test_pr_metadata.py`
- Test: `tests/e2e/openspec_claude_code/test_full_lifecycle.py` — **multi-block** (design
  §9): drop the `affected_anchors:[cap-hello]` fixture (`:131`), the
  `# @capability:cap-hello` sentinel write into the demo repo (`:169`, plan-review N-3),
  the Phase-L anchor-index assertions (`:251-255`), the `l1_update_completed` assertion
  (`:271`); rewrite the tail to assert `merged → ARCHIVED` directly (no MERGED, no anchor
  dispatch).

**Steps:** edit all sites → update tests (incl. the E2E canary) → **regenerate
cli-reference** (`python -m scripts.gen_cli_reference` — `plan ready --anchors` removed) →
green invariant → commit (`refactor: remove dead affected_anchors pipeline`).

---

### Task 7: Remove the scanner's vestigial @capability default (Part C)

**Files:**
- Modify: `core/anchor_scanner.py` — delete `_DEFAULT_KEYWORD` (`:50`); make `keyword` a
  required keyword-only arg in `scan_sentinels` (`:156`) + `scan_sentinel_locations` (`:114`).
  **B-1 (plan-review BLOCKER): also scrub the `@capability:` literals in this KEPT file's
  docstrings/comments at `:1, :5, :127, :171`** — rewrite to reference `@decision:` (the
  real surviving keyword) or generic "the configured keyword". This file survives the
  slice, so its `@capability:` text would otherwise make the zero-assertion (Task 11) fail.
- Test: `tests/unit/core/test_anchor_scanner.py` — pass `keyword=` explicitly everywhere

**Step 1:** Confirm the only surviving callers pass `keyword` explicitly:
```bash
grep -rn "scan_sentinel" src/   # expect only decision_check.py + cli/decision.py
```
**Steps 2–4:** edit (incl. the B-1 docstring scrub) → update scanner tests → green
invariant → commit (`refactor(scanner): require explicit keyword, scrub @capability`).

> **Checkpoint:** at this commit the entire `@capability` MACHINERY is gone **and the
> scanner's own `@capability:` docstrings are scrubbed**. The `@capability:` sentinel
> COMMENTS on the 17 surviving modules still sit inert (`grep -rn "@capability:" src/`
> now shows only those ~17 sentinel lines — the docstring residue is gone). Full suite
> green. Now re-root.

---

### Task 8: Re-root — mint the frozen keep-set of decisions + narrow anchors (Part D)

**FROZEN keep-set (7) — hard cap; pulling another up requires a new slice (design §7.2):**

| Old `@capability:` (file) | New `d-<id>` | One-line decision (for `--text`) |
|---|---|---|
| `core/reducer.py` | `d-state-pure-fold` | State is a pure left-fold over the event log; never mutated in place. |
| `core/events.py` | `d-events-append-only` | Events are append-only; the log is the source of truth, state is derived. |
| `core/transitions.py` | `d-fixed-transition-matrix` | State transitions come only from the fixed declared matrix; no ad-hoc transition. |
| `gates/pre_tool_use.py` | `d-single-gate-policy` | Gate policy lives in one literal (`gates.decisions`); daemon + in-process gate both read it, neither invents policy. |
| `engineering/attestation.py` | `d-merge-gate-pure-git` | The merge gate verifies committed evidence with pure git — no network, no runtime trust. |
| `core/identity.py` | `d-identity-resolution-order` | Identity resolution order is fixed: `--as` > env > git config > `"cli"`. |
| `engineering/gh.py` | `d-gh-cli-not-rest` | GitHub access goes through the `gh` CLI, never raw REST. |

(Exact `d-` ids are the plan's proposal; the human may rename at ratification. Anchors go
on the **narrowest betraying code site (α)**, NOT the file-top comment — e.g.
`d-gh-cli-not-rest` on a `subprocess.run(["gh", ...])` line in `gh.py:104`, not `:1`.)

**⚠️ Cross-file re-targeting (plan-review S-3 — the old `@capability` file is NOT always
the betraying site; re-examine each before minting):**
- `d-merge-gate-pure-git`: the old sentinel is on `engineering/attestation.py:1`, but that
  file is a **pure-function domain layer with no git/network calls** — the decision "uses
  pure git, no network" **cannot be betrayed there**. Remove the `@capability` from
  `attestation.py` and place `@decision:d-merge-gate-pure-git` on the actual git-invocation
  site in **`cli/attest.py`** (where a network call could replace git). Anchoring on
  attestation.py would be the β anti-pattern.
- `d-single-gate-policy`: the single-source literal it protects is `PRE_TOOL_USE_DECISIONS`
  in **`gates/decisions.py`** — place the anchor on that literal (the thing the decision is
  about), not on the consumer `pre_tool_use.py:1`. (The old `@capability` was on
  pre_tool_use.py; the re-root moves it to the decisions.py literal.)
- For the other 5, confirm the narrow betraying site before minting (reducer fold site,
  events append path, transitions matrix literal, identity resolution function, gh
  subprocess line) — do not default to `:1`.

**Per decision (repeat for each of the 7), committed in 3 logical groups
(core/ ; gates/+identity ; engineering/) to avoid 7 micro-commits:**

**Step 1:** `decision new <id> --text "<one-line>"` then `decision ratify <id>`.
**Step 2:** Replace the `@capability:capability-<old>` sentinel comment with
`@decision:<id>` on the **narrow** site embodying the decision (move it off `:1` if it
was file-top).
**Step 3:** `.venv/bin/super-harness decision check` → exit 0 (the new ratified record
has its anchor: no dangling-up, no dangling-down for it).
**Step 4 (per group):** Green invariant. **Step 5 (per group):** Commit
(`feat(decision): re-root <area> capabilities onto ratified decisions`).

---

### Task 9: Delete the remaining label sentinels (delete + defer)

**The ~10 module-label `@capability:` that map to no single betrayable decision
(design §7.2):** `adapter-protocol`, `agent-adapter-builtin`, `agents-md-injection`,
`ci-templates`, `framework-adapter-builtin` (both framework adapter files),
`pr-metadata`, `sensor-architecture`, `verification-runner`, `verification-runner-config`,
`cli-surface`.

**Step 1:** Remove each `@capability:` comment line from those source files (delete the
sentinel; write no record).
**Step 2:** Confirm the deferral is registered (OPEN-ITEMS #8 already lists "decisions for
these modules → spec-authoring slice"; add the per-module list if not present).
**Step 3:** The keyword-contract gate:
```bash
grep -rn "@capability:" src/        # MUST be empty (zero)
grep -rn "@implements:" src/        # MUST be empty (never introduced)
.venv/bin/super-harness decision check   # exit 0
```
**Step 4:** Green invariant. **Step 5:** Commit
(`refactor: delete remaining @capability module-labels (defer to spec-authoring)`).

---

### Task 10: Reconcile surfaces (Part E)

**Files (design §10):**
- `private/specs/2026-05-27-cli-command-surface.md` — 5 surfaces (`:311` on-merge behaviour,
  `:657-664` on-merge data block, `:421/425` `plan ready --anchors`, `:449-460` `anchor list`,
  `:464-470` `anchor sync`)
- `private/specs/2026-05-26-sensor-gate-architecture.md` — sensor table (`~:326-340`),
  §3.1.9/§3.1.10, `affected_anchors` snippets (`~:462/489/516`), capability-l1-updater carve-out
- `private/specs/2026-05-26-lifecycle-event-model.md` — `affected_anchors` (`~:156/251/308/538`),
  l1_update events, **the state machine** (`~:346/363/416/433`: 11→10, merge→ARCHIVED)
- `docs/getting-started.md:282-345` — multi-paragraph rewrite (remove on-merge L1 dispatch,
  anchor sync/list, index, l1 follow-up PR, sentinel-presence warn)
- `AGENTS.md:22,80` — stale `affected_anchors` / "anchor sentinels" prose
- `docs/cli-reference.md` — **regenerate** the golden after CLI changes

**Step 1:** Edit the private specs + getting-started + AGENTS.md prose. (The generator's
`_EXIT_CODES` `anchor` entries + the per-task cli-reference regens were already handled in
Tasks 3/5/6 — by here the golden should already be in sync; this task is the prose/spec
surfaces that have no drift gate.)
**Step 2:** Final regenerate + verify: `python -m scripts.gen_cli_reference` then
`python -m scripts.gen_cli_reference --check` (exit 0).
**Step 3:** Green invariant (incl. cli-reference `--check`).
**Step 4:** Commit (`docs: reconcile specs/template/getting-started after capability retirement`).
(Note: `private/**` is gitignored — those spec edits are local SSOT, not in the commit.)

---

### Task 11: Whole-branch verification (before completion)

> **REQUIRED SUB-SKILL:** Use superpowers:verification-before-completion.

**Step 1:** Full green invariant one more time, from a clean state:
```bash
.venv/bin/pytest -q              # == baseline count minus deleted tests, all green
.venv/bin/ruff check . && .venv/bin/mypy src
.venv/bin/super-harness decision check    # exit 0
# cli-reference drift check (the repo's --check command) — in sync
```
**Step 2:** Keyword-contract assertions:
```bash
grep -rn "@capability:" src/   # ZERO
grep -rn "@implements:" src/   # ZERO
grep -rn "affected_anchors" src/   # ZERO (pipeline fully gone)
```
**Step 3:** Lifecycle smoke: confirm a change driven to `merged` reaches `ARCHIVED`
directly (no MERGED), e.g. via the E2E test or `scripts/smoke-gate.sh` if applicable.
**Step 4:** Report results with evidence (paste the counts/exit codes) — do NOT claim
done without the output. Then proceed to `review approve --reviewer code-reviewer` →
`done`.

---

## Execution notes

- **Decision-record set is frozen** (Task 8 table). Adding more = a new slice.
- **Every task = full suite + ruff + mypy + decision check**, not a subset (design §2 risk
  is red tests; this is the guard).
- **Per-task code-quality review must run BOTH ruff AND mypy** (slice-1 hard lesson: ruff
  alone missed unused imports + type errors).
- `private/**` edits (Task 10) are local SSOT, gitignored — they won't appear in commits.
