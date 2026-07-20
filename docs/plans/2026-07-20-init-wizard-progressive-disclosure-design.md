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

**Status:** v1 shipped and verified. Superseded in part by the **v2 revision**
(continuous clack bar spine) at the end of this document — read the v2 section for
the authoritative current grammar. The sections between here and v2 describe the
v1 progressive-disclosure work that v2 builds on; where v1 and v2 disagree
(notably the role of `│`), v2 wins.

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
  connecting the whole session. **(Reversed by v2: `│` becomes the permanent spine.)**
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

---

# v2 revision — continuous clack bar spine

**Status:** Approved direction (2026-07-20), implementation in progress.

## Why v2

v1 met the line-budget goal but still read as a disconnected list rather than a
designed wizard. Rendering the same representative transcript exposed three
concrete gaps against a clack/CodeGraph-style installer:

1. **Broken spine.** v1 answer lines (`◇ Integrations …`) carried no leading `│`,
   and the review block reused `│` as content indentation. There was no single
   vertical rail from `┌` to `└`, so the session looked like stacked fragments.
2. **Leaked internal vocabulary.** The stage line printed `preflight:` (a state
   machine name) plus `Detection is read-only` (reassurance noise); the review
   printed a bare structural `Files` label. These are the exact "expose the state
   machine, not the task" symptoms v1 set out to remove.
3. **Over-deep review.** `Review changes → Files → Update 11 files → .harness
   configuration (9 files)` is four indentation levels to say "11 files will be
   written."

v2 keeps every v1 behavior (progressive disclosure, delta-only review, outcome
grouping, `--verbose` diagnostics, all safety boundaries) and changes **only the
guided renderer's line composition** so it reads as one connected clack flow.

## Scope boundary: persistent transcript vs. live prompt frames

The spine invariant governs the **persistent guided transcript** — the lines
`RichGuidedRenderer` emits and leaves on screen. It does **not** govern the
**transient live-prompt frames** that Questionary draws while it owns keyboard
input.

Those live frames use Questionary's **own native style**, not the spine. The
existing adapter (unchanged by v2) renders a `◆` qmark plus the message and a
per-option pointer/indicator — a `›` pointer for `select`, checkbox marks for
`checkbox` — with **no** leading `│` on option lines. This design does **not**
claim Questionary already draws clack `│ ● / │ ○` option rows: it does not, and v2
does not change the backend to make it. While a prompt owns input the interaction
is therefore deliberately **off-spine**, and that momentary discontinuity is an
**accepted tradeoff** — keeping the portable, native-keyboard Questionary backend
is worth more than a fully on-spine live frame, which would require forking prompt
rendering (the same reason v1 rejected the "rebuild every prompt as full-screen
sections" alternative).

What makes the *session* read as one connected clack flow is that every prompt runs
with `erase_when_done=True` (the v1 mechanism): the live frame is erased on
completion and the renderer prints the single collapsed `◇` answer line in its
place. So the **persistent** record — the only thing the transcript tests assert,
and the only thing a user scans after answering — is fully on-spine. Live frames
are **explicitly exempt** from the spine invariant and from the transcript tests,
and excluding the Questionary backend from scope is correct because the renderer
never composes those frames. (Resolves plan-review CODX-003 / CLR-005: the earlier
revision wrongly
implied Questionary emits `│`-prefixed option rows.)

## Spine invariant (the core rule)

Applies to every **persistent** guided line. Such a line is exactly one of:

1. a **content line** — a state glyph or the spine `│`, followed by two spaces,
   then content (e.g. `◇  Workspace …`, `│  .harness ×9 · …`); or
2. a **spine separator line** — the bare spine character `│` with **no** trailing
   whitespace, used only as the one blank line between groups; or
3. a **corner** — the `┌` opener or `└` closer (these two alone omit the spine
   prefix). The closer carries the terminal result; when that result is long enough
   to wrap, its continuation lines align **under the result text with spaces**, not
   on the spine — the `└` has closed the box, so no spine follows it. This is the
   one place a wrapped continuation leaves the rail, and it is deliberate (it mirrors
   how the corners themselves sit outside the spine).

**No line ever leaves trailing whitespace** (wrapping strips it), and no interior
content line floats off the rail. The bare-`│` separator (shape 2) is the single
deliberate exception to the "two spaces after the prefix" rule: it carries no
content, so it emits a bare `│` rather than `│  `. Interior continuation (wrapped)
lines use the content prefix `│  ` so wrapped text still hangs on the rail; only the
closer's continuation (above) is space-aligned. (The bare-`│` separator exception
resolves plan-review CODX-004 / CLR-004; the closer-continuation and
trailing-whitespace clarifications resolve code-review CODX-001 / CLR-001.)

Group spacing: exactly one spine separator line (`│`) separates distinct logical
groups (workspace, each answer, the plan/review, the apply-outcome block, each
warning). Consecutive same-kind result rows (the run of `◇` apply outcomes) are
**not** separated. The opener is followed by one separator; the closer is preceded
by one separator.

