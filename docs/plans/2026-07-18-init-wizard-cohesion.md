---
change: init-wizard-cohesion
stage: plan
tier_hint: Normal
scope:
  files:
    - src/super_harness/adapters/__init__.py
    - src/super_harness/adapters/install.py
    - src/super_harness/adapters/agent/_settings_merge.py
    - src/super_harness/adapters/agent/codex.py
    - src/super_harness/adapters/agent/claude_code.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/init_plan.py
    - src/super_harness/cli/init_ui.py
    - src/super_harness/cli/init_executor.py
    - tests/unit/adapters/test_install.py
    - tests/unit/adapters/test_settings_merge.py
    - tests/unit/adapters/test_codex.py
    - tests/unit/adapters/test_claude_code.py
    - tests/integration/adapter/test_claude_code.py
    - tests/unit/cli/test_init_plan.py
    - tests/unit/cli/test_init_ui.py
    - tests/unit/cli/test_init_executor.py
    - tests/integration/cli/test_init.py
    - docs/getting-started.md
    - docs/adapters/codex.md
    - docs/adapters/claude-code.md
    - docs/plans/2026-07-18-init-wizard-cohesion.md
---

# Init Wizard Cohesion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the guided `init` session visually coherent and operationally truthful while preserving the existing configuration semantics and non-interactive contracts.

**Architecture:** Keep Questionary as the native keyboard-input backend and Rich as the session renderer, but give both one restrained visual vocabulary. Preflight freezes each selected agent's original settings bytes, desired bytes, and resolved executable paths into `InitPlan`; apply consumes that exact transaction and rejects workspace or PATH drift before writing. This makes backup disclosure exact while ensuring a fresh install creates no synthetic backups, a changed existing config produces exactly one disclosed backup, and an already-current config produces none. Extend the executor result with measured elapsed time and keep reinitialization inside the guided session frame.

**Tech Stack:** Python 3.10+, Click, Questionary, prompt_toolkit, Rich, pytest, Ruff, mypy.

---

## Design delta

This follow-up retains the approved five-stage wizard and changes only its presentation and truth boundary:

- Selected checkboxes use a green filled indicator while the label stays at normal emphasis; unselected indicators and secondary text are dim. Color remains optional and the filled/empty glyph remains sufficient under `NO_COLOR`.
- Questionary uses the same `◆` current-step glyph, a short `›` focus pointer, short prompt titles, and one compact navigation instruction. It disables prompt_toolkit's CPR probe for these non-full-screen prompts so terminals that do not answer CPR do not print warnings.
- The Rich renderer opens once with `┌  super-harness init`, uses `│` consistently for summaries, and closes once with `└`. Guided success does not print a second legacy `initialized at` line.
- Review groups ordinary file work and separately discloses only local agent configs whose planned settings transformation differs from the captured bytes and therefore will receive a timestamped backup.
- Agent hook installation reads, mutates, compares, backs up, and writes one settings file once. A fresh file creates zero backups; a changed existing file creates exactly one backup; an idempotent reinstall creates none.
- The reviewed agent transaction is immutable. Apply compares current settings bytes and current resolved binary paths with the frozen inputs; any drift aborts that operation before backup or write and instructs the user to rerun configuration/review.
- Uninstall restores the earliest pristine backup when one exists. When installation started from an absent file and therefore has no backup, uninstall strips only the three marker-owned hooks, preserves unrelated settings, prunes empty hook scaffolding, and removes the settings file only when nothing user-owned remains.
- Re-running without `--force` stays in the guided frame and recommends `super-harness status` first, then `super-harness init --force` to review reconfiguration. Line and non-interactive modes keep deterministic error output with the same truthful guidance.

## File structure

