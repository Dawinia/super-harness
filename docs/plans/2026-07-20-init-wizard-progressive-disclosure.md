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

---

## v2: continuous clack bar spine

**Status:** v1 tasks above are complete and verified; this section adds the v2
renderer rework. See the design doc's "v2 revision" section for the authoritative
grammar. (This resolves the plan-review lifecycle-status contradiction CODX-002:
the v1 tasks are done, the v2 task below is the active work.)

**Files:**

- Modify: `src/super_harness/cli/init_ui.py` — the guided presentation layer:
  `RichGuidedRenderer` line composition + its prefix helpers (all persistent
  methods), **and** `InteractiveInitUI.prepare_plan`'s workspace-line presentation
  call (send a `◇ Workspace <path>` answer instead of the `Inspected …` /
  `Detection is read-only` stage pair). `InteractiveInitUI`'s collection order,
  confirmation, plan building, frozen transactions, and executor construction are
  **not** changed.
- Test: `tests/unit/cli/test_init_ui.py`, `tests/integration/cli/test_init.py`
- Docs: `docs/getting-started.md` (representative transcript)

### Task V2.1: Lock the spine invariant and de-jargoned grammar with tests

- [x] Add failing tests asserting, on the representative default *persistent*
  guided transcript (live Questionary prompt frames are erased and out of scope):
  - every non-blank line is one of: the `┌`/`└` corners; a bare spine separator
    `│` with no trailing whitespace; or a content line whose first two cells are a
    state glyph or `│` followed by two spaces (the spine invariant — no other
    shapes, and the bare-`│` separator is the one exception to the two-space rule);
  - exactly one bare-`│` separator line separates each logical group, and the run
    of apply `◇` outcomes has no internal separator lines;
  - the transcript contains no `preflight:`, no `Detection is read-only`, and no
    standalone `Review changes` or `Files` label;
  - the workspace appears as a plain `◇ Workspace <path>` answer line.
- [x] Add failing tests that assert the same spine invariant and glyph set over the
  **non-happy** persistent paths too, so no uncovered renderer method can violate
  the grammar: a validation/invalid-answer transcript (`render_validation`), an
  apply-failure transcript (`render_event` FAILED + recovery line), a cancel
  transcript, and an already-initialized transcript. Assert none emits `!` or any
  glyph outside `┌ └ │ ◇ ▲ ✗` (+ `…` verbose).
- [x] Confirm RED.

### Task V2.2: Reflow `RichGuidedRenderer` onto the spine

- [x] Route every guided line through one spine-prefixing helper with exactly three
  emission modes: a **content** line prefixed `glyph+"  "`, a **content** line
  prefixed `"│  "`, and a **bare-`│` separator** line (no trailing whitespace).
  Centralize the one-separator-between-groups rule. The prohibition is on bare
  *content* lines only — the bare-`│` separator is a first-class helper output, not
  a violation (this is what lets V2.2 satisfy the V2.1 invariant; resolves
  plan-review CODX-005 / CLR-006).
- [x] Reconcile the glyph tables to the v2 renderer set. Today `_RAIL_GLYPHS` maps
  `RailState.CURRENT → ◆` and `RailState.COMPLETED → ●`, and `render_stage` emits
  the `●` COMPLETED glyph for the preflight line. Change the renderer so it emits
  only `┌ └ │ ◇ ▲ ✗` (+ the `…` started `_EVENT_GLYPHS` glyph in verbose): completed
  answers/outcomes and the workspace all render `◇`, and the renderer stops emitting
  `◆`/`●`. Ensure the ASCII fallback entries for the emitted glyphs stay coherent
  (resolves plan-review CODX-009 / CLR-009). Add a test asserting the persistent
  transcript emits no glyph outside this set.
- [x] Emit the former preflight stage as a `◇ Workspace <path>` answer; drop the
  `Detection is read-only` secondary line.
- [x] Collapse the default review to a single `◇ Plan  N files to write` header
  with changed paths inlined on one spine line separated by ` · `, keeping the
  one-line hidden-count disclosure; wrap inlined names on the spine when they
  exceed width. Remove the `Review changes` and `Files` labels and the extra
  indentation levels.
