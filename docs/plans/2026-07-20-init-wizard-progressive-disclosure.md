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

- [x] Correct the transcript fixture with the faithful `6fcd6b3` capture: actual
  Questionary adapters in a 120x80 tmux pane, Unicode/no-color, with 11 `UPDATE`,
  3 `PRESERVE`, 2 `SKIP`, six successful executor events, and one GitHub warning.
  The pre-change transcript is 37 nonblank lines.
- [x] Remove the repeated Integrations, Automated reviewers, and GitHub sections
  from the default review. The review now begins with `Review changes` and contains
  only the file mutation summary plus the hidden unchanged-file count. Verbose mode
  retains those three sections and every existing action/backup path diagnostic.
- [x] Re-run the representative capture after the correction. At the fixed
  120-column Unicode/no-color budget profile, the transcript is 18 nonblank lines:
  `1 - 18 / 37 = 51.351351%`, within the accepted 40-60% range. The confirmation
  remains the unchanged active `Apply this plan?` Questionary prompt immediately
  after the delta-only review.
- [x] Drive the representative capture through `InteractiveInitUI.prepare_plan`
  and `collect` with a scripted prompt adapter and the real `RichGuidedRenderer`,
  then route executor events and the result through the UI. Remove the stale
  `configuration: Choose integrations and reviews` rail row: the active
  Questionary prompt owns the only current marker while it is open. The actual UI
  path remains 18 lines against the faithful 37-line baseline (51.351351%).
- [x] Prove strict-ASCII output with a combined Windows-style CJK path,
  visible file action, and hidden unchanged rows. The renderer preserves UTF-8
  output unchanged and renders unencodable path characters deterministically,
  for example `C:\\u9879\\u76ee`, instead of raising `UnicodeEncodeError`.

### Reviewable capture evidence

Both profiles use
`_render_representative_progressive_disclosure_transcript` in
`tests/unit/cli/test_init_ui.py`. The helper drives the actual
`InteractiveInitUI.prepare_plan`/`collect` orchestration with a scripted prompt
adapter and the real renderer, substitutes only the stable representative plan,
then sends the representative executor events and result through the UI. Reproduce
the capture counts with:

```bash
.venv/bin/python -c 'import runpy; d=runpy.run_path("tests/unit/cli/test_init_ui.py"); f=d["_render_representative_progressive_disclosure_transcript"]; w=f(width=120, unicode=True, color=True); n=f(width=44, unicode=False, color=False); print(sum(bool(x.strip()) for x in w.splitlines()), chr(27) in w); print(sum(bool(x.strip()) for x in n.splitlines()), n.isascii())'
NO_COLOR=1 .venv/bin/python -c 'import runpy; d=runpy.run_path("tests/unit/cli/test_init_ui.py"); print(d["_render_representative_progressive_disclosure_transcript"](width=44, unicode=False, color=False), end="")'
```

Observed evidence after the correction:

| Profile | Nonblank lines | Evidence |
| --- | ---: | --- |
| 120-column Unicode/color | 19 | ANSI present; concrete answers, delta-only review, grouped apply, warning, and result all visible |
| 44-column `NO_COLOR`/ASCII | 23 | ASCII-only; wrapped values retain hierarchy and no content is truncated |

The narrow capture was:

```text
+ super-harness init
*  preflight: Inspected /work/my-project
|  Detection is read-only
|  Integrations  Codex, Claude Code
|  Automated reviewers  Codex (gpt-5.6-sol),
                        Claude (opus[1m])
|  GitHub  Workflow and PR template
|  Review changes
|  Files
|    Update    11 files
|      .harness configuration (9 files)
|      /work/my-project/AGENTS.md
|      /work/my-project/.gitignore
|    5 unchanged files hidden - use
|    --verbose to inspect
o  Harness configuration
o  Agent integrations
o  Repository guidance
!  GitHub setup: GitHub repository settings
   need manual confirmation. Settings ->
   General -> Pull Requests.
+ Setup complete in 3.1s - Next:
  super-harness status
```
