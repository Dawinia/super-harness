---
change: init-wizard-progressive-disclosure
stage: design
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

# Init Wizard Progressive Disclosure Design

**Status:** Approved direction, awaiting implementation

## Goal

Make the default guided `super-harness init` experience as light and legible as
CodeGraph's installer while retaining super-harness's stronger review and safety
semantics. The representative successful transcript should contain 40–60% fewer
non-blank lines than the current renderer. The baseline is the existing integration
fixture that selects both detected integrations and reviewers, enables GitHub setup,
updates the standard `.harness` files, preserves existing local agent/GitHub files,
and completes with one manual GitHub warning. Detailed unchanged-file information
remains available through the existing global `--verbose` option.

## Problem

The current wizard exposes its internal state machine instead of the user's task:

- completed prompts collapse to generic state such as `done (2 selections)` rather
  than the values the user chose;
- configuration, review, apply, and outcome transitions repeat information without
  adding a decision;
- the review expands unchanged and skipped files alongside real changes;
- the apply phase narrates individual executor steps that are only useful for
  diagnosis;
- a single continuous rail makes the session look like a CI log instead of an
  interactive wizard.

The result is operationally correct but does not meet the product's interaction
quality goal.

## Chosen approach

Use a CodeGraph-style question shell with Hermes-style progressive disclosure:

1. Only the active question is expanded.
2. A completed question collapses to one concrete answer line.
3. The review shows the delta by default, not a complete inventory.
4. Apply output groups successful work by user-visible outcome.
5. Warnings and failures remain visible and actionable in every mode.
6. `--verbose` restores diagnostic detail without changing behavior.

Two smaller alternatives were rejected:

- Hiding only `Preserve` and `Skip` rows leaves duplicate lifecycle narration and
  generic completed answers, so the transcript still feels heavy.
- Rebuilding the entire wizard as independent full-screen Hermes sections would be
  a larger interaction change and would weaken the current portable line-rendering
  model without improving the core decisions.

## Default interaction contract

Completed prompts name their selected values:

```text
◇ Integrations  Codex, Claude Code
◇ Automated reviewers  Codex (gpt-5.6-sol), Claude (opus[1m])
◇ GitHub  Workflow and PR template
```

The review contains only planned mutations plus one disclosure line:

```text
◆ Review changes
│ Update 11 files
│   .harness configuration (9)
│   AGENTS.md
│   .gitignore
│
│ 5 unchanged files hidden · use --verbose to inspect
◆ Apply changes?
│ ● Confirm and continue
│ ○ Back
│ ○ Cancel
```

Apply output reports outcomes rather than executor internals:

```text
◇ Harness configuration
◇ Agent integrations
◇ Repository guidance
▲ GitHub requires manual confirmation
└ Setup complete in 3.1s · Next: super-harness status
```

The guided renderer must not print standalone lines for `Configuration collected`,
`Review planned`, `Plan confirmed`, `Applying setup`, or `outcome`. The closing line
owns the successful result and next action.

## Verbose interaction contract

`super-harness --verbose init` keeps the same prompts and safety boundaries, but the
review additionally shows preserved/skipped paths, backup paths, and uncollapsed
file details. Apply may show per-operation progress and success events. Verbose mode
must never reveal secrets and must not alter the plan or executor inputs.

## Visual grammar

- `◆` marks the one active question or decision.
- `◇` marks a completed answer or completed outcome.
- `▲` marks a warning that requires attention.
- `✗` marks failure.
- `│` is used only inside the active review/question block, not as a permanent rail
  connecting the whole session.
- Color reinforces state but never carries state alone; glyphs and text preserve the
  distinction under `NO_COLOR` and redirected output.

ASCII terminals use equivalent stable characters without changing the hierarchy.
Narrow terminals wrap by display-cell width and indent continuation text beneath
the content, not beneath a glyph.

## Rendering and data boundaries

- `InteractiveInitUI` converts resolved choices into concrete answer summaries and
  asks `GuidedRenderAdapter` to render them. Internal lifecycle transitions remain
  internal.
- The default `render_plan` includes only `CREATE`, `UPDATE`, and `DELETE` rows. It
  collapses multiple `.harness` mutations into one group and reports the total
  hidden `PRESERVE`/`SKIP` count in one dim line.
- Verbose `render_plan` includes all action groups, exact paths, and planned backup
  paths.
- Verbosity is a renderer input only. It must not enter request collection, plan
  construction, confirmation decisions, frozen integration transactions, or
  executor operation construction.
- Guided apply events are accumulated into the existing public outcome groups. A
  group prints once when complete. Warnings and failures bypass collapsing.
- Session close renders the final success/cancel/failure result. It must not add an
  empty closing rail after a completed result.
- Planning, confirmation, frozen inputs, filesystem writes, GitHub behavior, and
  executor order are unchanged.

## Compatibility and failure behavior

- Line mode, non-interactive mode, JSON output, and `--quiet` retain their existing
  deterministic contracts.
- Cancellation before confirmation performs no writes and ends with one concise
  cancellation line.
- Apply failures print the failed public outcome and actionable error, then stop;
  completed groups are not replayed.
- Repeat initialization keeps the existing status/force guidance, but guided mode
  renders it inside one compact result block.
- Unicode/ASCII, color/no-color, wide/narrow, macOS/Linux/native Windows, and
  redirected-output capability variants remain covered.

## Verification

Automated tests will prove:

- completed prompts display selected values rather than selection counts;
- default review omits preserved/skipped paths and reports their hidden count;
- verbose review restores those details;
- the same scripted answers in default and verbose guided modes produce equal
  `InitPlan` values, equal confirmation decisions, and identical executor calls in
  identical order;
- confirmation rejection in either verbosity performs zero executor calls and zero
  writes, while secret-like fixture values never appear in either transcript;
- default apply emits each public outcome at most once and omits internal lifecycle
  labels;
- warning/failure/cancel paths remain visible;
- the defined representative default successful transcript contains between 40%
  and 60% fewer non-blank lines than a checked-in pre-change baseline fixture;
- line, non-interactive, JSON, quiet, ASCII, no-color, narrow-width, and Windows
  entrypoint contracts do not regress.

Manual acceptance compares the same initialized fixture in CodeGraph and
super-harness, checking visual hierarchy, answer recall, review scan time, and the
absence of debug-style narration.