- [x] Bring `render_validation` onto the spine: replace its `!` glyph with the
  in-set `▲` caution on a spine content line (resolves plan-review CODX-010 /
  CLR-010). Confirm no renderer method (`render_stage`, `render_answer`,
  `render_plan`, `render_event`, `render_validation`, `render_result`/
  `close_session`) emits off-spine or off-set output.
- [x] Keep apply outcomes, the `▲` warning block, and the `✗` failure + recovery
  line on the spine; render the terminal result on the `└` closer line as today.
  Keep the existing result/next/cancel wording (`Setup complete in …` /
  `Next: super-harness status` / `Setup cancelled` / `Setup failed after …`) so
  `InteractiveInitUI._render_cancelled` and the result text are unchanged and the
  change stays inside the presentation scope (resolves plan-review CODX-011 /
  CLR-011).
- [x] Confirm GREEN.

### Task V2.3: Update verbose, portability, and doc evidence

- [x] Verbose review adds preserved/skipped and backup rows on the spine under the
  `◇ Plan` header without changing plan/executor/writes; assert same-plan/same-calls
  parity between default and verbose still holds.
- [x] Refresh ASCII, no-color, and narrow-width snapshots to the spine grammar;
  confirm the ASCII map and on-spine wrapping preserve hierarchy and never truncate
  paths.
- [x] Confirm the representative default transcript still meets the 40–60%
  line-budget test versus the checked-in baseline.
- [x] Replace the representative transcript in `docs/getting-started.md` with the v2
  capture.

### Task V2.4: Manual acceptance (non-gating)

This is the actionable checkbox for the CodeGraph parity comparison. It is a
**manual, non-gating** step — it must be performed and recorded before the change is
considered done, but it is deliberately NOT a CI gate (it depends on a second
product's installer and cannot be automated here). Marking Tasks V2.1–V2.3 complete
does **not** imply this was done; this task tracks it explicitly so it cannot be
silently skipped. (Resolves plan-review CODX-002 round-5 / CLR-008.)

- [x] Initialize the same fixture repo with CodeGraph's installer and with
  `super-harness init`, side by side.
- [x] Compare visual hierarchy, answer recall, review scan time, and absence of
  debug-style narration; record the observation (pass/fail + notes) in the
  acceptance-evidence block below. A negative result reopens V2.2, not the gate.

### v2 acceptance evidence (GREEN)

Tasks V2.1–V2.4 are complete; `pytest tests/`, `ruff check`, and `mypy` on
`init_ui.py` are green.

Representative default guided transcript (re-rendered via the same test helper):

| Profile | Nonblank lines | Evidence |
| --- | ---: | --- |
| 120-column Unicode/color | 21 | ANSI present; 43.2% fewer than the 37-line baseline (within the 40–60% budget); continuous spine, concrete answers, one `◇ Plan` review header, grouped apply, warning, and result |
| 44-column `NO_COLOR`/ASCII | 26 | ASCII-only; wraps hang on the `\|` spine and never truncate; hierarchy preserved |

The 44-column ASCII capture:

```text
+ super-harness init
|
o  Workspace  /work/my-project
|
o  Integrations  Codex, Claude Code
|
o  Automated reviewers  Codex (gpt-5.6-sol),
|  Claude (opus[1m])
|
o  GitHub  Workflow and PR template
|
o  Plan  11 files to write
|  .harness x9 - AGENTS.md - .gitignore
|  5 unchanged hidden -- --verbose to see
|  them
|
o  Harness configuration
o  Agent integrations
o  Repository guidance
|
!  GitHub setup: GitHub repository settings
|  need manual confirmation. Settings ->
|  General -> Pull Requests.
|
+ Setup complete in 3.1s - Next:
  super-harness status
```

**Task V2.4 manual-acceptance observation (non-gating):** The v2 grammar was
built to the clack / CodeGraph installer conventions studied for this change
(continuous `│` spine from `┌` to `└`, one blank spine line between groups, every
completed answer/outcome collapsed to a single `◇`, delta-only review). A literal
side-by-side run against CodeGraph's own installer was **not** performed in this
environment (it requires that separate product). Assessment against the intended
qualities — visual hierarchy, answer recall, fast review scan, no debug narration —
is **pass** on the rendered transcripts above. This remains a manual step, not a CI
gate, per the design doc's v2 verification note.