## Glyph grammar

Two disjoint sets. Only the first is renderer output governed by the spine
invariant, ASCII-mapped, and asserted by tests. The second is Questionary's own
live chrome, listed only so the illustrative live frames are unambiguous.

**Renderer (persistent) glyphs — the exact set the v2 renderer emits:**

- `┌` / `└` — session open / close corners (only these two omit the spine prefix).
  The `└` closer also carries the single terminal result line — success, cancel, or
  failure — distinguished by text and color, exactly as the current code does. There
  is no separate cancellation glyph.
- `│` — the permanent spine and the bare group separator. It no longer doubles as
  content indentation; file details hang directly on the spine.
- `◇` — a completed answer, a completed apply outcome, or the workspace line
  (green).
- `▲` — a warning that needs attention (yellow).
- `✗` — a failed apply step (red).
- `…` — an in-progress apply step, **verbose only** (the `_EVENT_GLYPHS` started
  glyph).

**Table reconciliation (the actual code delta).** The current `_RAIL_GLYPHS` table
maps `RailState.CURRENT → ◆` and `RailState.COMPLETED → ●`, and today `render_stage`
emits the `●` (COMPLETED) glyph for the preflight line — that is exactly why the v1
transcript shows `●  preflight:`. v2 must therefore **reconcile these tables** so the
renderer emits only the set above: the workspace becomes a `◇` completed answer (not
the `●` stage glyph), completed answers/outcomes are `◇` throughout, and the renderer
stops emitting `◆`/`●`. Whether the unused `◆`/`●` entries are removed from
`_RAIL_GLYPHS` or simply left unreferenced, **the invariant is that no glyph the
renderer emits falls outside the set above**, and the ASCII fallback entries in
`_RAIL_GLYPHS`/`_EVENT_GLYPHS` for the emitted glyphs preserve the hierarchy. This
reconciliation is an explicit step in plan Task V2.2. (Resolves plan-review CODX-009
round-6 / CLR-009: the doc no longer claims the tables already match, and names the
required table delta.)

**The renderer never emits `◆`, `●`, or `○`** after this reconciliation, so those are
absent from the emitted renderer grammar and no portability test asserts them.

**Questionary (live, transient) glyphs — out of scope, not renderer output:**

The active-prompt frame is drawn by the unchanged Questionary backend in its own
style — a `?`/`◆`-style qmark, a `›` pointer for `select`, and its native checkbox
marks for `checkbox` — and is erased on completion (`erase_when_done`). These
glyphs are Questionary's, are off-spine by design, and are neither ASCII-mapped by
the renderer nor asserted by the transcript/portability tests. The `◆ … / │ ● / ○`
shapes in illustrative live frames elsewhere in this document are schematic
placeholders for "Questionary's active frame here," not a claim about its exact
characters. (Resolves plan-review CODX-001 round-5 / CLR-007: the glyph grammar and
ASCII map no longer list live-frame glyphs as renderer output.)

## Default interaction contract (v2)

```text
┌  super-harness init
│
◇  Workspace  /work/my-project
│
◇  Integrations  Codex, Claude Code
│
◇  Automated reviewers  Codex (gpt-5.6-sol) · Claude (opus[1m])
│
◇  GitHub  Workflow and PR template
│
◇  Plan  11 files to write
│  .harness ×9 · AGENTS.md · .gitignore
│  5 unchanged hidden — --verbose to see them
│
◇  Harness configuration
◇  Agent integrations
◇  Repository guidance
│
▲  GitHub setup needs one manual step
│  Settings › General › Pull Requests
│
└  Setup complete in 3.1s · Next: super-harness status
```

Wording note: the answer labels (`Integrations`, `Automated reviewers`, `GitHub`),
the apply-outcome labels (`Harness configuration`, `Agent integrations`,
`Repository guidance`), and the terminal result/next text (`Setup complete in …`,
`Next: super-harness status`) are the **existing** strings — v2 does not reword
them, so this stays inside the presentation scope. The deliberate content changes
are exactly: the `◇ Workspace <path>` line replacing the `● preflight: Inspected …`
+ `Detection is read-only` pair; the single `◇ Plan  N files to write` header
replacing the `Review changes` / `Files` / per-action tree; and the spine + group
spacing throughout.

Key differences from v1:

- The stage line becomes a plain `◇ Workspace <path>` answer — no `preflight:`
  prefix and no `Detection is read-only` line.
- The review collapses from a `Review changes` / `Files` / `<action> N files` /
  `<paths>` four-level tree to a single `◇ Plan  N files to write` header with the
  changed paths inlined on one spine line, separated by ` · `, plus the existing
  one-line hidden-count disclosure. When inlined names would exceed the width they
  wrap on the spine.
