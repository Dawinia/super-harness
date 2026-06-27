# Architecture-fitness (G-FITNESS) Implementation Plan

> **For Claude:** This plan has **zero Python mechanism code** (it reuses the tier-1
> executable-check spine — see design §3). It is a config + decision-authoring + dogfood
> + self-host-lifecycle exercise. Verification is the real gates (`decision check`,
> bite-test at ratify, CI, `attest verify`), not unit tests.

**Goal:** Arm `d-core-is-base` — the repo's first architecture-class tier-1 decision —
enforcing `core ⊥ {cli, gates}` via import-linter, threaded decision → AGENTS.md → contract
→ gate.

**Architecture:** Reuse tier-1 (`decision check` runs the ` ```check ``` ` shell command,
`bite_test` proves it bites). The check command is
`PYTHONPATH=src lint-imports --config .importlinter --contract core-is-base --no-cache`,
which works on both bite-test sides via relative PYTHONPATH (spike-validated).

**Tech Stack:** import-linter (grimp), super-harness lifecycle CLI.

**Design:** `docs/plans/2026-06-27-architecture-fitness-decisions-design.md`

---

### Task 1: Add import-linter dependency

**Files:** Modify `pyproject.toml` (dev/test dependency group).

- Add `import-linter` to the dev/optional group used by CI before `decision check`.
- Install into `.venv`: `.venv/bin/python -m pip install -e '.[<group>]'` (or pip install
  import-linter if the group install is heavy).
- Verify: `.venv/bin/lint-imports --help` works.

### Task 2: Create `.importlinter` contract

**Files:** Create `.importlinter` (repo root).

```ini
[importlinter]
root_package = super_harness

[importlinter:contract:core-is-base]
name = core is the base layer (must not import cli/gates)
type = forbidden
source_modules =
    super_harness.core
forbidden_modules =
    super_harness.cli
    super_harness.gates
```

- Verify green on real tree:
  `PYTHONPATH=src .venv/bin/lint-imports --config .importlinter --contract core-is-base --no-cache`
  → exit 0, "1 kept".

### Task 3: Author the tier-1 decision record

**Files:** Create `docs/decisions/d-core-is-base.md`.

- Frontmatter: `id: d-core-is-base`, `status: proposed` (ratify in Task 4).
- Body: one-line statement; a ` ```check ``` ` block with the §3.3 command; a
  ` ```counterexample path=src/super_harness/core/_ce.py ``` ` block whose content is
  `from super_harness.cli import plan` (a real forbidden import the check must catch).
- Confirm `decision_tier()` sees it as tier-1 (has a check block).

### Task 4: Ratify (the bite-test is the anti-hollow proof)

- `decision ratify d-core-is-base` (or the CLI's ratify path) — this runs `bite_test`:
  PASS on real tree + FAIL with counterexample. Expected: ratify succeeds, "bites".
- If ratify needs the venv on PATH: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify ...`.
- Verify: `decision check` is green and counts `d-core-is-base` as tier-1.

### Task 5: Regenerate AGENTS.md + derivable docs

- `PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md -y` — picks up the new
  decision / guide line.
- `super-harness doc check` — green.
- `super-harness sync --check` — green.
- Note: AGENTS.md's generated "Decision conformance" section routes agents to
  `docs/decisions/` generically and does NOT enumerate per-decision lines, so this change
  produces no AGENTS.md diff. The decision record's prose is the guide face. (No scope
  entry for AGENTS.md.)

### Task 6: CI wiring

**Files:** Modify the CI workflow (`.github/workflows/*.yml`).

- Ensure import-linter is installed before the `decision check` step (covered if CI
  installs the dev group from Task 1; verify the install command includes it).
- Confirm `decision check` runs with `src` importable (PYTHONPATH is in the check command,
  so just needs `lint-imports` on PATH + the package present).

### Task 7: Full green sweep (local, pre-lifecycle-gates)

Run all with `PATH="$(pwd)/.venv/bin:$PATH"`:
- `super-harness decision check` → green
- `super-harness doc check` → green
- `super-harness sync --check` → green
- pytest suite → green (no source changed, but confirm nothing regressed)

### Task 8: Self-host lifecycle to PR

Follow the established sequence (NEXT-SESSION-PROMPT / memory `project-self-host-pr-attest-scope`):
- **Drift check first**: since AGENTS.md + cli docs may shift, run `sync --agents-md -y` +
  `doc check --fix` BEFORE `plan ready` to learn the full derived-file set, then put every
  changed file in `--scope`.
- `change start` → `plan ready --tier-hint --scope <all changed files>` → independent
  plan-review subagent → `review approve --reviewer plan-reviewer` → `implementation start`
  → (artifacts already authored above) → green → `done <slug>` → `review prepare --reviewer
  code-reviewer --base main` → independent reviewer subagent → verdict YAML → `review
  approve --verdict-file` → `attest write` + commit attestation → `attest verify --base
  main --head HEAD` → push → `gh pr create` → CI green → `gh pr merge --squash
  --delete-branch` → `git checkout main && git pull` → `on-merge --commit <sha> --change
  <slug>` → verify landed.

### Task 9: Ledger + memory + open-items

- `CAPABILITY-CONVERGENCE-LEDGER.md` (+ `.html`): add row + dashboard + convergence
  judgement + slice section. **Value-bleed counter +1 only if a live tripwire actually
  bled** — note import-linter caught the `core→adapters→sensors` transitive coupling grep
  missed (a real finding; decide whether that counts as a bleed vs a pre-arm discovery).
- `OPEN-ITEMS.md`: record `core → sensors` transitive coupling (deferred fix) + future
  arch decisions (sensors after adapters cycle fix; gates⊥cli / sensors⊥cli expansion).
- memory: update phase-status, dogfood-ledger, portability-decoupling (axis-A seam proven
  by shell command); rewrite `NEXT-SESSION-PROMPT.md`.