- `src/super_harness/adapters/__init__.py` exposes an optional read-only hook-install preview boundary for agent adapters.
- `src/super_harness/adapters/install.py` defines the immutable integration transaction, resolves it during preflight, and validates/consumes it during apply.
- `src/super_harness/adapters/agent/_settings_merge.py` owns pure hook transformation, single-event and batched settings transactions, and marker-only removal.
- `src/super_harness/adapters/agent/codex.py` and `claude_code.py` install all three managed hooks through the batch transaction.
- `src/super_harness/cli/init_plan.py` carries frozen integration transactions and derives the exact configs that require backup.
- `src/super_harness/cli/init_ui.py` owns prompt styling, the continuous rail, compact review, reinitialization, and outcome rendering.
- `src/super_harness/cli/init_executor.py` measures real apply duration without fake progress.
- `src/super_harness/cli/init.py` selects guided versus legacy completion and reinitialization output.

## Task 1: Make agent settings writes atomic and backup truth explicit

**Files:**

- Modify: `src/super_harness/adapters/agent/_settings_merge.py`
- Modify: `src/super_harness/adapters/__init__.py`
- Modify: `src/super_harness/adapters/install.py`
- Modify: `src/super_harness/adapters/agent/codex.py`
- Modify: `src/super_harness/adapters/agent/claude_code.py`
- Modify: `src/super_harness/cli/init_plan.py`
- Test: `tests/unit/adapters/test_install.py`
- Test: `tests/unit/adapters/test_settings_merge.py`
- Test: `tests/unit/adapters/test_codex.py`
- Test: `tests/unit/adapters/test_claude_code.py`
- Test: `tests/integration/adapter/test_claude_code.py`
- Test: `tests/unit/cli/test_init_plan.py`
- Test: `tests/integration/cli/test_init.py`

- [x] **Step 1: Add failing atomic-backup and uninstall tests**

Add tests proving that installing all three hooks into an absent settings file creates no `*.super-harness-backup.*` files, installing into a user-owned settings file creates exactly one backup containing the exact original bytes, and an idempotent reinstall creates no additional backup. Add install/uninstall round trips for both agents: a pre-existing file restores exact original bytes, while a fresh file with only managed hooks is removed and a fresh file with unrelated content retains that content after the managed hooks are stripped. Add an init integration regression asserting a fresh guided-equivalent apply creates the 14 planned files and zero backup files.

- [x] **Step 2: Run the atomic-backup tests and confirm RED**

Run:

```bash
pytest -q tests/unit/adapters/test_install.py tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/integration/cli/test_init.py -k 'backup or uninstall or fresh_init'
```

Expected: the fresh and changed-config assertions fail because each adapter currently performs three independent writes and leaves two intermediate backups.

- [x] **Step 3: Implement one frozen settings transaction**

Refactor the existing event-specific mutations behind a pure transformation that returns an immutable settings plan containing the settings path, original bytes, desired bytes, `changed`, and `backup_required`. Expose a batch planner with explicit pre-tool, session-start, and stop commands/markers, plus an apply function that first compares the current raw bytes with the frozen original bytes, then calls `_write_backup` once only when the original file existed and the final mapping differs, and writes once. A byte mismatch raises a specific stale-plan error before backup or write. Keep the existing single-event public functions behavior-compatible for their callers and tests.

- [x] **Step 4: Route preflight, install, and uninstall through the shared transformation**

Add a non-mutating `plan_hook_install` method to the agent-adapter boundary with a default `None` result for adapters that do not manage a local settings file. Codex and Claude Code implement it with the shared batch planner and injectable executable lookup. `adapters.install` wraps that settings plan plus the exact resolved `super-harness-hook` and `super-harness` paths in an immutable integration transaction. Apply accepts the frozen transaction, verifies both executable lookups still resolve to the recorded paths, and delegates its settings plan without recomputing commands or desired bytes. Replace the three sequential merge calls in both adapters with this planned apply while preserving exact markers/matchers, rollback to the pre-install snapshot on any exception, and installed-detail text. When no pristine backup exists, uninstall invokes marker-only removal; it prunes empty event lists and the empty `hooks` mapping, deleting the file only when the resulting top-level mapping is empty.

