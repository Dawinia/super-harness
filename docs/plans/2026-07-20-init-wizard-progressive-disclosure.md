---
change: init-wizard-progressive-disclosure
stage: plan
tier_hint: Normal
scope:
  files:
    - src/super_harness/cli/init.py
    - src/super_harness/cli/init_ui.py
    - tests/unit/cli/test_init_ui.py
    - tests/integration/cli/test_init.py
    - docs/getting-started.md
    - docs/plans/2026-07-20-init-wizard-progressive-disclosure-design.md
    - docs/plans/2026-07-20-init-wizard-progressive-disclosure.md
---

# Init Wizard Progressive Disclosure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development or superpowers:executing-plans to execute
> this plan task by task. Follow test-driven development for every behavior change.

**Goal:** Reduce the default guided `init` transcript by 40–60% while making each
completed answer concrete and keeping unchanged/diagnostic detail behind
`--verbose`.

**Architecture:** Keep Questionary as the native keyboard backend, but run its
applications with `erase_when_done=True` so generic terminal residues such as
`done (2 selections)` disappear; the Rich renderer then owns the one-line concrete
answer summary. Thread global verbosity only into the guided renderer, collapse the
review to planned mutations, and map executor events into four public outcomes.
Leave planning, frozen transactions, confirmation, operation order, and all
non-guided renderers unchanged.

**Tech stack:** Python 3.10+, Click, Questionary, prompt_toolkit, Rich, pytest, Ruff,
mypy.

## Task 1: Make Questionary yield completed-line ownership

**Files:**

- Modify: `src/super_harness/cli/init_ui.py`
- Test: `tests/unit/cli/test_init_ui.py`

- [x] Add failing adapter tests proving checkbox/select prompts pass
  `erase_when_done=True` and therefore do not retain Questionary's generic
  multi-selection answer line. Keep CPR suppression, native keyboard bindings,
  selected-icon color, and ASCII fallbacks asserted.
- [x] Run the focused tests and confirm RED:

  ```bash
  pytest -q tests/unit/cli/test_init_ui.py -k 'questionary and erase'
  ```

- [x] Pass `erase_when_done=True` through Questionary's application kwargs for
  checkbox/select/model prompts. Do not fork Questionary or take full-screen/live
  terminal ownership.
- [x] Run the focused tests and confirm GREEN.

## Task 2: Collapse resolved choices into concrete answers

**Files:**

- Modify: `src/super_harness/cli/init_ui.py`
- Test: `tests/unit/cli/test_init_ui.py`

- [x] Add failing renderer/UI tests for these completed lines:
  `◇ Integrations  Codex, Claude Code`,
  `◇ Automated reviewers  Codex (MODEL), Claude (MODEL)`, and
  `◇ GitHub  Workflow and PR template` (with truthful `(none)`/skip variants).
  Assert that `done (N selections)`, `Configuration collected`, and duplicate
  configuration/review state lines are absent.
- [x] Run the answer-summary tests and confirm RED.
- [x] Add a library-neutral `render_answer(label, value)` boundary to
  `GuidedRenderAdapter` and `RichGuidedRenderer`. Render summaries only after the
  corresponding choice is fully resolved; combine reviewer producer and model into
  one answer. Remove guided emissions for `Configuration collected`,
  `Review planned`, and `Plan confirmed`; preserve validation/cancel/back behavior.
- [x] Run the answer-summary tests and confirm GREEN.

## Task 3: Make review delta-only by default

**Files:**

- Modify: `src/super_harness/cli/init.py`
- Modify: `src/super_harness/cli/init_ui.py`
- Test: `tests/unit/cli/test_init_ui.py`
- Test: `tests/integration/cli/test_init.py`

- [x] Add failing tests proving `create_init_ui` receives the global verbose count
  as a boolean only for rendering; default guided review lists only
  `CREATE`/`UPDATE`/`DELETE`, collapses `.harness`, and prints one hidden unchanged
  count. Prove verbose review restores exact `PRESERVE`/`SKIP` and backup paths.
- [x] Add paired default/verbose integration tests proving the same scripted
  answers produce equal `InitPlan` values, equal confirmation decisions, identical
  executor calls/order, zero calls/writes on rejection, and no secret-like fixture
  value in either transcript.
- [x] Run the focused review/verbosity tests and confirm RED.
- [x] Thread `bool(ctx.obj.get("verbose"))` through `create_init_ui` into
  `InteractiveInitUI`/`RichGuidedRenderer`. Default `render_plan` prints only
  mutations and one dim hidden-count line; verbose prints all existing detail.
  Do not add verbosity to `InitRequest`, plan construction, or executor inputs.
- [x] Run the focused tests and confirm GREEN.

## Task 4: Collapse apply events and let the result close the session

**Files:**

- Modify: `src/super_harness/cli/init_ui.py`
- Test: `tests/unit/cli/test_init_ui.py`
- Test: `tests/integration/cli/test_init.py`

- [x] Add failing tests for one default line per successful public group:
  `Harness configuration`, `Agent integrations`, and `Repository guidance`;
  GitHub success/warning remains its own group. Assert that started events,
  executor step IDs, `Applying setup`, `outcome:`, and an empty trailing rail are
  absent. Assert verbose mode retains per-operation diagnostics.
- [x] Add failing success/failure/cancel/interruption tests proving the renderer
  closes exactly once with a result line, warnings/failures stay actionable, and
  narrow/ASCII/no-color modes retain hierarchy without truncation.
- [x] Run the focused event/outcome tests and confirm RED.
- [x] Map succeeded executor events into stable public outcome groups, deduplicate
  groups, and let warnings/failures bypass collapsing. Add a renderer result
  boundary so success prints
  `└ Setup complete in ELAPSED · Next: super-harness status`; suppress the generic
  close glyph after any terminal result. Keep expanded event detail in verbose mode.
- [x] Run the focused tests and confirm GREEN.

## Task 5: Lock transcript budget and compatibility

**Files:**

- Modify: `tests/unit/cli/test_init_ui.py`
- Modify: `tests/integration/cli/test_init.py`
- Modify: `docs/getting-started.md`
- Modify: `docs/plans/2026-07-20-init-wizard-progressive-disclosure.md`

- [x] Add the pre-change representative transcript as a named checked-in test
  fixture constant and compare non-blank line counts against the new transcript.
  Assert a reduction between 40% and 60%, plus structural assertions for concrete
  answers, delta-only review, grouped apply, warning, and closing result.
- [x] Run guided compatibility slices:

  ```bash
  pytest -q tests/unit/cli/test_init_ui.py tests/integration/cli/test_init.py
  pytest -q tests/integration/cli/test_init_windows_entrypoint.py
  ```

- [x] Update `docs/getting-started.md` to state that guided mode hides unchanged
  detail by default and that `super-harness --verbose init` expands the review and
  apply diagnostics.
- [x] Run decision/doc/static/full verification:

  ```bash
  super-harness decision check --changed
  super-harness doc check
  ruff check src/super_harness/cli/init.py src/super_harness/cli/init_ui.py \
    tests/unit/cli/test_init_ui.py tests/integration/cli/test_init.py
  mypy src/super_harness/cli/init.py src/super_harness/cli/init_ui.py
  pytest -q
  super-harness verify
  ```

- [x] Manually exercise the same two-agent fixture in a wide color terminal and a
  narrow `NO_COLOR` terminal, compare it with the recorded CodeGraph interaction,
  and save the observed line-count evidence in the implementation summary.
