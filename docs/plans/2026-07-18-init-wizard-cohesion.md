---
change: init-wizard-cohesion
stage: plan
tier_hint: Normal
scope:
  files:
    - src/super_harness/adapters/agent/_settings_merge.py
    - src/super_harness/adapters/agent/codex.py
    - src/super_harness/adapters/agent/claude_code.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/init_plan.py
    - src/super_harness/cli/init_ui.py
    - src/super_harness/cli/init_executor.py
    - tests/unit/adapters/test_settings_merge.py
    - tests/unit/adapters/test_codex.py
    - tests/unit/adapters/test_claude_code.py
    - tests/integration/adapter/test_claude_code.py
    - tests/unit/cli/test_init_plan.py
    - tests/unit/cli/test_init_ui.py
    - tests/unit/cli/test_init_executor.py
    - tests/integration/cli/test_init.py
    - docs/getting-started.md
    - docs/plans/2026-07-18-init-wizard-cohesion.md
---

# Init Wizard Cohesion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the guided `init` session visually coherent and operationally truthful while preserving the existing configuration semantics and non-interactive contracts.

**Architecture:** Keep Questionary as the native keyboard-input backend and Rich as the session renderer, but give both one restrained visual vocabulary. Merge each agent's three hook mutations as one settings transaction so a fresh install creates no synthetic backups and a real existing config produces exactly one backup that is disclosed in `InitPlan`. Extend the executor result with measured elapsed time and keep reinitialization inside the guided session frame.

**Tech Stack:** Python 3.10+, Click, Questionary, prompt_toolkit, Rich, pytest, Ruff, mypy.

---

## Design delta

This follow-up retains the approved five-stage wizard and changes only its presentation and truth boundary:

- Selected checkboxes use a green filled indicator while the label stays at normal emphasis; unselected indicators and secondary text are dim. Color remains optional and the filled/empty glyph remains sufficient under `NO_COLOR`.
- Questionary uses the same `◆` current-step glyph, a short `›` focus pointer, short prompt titles, and one compact navigation instruction. It disables prompt_toolkit's CPR probe for these non-full-screen prompts so terminals that do not answer CPR do not print warnings.
- The Rich renderer opens once with `┌  super-harness init`, uses `│` consistently for summaries, and closes once with `└`. Guided success does not print a second legacy `initialized at` line.
- Review groups ordinary file work and separately discloses every existing local agent config that will receive a timestamped backup.
- Agent hook installation reads, mutates, compares, backs up, and writes one settings file once. A fresh file creates zero backups; a changed existing file creates exactly one backup; an idempotent reinstall creates none.
- Re-running without `--force` stays in the guided frame and recommends `super-harness status` first, then `super-harness init --force` to review reconfiguration. Line and non-interactive modes keep deterministic error output with the same truthful guidance.

## File structure

- `src/super_harness/adapters/agent/_settings_merge.py` owns single-event and batched hook-setting transactions.
- `src/super_harness/adapters/agent/codex.py` and `claude_code.py` install all three managed hooks through the batch transaction.
- `src/super_harness/cli/init_plan.py` records existing agent configs that require backup.
- `src/super_harness/cli/init_ui.py` owns prompt styling, the continuous rail, compact review, reinitialization, and outcome rendering.
- `src/super_harness/cli/init_executor.py` measures real apply duration without fake progress.
- `src/super_harness/cli/init.py` selects guided versus legacy completion and reinitialization output.

## Task 1: Make agent settings writes atomic and backup truth explicit

**Files:**

- Modify: `src/super_harness/adapters/agent/_settings_merge.py`
- Modify: `src/super_harness/adapters/agent/codex.py`
- Modify: `src/super_harness/adapters/agent/claude_code.py`
- Modify: `src/super_harness/cli/init_plan.py`
- Test: `tests/unit/adapters/test_settings_merge.py`
- Test: `tests/unit/adapters/test_codex.py`
- Test: `tests/unit/adapters/test_claude_code.py`
- Test: `tests/integration/adapter/test_claude_code.py`
- Test: `tests/unit/cli/test_init_plan.py`
- Test: `tests/integration/cli/test_init.py`

- [ ] **Step 1: Add failing atomic-backup tests**

Add tests proving that installing all three hooks into an absent settings file creates no `*.super-harness-backup.*` files, installing into a user-owned settings file creates exactly one backup containing the exact original bytes, and an idempotent reinstall creates no additional backup. Add an init integration regression asserting a fresh guided-equivalent apply creates the 14 planned files and zero backup files.

- [ ] **Step 2: Run the atomic-backup tests and confirm RED**

Run:

```bash
pytest -q tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/integration/cli/test_init.py -k 'backup or fresh_init'
```

Expected: the fresh and changed-config assertions fail because each adapter currently performs three independent writes and leaves two intermediate backups.

- [ ] **Step 3: Implement one batched settings transaction**

Refactor the existing event-specific mutations behind a shared read/compare/write boundary and expose a batch function with explicit pre-tool, session-start, and stop commands/markers. The batch must parse once, mutate a deep copy, compare once, call `_write_backup` once only when the original file existed and the final mapping differs, and write once. Keep the existing single-event public functions behavior-compatible for their callers and tests.

- [ ] **Step 4: Route Codex and Claude Code installation through the batch**

Replace the three sequential merge calls in both adapters with the new batch call. Preserve upfront binary resolution, exact markers/matchers, rollback to the pre-install snapshot on any exception, and installed-detail text.

- [ ] **Step 5: Record planned backups**

Add `backup_paths: tuple[Path, ...] = ()` to `InitPlan`. Populate it with selected integration config paths that were present during preflight. A fresh plan has no backup paths; a force plan updating existing `.codex/hooks.json` or `.claude/settings.local.json` lists those paths in stable integration order.