- [x] **Step 5: Record planned backups**

Add immutable integration transactions to `InitPreflight` and `InitPlan`, plus `backup_paths: tuple[Path, ...] = ()` derived from those selected transactions. `inspect_workspace` obtains transactions with the injected executable lookup; `build_init_plan` freezes the selected subset; and the executor passes each exact transaction to `install_agent_integration`. Include a drift regression that mutates settings bytes after review and another that changes executable lookup results: both must fail before creating a backup or modifying settings. A fresh plan and an already-current force plan have no backup paths; a force plan that will actually change existing `.codex/hooks.json` or `.claude/settings.local.json` lists those paths in stable integration order.

- [x] **Step 6: Run focused tests and confirm GREEN**

Run:

```bash
pytest -q tests/unit/adapters/test_install.py tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/unit/cli/test_init_plan.py tests/integration/cli/test_init.py
```

Expected: all selected tests pass; fresh installs have no backups and uninstall cleanly, changed existing configs have one exact backup, idempotent configs disclose no backup, plans identify only actual backup-producing changes, and post-review settings/PATH drift is rejected without writes.

## Task 2: Unify prompt and review presentation

**Files:**

- Modify: `src/super_harness/cli/init_ui.py`
- Test: `tests/unit/cli/test_init_ui.py`

- [x] **Step 1: Add failing prompt-style and rail tests**

Add tests that inspect Questionary calls and rendered text to require:

```text
◆  Integrations  (↑/↓ move · space select · enter confirm)
›  ● Codex  detected · recommended
   ○ Claude Code  not detected
```

The tests must assert that selected indicators use the `selected` green token while choice labels use a neutral token, no reverse/background style exists, prompt titles are `Integrations`, `Automated reviewers`, and `GitHub setup`, the review uses only `│` rail prefixes, and backup paths appear under a compact `Local backups` group.

- [x] **Step 2: Run the UI tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py -k 'questionary or prompt or rich_guided or backup or rail'
```

Expected: failures show whole selected rows are green, long prompt titles remain, review uses bare `|`, and the renderer has no opening/closing frame or backup group.

- [x] **Step 3: Implement the shared prompt vocabulary**

Build Questionary choices with formatted neutral title tokens, style only the selected indicator green, dim unselected indicators/instructions when color is enabled, and pass `qmark="◆"`, `pointer="›"`, and compact explicit instructions. Wrap each `unsafe_ask()` in a narrowly scoped environment guard that sets `PROMPT_TOOLKIT_NO_CPR=1` and restores the caller's prior value afterward.

- [x] **Step 4: Implement the continuous Rich frame and compact review**

Open the renderer once, use the Unicode/ASCII rail consistently for stages and plan rows, shorten prompt titles without removing role descriptions from choices, group file actions, render planned local backups explicitly, and close the rail exactly once on success, cancellation, reinitialization, or failure.

- [x] **Step 5: Run UI tests and confirm GREEN**

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

- [x] **Step 1: Add failing duration, completion, and reinitialization tests**

Inject a deterministic monotonic clock into `InitExecutor` and assert the result records real elapsed milliseconds. Add guided integration assertions for one framed completion with elapsed time and no duplicate `super-harness initialized at` line. Add repeat-init tests requiring framed `Already initialized`, `super-harness status`, and a secondary `super-harness init --force` recovery action; retain deterministic legacy error formatting outside guided mode.

- [x] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_executor.py tests/unit/cli/test_init_ui.py tests/integration/cli/test_init.py -k 'duration or outcome or already or reinit or initialized_at'
```

Expected: failures show no duration field, duplicate guided completion, and the old overwrite-only force hint.

- [x] **Step 3: Measure apply duration and render one outcome**

Measure around the executor's complete ordered apply sequence using an injected monotonic callable. Store non-negative `elapsed_ms` on the immutable result. Render it only as truthful elapsed feedback (`152ms`, `1.2s`) and suppress the legacy final line only in guided mode; preserve line/non-interactive output contracts.