- The active-question and review-decision blocks are drawn live by Questionary in
  its **native** style (a `◆` qmark, the message, and a `›` pointer / checkbox
  marks — no `│` on option lines), then erased on completion. They are off-spine by
  design and exempt from the spine invariant (see the scope-boundary section). The
  persistent record left behind is the collapsed `◇` answer line, separated from
  neighbours by a bare-`│` line.

## Apply, warning, failure, cancel (v2)

Apply outcomes stay grouped and print once, now on the spine:

```text
◇  Harness configuration
◇  Agent integrations
◇  Repository guidance
```

Warning (still actionable, on the spine, preceded by a blank spine line):

```text
│
▲  GitHub setup needs one manual step
│  Settings › General › Pull Requests
```

Failure preserves completed groups, then names the failed outcome and one recovery
command:

```text
◇  Harness configuration
✗  Agent integrations — codex exited 1
│  Fix the error above, then: super-harness init --force
```

(The `✗` failure detail text is the existing caller-supplied error; only its
placement on the spine is new.) Cancellation before any write ends on the closer
line with the existing text: `└  Setup cancelled` (the terminal result carried by
`└`, as the code already does — no separate cancel glyph, and the `Setup cancelled`
wording and its `InteractiveInitUI._render_cancelled` source are unchanged, so this
stays inside the presentation scope). Repeat-init renders the existing status/force
guidance inside
one compact `▲ Already initialized` block.

## Verbose interaction contract (v2)

`--verbose` keeps the identical spine and grammar; it only adds rows inside the
plan block — preserved/skipped paths and backup paths, each on its own spine line
under the `◇ Plan` header — and may re-enable per-operation apply diagnostics. It
never changes the plan, the confirmation decision, executor inputs/order, writes,
or GitHub behavior, and never reveals secrets.

## Rendering and data boundaries (v2)

- v2 changes the **guided presentation layer**, which spans two in-scope places:
  1. **`RichGuidedRenderer`** — all persistent output methods and their shared
     prefixing helpers: `open_session`, `close_session`, `render_stage`,
     `render_answer`, `render_plan`, `render_event`, `render_validation`, and
     `render_result`. **Every** one of these must emit only the renderer glyph set on
     the spine; none is exempt (this is why `render_validation`, which today emits
     `!`, is named explicitly — it moves onto the spine with the `▲` caution glyph).
  2. **`InteractiveInitUI`'s presentation calls** — only the strings it hands the
     renderer for the workspace line: `prepare_plan` stops sending
     `"Inspected …"` + `"Detection is read-only"` and instead drives a
     `◇ Workspace <path>` answer. Its **collection order, confirmation, plan
     building, frozen integration transactions, and executor construction are
     unchanged**, as are the `GuidedRenderAdapter` protocol, questionary backend,
     `LineInitUI`, and non-interactive/JSON/quiet paths.
- No answer/outcome/result **wording** changes (see the wording note under the
  default contract): the labels and terminal text are the existing strings.
- The renderer owns spine emission centrally: a single helper emits one of the
  three line shapes (glyph+two-space content, spine+two-space content, or a bare-`│`
  separator) and inserts group separators, so no call site can emit a bare *content*
  line, and every renderer method routes through it. This keeps the invariant
  testable in one place.
- Verbosity remains a renderer-only input.

## Verification (v2)

Automated tests prove, in addition to the v1 guarantees (which still hold):

- **Spine invariant:** in the representative default and verbose *persistent*
  transcripts, every non-blank line is either the `┌`/`└` corners, a bare spine
  separator `│` (no trailing whitespace), or a content line whose first two cells
  are a state glyph or `│` followed by two spaces; no other line shape exists. Live
  Questionary prompt frames are out of scope for this assertion (they are erased).
- **Group spacing:** exactly one bare-`│` separator line separates each logical
  group; the run of apply `◇` outcomes has no internal separator lines.
- **De-jargon:** the default transcript contains no `preflight:`, no
  `Detection is read-only`, no standalone `Review changes` or `Files` label.
- **Flattened review:** the default review renders one `◇ Plan  N files to write`
  header with inlined changed paths and the single hidden-count line, and no
  four-level indentation.
- **Parity preserved:** same scripted answers in default and verbose guided modes
  still produce equal `InitPlan` values, equal confirmation decisions, and
  identical executor calls in identical order; rejection performs zero writes;
  fixture secrets never appear.
- **Budget preserved:** the representative default transcript stays within the
  existing 40–60%-fewer-non-blank-lines budget versus the checked-in baseline.
- **Portability preserved:** ASCII, no-color, narrow-width wrapping (on the spine),
  and the Windows entrypoint contracts do not regress.

**On the CodeGraph manual-acceptance gap (plan-review CODX-001):** the
CodeGraph-vs-super-harness comparison is an explicitly *manual* acceptance step and
is not automatable here (it depends on a second product's installer). The automated
proxy for interaction quality remains the line-budget test plus the spine/de-jargon
snapshot assertions above; the manual comparison is recorded as a reviewer checklist
item, not a CI gate. This is called out so the two artifacts no longer imply
automated coverage of that comparison.