- [ ] **Step 6: Run focused tests and confirm GREEN**

Run:

```bash
pytest -q tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/unit/cli/test_init_plan.py tests/integration/cli/test_init.py
```

Expected: all selected tests pass; fresh installs have no backups, changed existing configs have one exact backup, and plans identify those backup sources.

## Task 2: Unify prompt and review presentation

**Files:**

- Modify: `src/super_harness/cli/init_ui.py`
- Test: `tests/unit/cli/test_init_ui.py`

- [ ] **Step 1: Add failing prompt-style and rail tests**

Add tests that inspect Questionary calls and rendered text to require:

```text
◆  Integrations  (↑/↓ move · space select · enter confirm)
›  ● Codex  detected · recommended
   ○ Claude Code  not detected
```

The tests must assert that selected indicators use the `selected` green token while choice labels use a neutral token, no reverse/background style exists, prompt titles are `Integrations`, `Automated reviewers`, and `GitHub setup`, the review uses only `│` rail prefixes, and backup paths appear under a compact `Local backups` group.

- [ ] **Step 2: Run the UI tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py -k 'questionary or prompt or rich_guided or backup or rail'
```

Expected: failures show whole selected rows are green, long prompt titles remain, review uses bare `|`, and the renderer has no opening/closing frame or backup group.

- [ ] **Step 3: Implement the shared prompt vocabulary**

Build Questionary choices with formatted neutral title tokens, style only the selected indicator green, dim unselected indicators/instructions when color is enabled, and pass `qmark="◆"`, `pointer="›"`, and compact explicit instructions. Wrap each `unsafe_ask()` in a narrowly scoped environment guard that sets `PROMPT_TOOLKIT_NO_CPR=1` and restores the caller's prior value afterward.

- [ ] **Step 4: Implement the continuous Rich frame and compact review**

Open the renderer once, use the Unicode/ASCII rail consistently for stages and plan rows, shorten prompt titles without removing role descriptions from choices, group file actions, render planned local backups explicitly, and close the rail exactly once on success, cancellation, reinitialization, or failure.

- [ ] **Step 5: Run UI tests and confirm GREEN**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py
```

Expected: all UI tests pass in Unicode, ASCII, color, no-color, wide, and narrow cases.

## Task 3: Improve apply closure and repeat-init recovery

**Files:**

- Modify: `src/super_harness/cli/init.py`
- Modify: `src/super_harness/cli/init_ui.py`
- Modify: `src/super_harness/cli/init_executor.py`
- Test: `tests/unit/cli/test_init_executor.py`
- Test: `tests/unit/cli/test_init_ui.py`
- Test: `tests/integration/cli/test_init.py`

- [ ] **Step 1: Add failing duration, completion, and reinitialization tests**

Inject a deterministic monotonic clock into `InitExecutor` and assert the result records real elapsed milliseconds. Add guided integration assertions for one framed completion with elapsed time and no duplicate `super-harness initialized at` line. Add repeat-init tests requiring framed `Already initialized`, `super-harness status`, and a secondary `super-harness init --force` recovery action; retain deterministic legacy error formatting outside guided mode.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_executor.py tests/unit/cli/test_init_ui.py tests/integration/cli/test_init.py -k 'duration or outcome or already or reinit or initialized_at'
```

Expected: failures show no duration field, duplicate guided completion, and the old overwrite-only force hint.

- [ ] **Step 3: Measure apply duration and render one outcome**

Measure around the executor's complete ordered apply sequence using an injected monotonic callable. Store non-negative `elapsed_ms` on the immutable result. Render it only as truthful elapsed feedback (`152ms`, `1.2s`) and suppress the legacy final line only in guided mode; preserve line/non-interactive output contracts.

- [ ] **Step 4: Keep repeat init inside the selected UI backend**

Create the UI before the existing-harness guard. Let guided mode render and close a concise framed recovery result; let line/non-interactive modes use `format_error` with the revised status-first hint. `--force` continues into normal configuration/review rather than overwriting silently.

- [ ] **Step 5: Run focused and compatibility tests**

Run:

```bash
pytest -q tests/unit/cli/test_init_executor.py tests/unit/cli/test_init_ui.py tests/integration/cli/test_init.py tests/integration/cli/test_init_windows_entrypoint.py
```

Expected: all pass with guided and legacy behavior separated explicitly.

## Task 4: Update user-facing documentation and run full verification

**Files:**

- Modify: `docs/getting-started.md`
- Modify: `docs/plans/2026-07-18-init-wizard-cohesion.md`

- [ ] **Step 1: Update the representative guided transcript**

Document the single framed session, icon-only selection emphasis, compact review groups, disclosed local backups, one timed outcome, and status-first reinitialization guidance. Do not claim package-wide Windows support beyond the existing `init` boundary.

- [ ] **Step 2: Run decision and documentation checks**

Run:

```bash
super-harness decision check --changed
super-harness doc check
```

Expected: both exit zero.

- [ ] **Step 3: Run formatting, typing, focused tests, and harness verification**

Run:

```bash
ruff format --check src/super_harness/adapters/agent/_settings_merge.py src/super_harness/adapters/agent/codex.py src/super_harness/adapters/agent/claude_code.py src/super_harness/cli/init.py src/super_harness/cli/init_plan.py src/super_harness/cli/init_ui.py src/super_harness/cli/init_executor.py tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py tests/integration/cli/test_init.py
ruff check src/super_harness tests
mypy src/super_harness
pytest -q tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py tests/integration/cli/test_init.py tests/integration/cli/test_init_windows_entrypoint.py
super-harness verify
```

Expected: all commands exit zero and `super-harness verify` reports zero failed checks.