- [x] **Step 4: Keep repeat init inside the selected UI backend**

Create the UI before the existing-harness guard. Let guided mode render and close a concise framed recovery result; let line/non-interactive modes use `format_error` with the revised status-first hint. `--force` continues into normal configuration/review rather than overwriting silently.

- [x] **Step 5: Run focused and compatibility tests**

Run:

```bash
pytest -q tests/unit/cli/test_init_executor.py tests/unit/cli/test_init_ui.py tests/integration/cli/test_init.py tests/integration/cli/test_init_windows_entrypoint.py
```

Expected: all pass with guided and legacy behavior separated explicitly.

## Task 4: Update user-facing documentation and run full verification

**Files:**

- Modify: `docs/getting-started.md`
- Modify: `docs/adapters/codex.md`
- Modify: `docs/adapters/claude-code.md`
- Modify: `docs/plans/2026-07-18-init-wizard-cohesion.md`

- [x] **Step 1: Update the representative guided transcript**

Document the single framed session, icon-only selection emphasis, compact review groups, disclosed local backups, one timed outcome, and status-first reinitialization guidance. Update both agent-adapter pages to describe one backup for a changed existing config, zero backup for a fresh/idempotent install, stale-plan rejection, earliest-backup restoration, and marker-only cleanup when no backup exists. Do not claim package-wide Windows support beyond the existing `init` boundary.

- [x] **Step 2: Run decision and documentation checks**

Run:

```bash
super-harness decision check --changed
super-harness doc check
```

Expected: both exit zero.

- [x] **Step 3: Run formatting, typing, focused tests, and harness verification**

Run:

```bash
ruff format --check src/super_harness/adapters/__init__.py src/super_harness/adapters/install.py src/super_harness/adapters/agent/_settings_merge.py src/super_harness/adapters/agent/codex.py src/super_harness/adapters/agent/claude_code.py src/super_harness/cli/init.py src/super_harness/cli/init_plan.py src/super_harness/cli/init_ui.py src/super_harness/cli/init_executor.py tests/unit/adapters/test_install.py tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py tests/integration/cli/test_init.py
ruff check src/super_harness tests
mypy src/super_harness
pytest -q tests/unit/adapters/test_install.py tests/unit/adapters/test_settings_merge.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py tests/integration/cli/test_init.py tests/integration/cli/test_init_windows_entrypoint.py
super-harness verify
```

Expected: all commands exit zero and `super-harness verify` reports zero failed checks.

## Implementation record

- Atomic settings planning, apply, rollback, locking, symlink refusal, and stale
  lock recovery: `5c46354`, `d8ef131`, `fff2ce7`, `77dfd11`, `0e8235f`.
- Shared Questionary/Rich presentation and follow-up hardening: `91b067f`,
  `ece0ccd`.
- Timed outcome, repeat-init recovery, and single-frame closure: `9451050`,
  `5840d5d`, `49e96b3`.
- User documentation and the verified transcript: `58c07a4`.
- Scoped Ruff formatting: `e38acd7`.
- Code-review fixes for explicit deletion, settings-path ancestry safety, and
  line-mode reviewer-model filtering: `40359d8`, `b8cfcde`.
- Final lifecycle/code review remains open until its receipts finish.

Verification on 2026-07-19:

- `super-harness decision check --changed`: clean.
- `super-harness doc check`: clean.
- `ruff check src/super_harness tests`: clean.
- `mypy src/super_harness`: clean (118 source files).
- Focused adapter/init suite including the Windows entrypoint: 292 passed,
  1 skipped before the formatting-only commit; the exact scoped suite passed
  again with 287 tests after formatting.
- Full repository suite: 2066 passed, 1 skipped.
- `super-harness verify`: passed (5 checks, 0 failed).
- `git diff --check`: clean.
- `ruff format --check ...`: clean (18 scoped Python files already formatted).
