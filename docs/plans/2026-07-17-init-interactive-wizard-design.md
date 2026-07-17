---
change: init-interactive-wizard
stage: design
---

# Design — Native cross-platform `init` wizard

**Date:** 2026-07-17
**Status:** Approved in design discussion
**Reference:** [`colbymchenry/codegraph`](https://github.com/colbymchenry/codegraph)

## Decision summary

Replace the TTY-only comma-separated selection prompts in `super-harness init`
with a complete guided wizard. The interactive path will use Questionary for
keyboard input and Rich for structured terminal rendering. It will run natively
in Windows Terminal / PowerShell, macOS terminals, Linux terminals, and WSL.

The wizard uses a restrained, Clack-inspired guided rail and five stages:

1. preflight,
2. configuration,
3. review before writes,
4. apply,
5. outcome and next step.

Existing flags and non-interactive behavior remain supported. The change makes
no package-wide claim that every `super-harness` command supports native
Windows; the compatibility guarantee in this change is scoped to `init`.

Native Windows support includes reaching the real installed-wheel entrypoint.
Today that is impossible: importing the eager root command graph reaches
POSIX-only `fcntl` imports before Click can dispatch to `init`. This change must
remove that startup blocker for the `init` path; isolated UI tests are not
sufficient evidence.

## Problem

The current interactive path is a pair of numbered lists followed by free-form
comma-separated input. It is functional but has four user-facing weaknesses:

- Users must translate visible options into a fragile string such as `1,2`.
  Empty tokens, malformed values, and out-of-range numbers become terminal
  errors instead of remaining inside the selection control.
- Detection, choice, validation, writes, and completion are presented as
  unrelated lines rather than one setup flow.
- File writes start immediately after selection. There is no consolidated view
  of the chosen integrations, review producers, models, GitHub setup, or files
  that will change.
- Successful and partial outcomes do not provide a consistent step ledger or a
  strong next action.

The screenshot that motivated this change demonstrates the overall problem: a
basic comma parser is the most prominent interaction in the product's first-run
experience. The local implementation also keeps prompt handling inside
`src/super_harness/cli/init.py`, next to detection, validation, file writes,
adapter installation, GitHub setup, and error rendering. Adding visual behavior
directly to that function would deepen the coupling.

## Goals

- Make first-run setup feel deliberate, legible, and trustworthy.
- Replace free-form multi-select parsing with arrow-key and space-bar selection.
- Detect installed integrations and producers and preselect recommended values.
- Show a complete, editable plan before the first write.
- Show truthful step progress without fake percentages or artificial delays.
- Preserve exact business behavior of the existing scaffold, adapter, review,
  AGENTS.md, `.gitignore`, and GitHub setup operations.
- Provide deterministic plain output for scripts and limited terminals.
- Verify the `init` surface on Ubuntu, macOS, and native Windows CI runners.
- Make `super-harness init` and `super-harness init --help` importable and
  executable from an installed wheel on native Windows.

## Non-goals

- Replacing Click as the command parser.
- Building a full-screen TUI.
- Changing review-governance semantics or maintaining a vendor model catalog.
- Installing Codex, Claude Code, or review-producer executables.
- Automatically rolling back user-owned files after a partial write failure.
- Declaring the entire `super-harness` CLI Windows-compatible.
- Making lifecycle event writing, observation, or daemon commands work on
  Windows as part of this change.
- Reworking unrelated command output.

## Chosen approach

### Questionary for interaction

Questionary provides checkbox, text, select, and confirmation controls on top of
`prompt_toolkit`. It removes the comma-parser failure class and gives the same
keyboard model on Unix and Windows. It supports the project's Python 3.10+
floor.

### Rich for rendering

Rich provides terminal capability detection, styles, progress/status rendering,
and automatic removal of dynamic behavior when output is not a terminal. The
wizard uses only restrained Rich primitives and a small glyph palette; it does
not render wide dashboards or nested panels.

### Alternatives rejected

1. **InquirerPy + Rich.** It offers deeper prompt customization, but the extra
   control is not needed for this five-stage flow and would increase the amount
   of application-owned keybinding and presentation logic.
2. **Click + Rich.** It would improve color, grouping, and progress but retain a
   numeric/free-form multi-select interaction, leaving the primary problem in
   place.
3. **Custom ANSI/raw-key input.** It avoids dependencies at the cost of owning
   terminal modes, cursor movement, Windows console differences, resize,
   cancellation, and test infrastructure. That is the wrong reliability trade.

## Interaction design

### Visual language

The selected direction is a continuous guided rail:

```text
┌  super-harness init
│  Reliable AI coding, from the first change.
│
◆  Coding-agent integrations
│  ◉ Codex          detected · recommended
│  ◉ Claude Code    detected · recommended
│
◇  Review planned setup? Yes
│
●  Created .harness/
●  Updated AGENTS.md
│
└  Ready. Run super-harness change start <slug>
```

Checkbox selection follows the CodeGraph/Clack visual model: a selected option
uses a filled leading indicator and green foreground when color is available;
an unselected option uses an empty indicator and the terminal's normal
foreground. The indicator remains a sufficient selection cue when `NO_COLOR`
or terminal capabilities disable color. A selected row must not inherit
prompt_toolkit's reverse-video `selected` style or gain a full-row background.
The pointer identifies keyboard focus independently from selection state.

The rail supplies continuity, not decoration. Completed questions collapse to a
short answer. Secondary hints disappear first on narrow terminals. Paths and
errors wrap; they are never truncated.

The primary palette is one accent for the rail, green for success, yellow for
warnings or planned modifications, red for failures, and dim text for hints.
Color never carries meaning by itself.

### Stage 1 — Preflight

Display the resolved project path and detect relevant executables before asking
questions. Detection is read-only. Availability is intentionally different for
agent integrations and review producers:

| option class | detected | not detected in interactive UI | explicit flag while not detected |
|---|---|---|---|
| coding-agent integration | preselected; `detected · recommended` | visible, enabled, unselected; `not detected` | accepted; hooks/config may be prepared before the agent is installed |
| review producer | preselected; `detected · recommended` | visible but disabled; `executable not found` | validation error before any write |

This preserves the existing ability to configure a coding-agent integration
for later use while retaining the rule that a selected review producer must be
installed because its explicit local profile is immediately actionable.

The guided UI must present these as two distinct decisions rather than repeat
agent product names. The second checkbox is titled `Automated reviewers —
choose which detected CLIs may review changes`. Its choices describe the role
and execution boundary explicitly: `Codex reviewer — runs via Codex CLI` and
`Claude reviewer — runs via Claude CLI`. Reviewer availability, defaults,
stored producer identifiers, and independence from coding-agent integrations
remain unchanged.

### Stage 2 — Configure

Ask only for values that were not supplied explicitly:

- coding-agent integrations,
- automated reviewers, shown with the CLI that executes each review,
- one explicit model per selected automated review source,
- whether to configure GitHub when not already enabled by the CLI surface,
- append/overwrite decisions for existing GitHub template/workflow files when
  GitHub setup is enabled.

The current `--setup-github` flag remains authoritative. This design does not
silently enable GitHub mutations; if the wizard offers GitHub setup, its default
is off and the resulting choice is shown in the review stage.

Existing GitHub file conflicts are resolved before the final review and stored
in `InitPlan`; the executor never prompts. `--yes` does not answer unresolved
append/overwrite questions. For non-interactive compatibility, existing files
continue to be skipped without `--quiet`, while `--quiet` retains its current
authorization to append/overwrite.

Model fields remain explicit, but interactive modes must not require free-text
model entry. The wizard discovers configured models, selects the only candidate
automatically, or presents multiple candidates as a single-choice prompt.

### Reviewer model discovery

Model discovery is read-only and provider-specific behind a common boundary.
For each selected review source, candidates are collected in this precedence
order and deduplicated by exact model identifier:

1. the current workspace's `.harness/review-profiles.local.yaml`,
2. the CLI's active user model (`~/.codex/config.toml` or
   `~/.claude/settings.json`),
3. additional named CLI profile models when the provider configuration exposes
   them.

Paths are resolved from `Path.home()` so the same logic works with Unix home
directories and native Windows user profiles. Discovery reads only model fields;
it must not copy, render, or persist credentials, environment values, or other
provider settings.

Each candidate carries its exact value and a human-readable origin. One
candidate is adopted automatically and shown in the configuration summary. Two
or more candidates use a Questionary single-select with the highest-precedence
candidate preselected; line mode uses a deterministic numbered choice over the
same candidates. No candidate disables that reviewer with `model not
configured`; neither interactive mode falls back to text entry or a stale
built-in catalog. Malformed or unreadable provider configuration produces a
specific disabled reason and performs no writes.

Explicit `--review-model SOURCE=MODEL` values remain authoritative and bypass
discovery. Non-interactive semantics and the requirement that every selected
automated source has an explicit stored model remain unchanged.

### Stage 3 — Review before writes

Render a compact `InitPlan` summary:

- resolved workspace,
- selected integrations,
- selected review producers and explicit models,
- GitHub setup on/off,
- files and directories to create or update.

On the interactive path, the user can return to configuration, confirm, or
cancel. Until confirmation, the workspace is unchanged. `--yes` skips only this
final interactive confirmation; it does not invent missing selections or
models and does not resolve existing-file decisions.

The confirmation gate is not imposed on non-interactive invocations. A non-TTY
stdin retains the current immediate-apply behavior using explicit flags and
existing defaults. Scripts do not need to add `--yes`.

### Stage 4 — Apply

Apply a sequence of named operations. Completed operations remain visible.
Only genuinely long or external operations receive a spinner. Fast filesystem
writes become success rows immediately; there is no fake percentage.

The initial operation sequence is:

1. scaffold `.harness/` and its canonical subdirectories,
2. write or preserve skeleton configuration,
3. configure review governance and local profiles,
4. install selected agent integrations,
5. render the super-harness AGENTS.md section,
6. inject the `.gitignore` block,
7. perform the existing optional GitHub setup.

The implementation may refine operation granularity for truthful recovery
messages, but it must not reorder behavior in a way that changes the existing
contracts documented in `init.py`.

No operation may prompt. All interactive choices, including GitHub
append/overwrite decisions, are resolved in the plan before apply begins.

### Stage 5 — Outcome

Success output reports completed steps, configured integrations, review-source
count, the initialized path, and one next command.

A partial failure reports:

- completed steps,
- the exact failed step,
- the existing domain-specific error and exit code,
- an actionable recovery command, usually `super-harness init --force` after
  correcting the named file or external-tool problem.

The command does not attempt a broad rollback. AGENTS.md and `.gitignore`
contain user-owned content, and deleting or restoring them after a later failure
could lose concurrent or pre-existing changes. Existing marker-bounded and
atomic-write guarantees remain the protection at individual write boundaries.

## Component design

```text
Click init_cmd
  -> select interaction backend
  -> collect inputs
  -> build and validate InitPlan (read-only)
  -> review / back / cancel
  -> apply InitPlan through InitExecutor
  -> emit InitStep events
  -> render outcome through the selected backend
```

### `init_cmd`

The Click command remains the public entrypoint and owner of existing options.
It resolves root-level context (`--workspace`, `--quiet`, `--json`) and selects
an interaction backend. It does not own prompt widgets or step rendering.

### Lazy root command dispatch for Windows reachability

The root Click group currently imports and registers every command module at
startup. Several unrelated lifecycle/observer modules import POSIX-only
`fcntl`, so native Windows fails before it can select `init`.

Replace eager command imports with a lazy command registry. The root group owns
stable command metadata and imports only the selected command module in
`get_command`; the loaded subtree is then passed through the existing
`rewrap_subtree` behavior. Root and command help remain equivalent, and the
documentation generator continues to load all commands on supported POSIX
builds.

`init.py` currently imports `install_agent_integration` from `cli.adapter`,
whose top-level lifecycle imports also reach `fcntl`. Move those lifecycle-only
imports behind the specific adapter commands that use them, or move the
platform-neutral install helper to a platform-neutral module. The chosen
implementation must prove that loading `init` does not import `core.writer`,
`core.post_emit`, daemon modules, or `fcntl` on Windows.

This is a startup isolation boundary, not a claim that commands loaded later
have acquired Windows-safe locking semantics.

### `InitPlan`

`InitPreflight` carries immutable reviewer-model candidates and per-provider
discovery errors alongside executable detection. A candidate contains only the
review source, exact model identifier, origin label, and precedence; it never
contains raw provider configuration. UI backends consume this common snapshot
and never reopen user configuration files.

An immutable value object contains the resolved workspace, force behavior,
integration choices, review choices, parsed models, GitHub choice, existing
GitHub-file decisions, review-write decision, and planned file actions. Building
and validating it performs no writes.

`--force` uses three deliberately separate review paths:

| mode | existing review files | explicit review flags | behavior |
|---|---|---|---|
| non-interactive preserve | present | none | treat files as opaque; do not parse; preserve bytes exactly |
| non-interactive reconfigure | any | at least one producer/model flag | ignore persisted values as defaults; require the same complete producer/model set as today; producer-only, model-only, or incomplete pairs fail before writes |
| interactive edit | present | optional | parse existing values as editable defaults, apply explicit values, and prompt for unresolved fields before review |

On interactive edit, malformed, unsupported-version, or semantically invalid
existing review files fail preflight without writes. The user may explicitly
choose to reset review configuration and continue from fresh detected defaults;
`--force` alone is not interpreted as silent permission to discard unreadable
review choices.

Detected producer defaults apply only to a fresh interactive init or an
explicit interactive reset; they never replace existing force-rerun choices
silently. The review screen labels review files as `preserved`, `updated`, or
`reset`.

The plan is the handoff between interaction and execution. The executor never
asks questions; the UI never performs scaffold work.

### Interaction backends

Input and rendering capability are separate decisions:

| stdin | output capability | input mode | rendering mode |
|---|---|---|---|
| non-TTY | any | no prompts; explicit flags/current defaults | plain |
| TTY | cursor-capable TTY | Questionary controls | Rich guided rail |
| TTY | redirected, `TERM=dumb`, or cursor-limited | deterministic line prompts: one yes/no per option, no comma parser | plain |

`InteractiveInitUI` owns Questionary prompts and Rich rendering for the full
capability path. `LineInitUI` owns yes/no-per-option prompts plus deterministic
line output for limited terminals. `NonInteractiveInitUI` never prompts. All
three consume the same plan and step-event types. Injectable prompt and render
interfaces keep behavior testable without requiring a real PTY for every unit
test.

`NO_COLOR` disables color but does not by itself disable Questionary or Unicode.
Unsafe output encoding independently selects ASCII glyphs.

### `InitExecutor`

The executor composes the existing scaffold, review, adapter, AGENTS.md,
`.gitignore`, and GitHub helpers. It emits a small closed set of events such as
`step_started`, `step_succeeded`, `step_warned`, and `step_failed` with stable
step identifiers and human-facing details.

These events are process-local presentation events, not lifecycle events and
not additions to `.harness/events.jsonl`.

## Compatibility contract

| Situation | Required behavior |
|---|---|
| stdin and stdout are usable TTYs | Questionary + Rich wizard |
| TTY stdin with redirected/limited output or `TERM=dumb` | Line-mode yes/no prompts + plain output; no comma parser |
| non-TTY stdin / CI | No prompts; immediate apply with current defaults/flags; no `--yes` requirement |
| Some values passed as flags | Accept them and ask only for missing values |
| `--yes` in an interactive mode | Skip only the final write confirmation |
| `--yes` in non-interactive mode | Accepted but not required; behavior otherwise unchanged |
| Detected integration/producer | Preselect and label as detected/recommended on fresh init |
| Unavailable integration | Allow selection; do not preselect; label not detected |
| Unavailable producer | Disable interactively; explicit selection fails before writes |
| One configured model | Select automatically and show its value and origin |
| Multiple configured models | Present a preselected single-choice list; never request free text |
| No configured model | Disable that reviewer with `model not configured` |
| Malformed/unreadable provider model config | Disable that reviewer with a specific reason; write nothing |
| Explicit Cancel before apply | Write nothing; print `Setup cancelled`; exit 0 |
| Ctrl+C before apply | Write nothing; Click-compatible interruption; exit 1 |
| Ctrl+C during apply | Keep completed steps, mark interrupted step, no rollback; exit 1 |
| No color | Preserve symbols and text meaning without color |
| No safe Unicode encoding | Use ASCII rail/status glyphs |
| Narrow terminal | Single column; remove secondary hints before wrapping primary data |
| `--force`, existing review files, no review flags, non-TTY | Preserve governance/profile bytes exactly |

Existing semantics remain unchanged for `--integration`, `--review-producer`,
`--review-model`, `--no-agent`, `--force`, `--setup-github`, and the accepted
but currently no-op `--framework` option. The existing `init` caveat that it
does not emit a machine-readable JSON envelope also remains unchanged.

## Error handling

- Explicit Cancel is a normal interactive no-op with exit 0, not a traceback.
- Ctrl+C before apply retains Click-compatible exit 1 and writes nothing.
- Ctrl+C during apply emits the completed-step ledger, marks the interrupted
  operation, performs no broad rollback, and exits 1.
- Questionary cancellation/interruption is translated at the interaction
  boundary; library-specific sentinel values do not leak into planning or
  execution.
- Validation errors identify the field or selection and keep the user at the
  relevant prompt when interactive.
- Execution errors retain existing exit-code categories and `format_error`
  content. The interactive renderer may structure that content but must not
  flatten domain-specific hints.
- Unexpected encoding or terminal-capability failures fall back to the plain
  renderer rather than blocking initialization.
- Quiet behavior remains quiet for execution advisories. Prompts are still
  visible when interaction is required.

## Testing strategy

### Unit tests

- Independent input/render-mode selection across TTY/non-TTY stdin, redirected
  stdout, CI, `TERM=dumb`, `NO_COLOR`, encoding, and narrow-width combinations.
- Plan construction precedence: explicit flags over detected/prompted values.
- Questionary and line-mode behavior for defaults, toggle/yes-no results,
  model discovery/selection, back, confirmation, explicit cancel, and Ctrl+C.
- Unicode and ASCII glyph selection.
- Step-event ordering and success/warning/failure rendering.
- Executor orchestration with each existing operation replaced by a fake.

### Integration tests

- Confirmation boundary: no workspace change before confirm.
- Cancel boundary: no `.harness/`, AGENTS.md, `.gitignore`, or GitHub write.
- Complete interactive success with injected prompt responses.
- Partial explicit flags ask only for missing values.
- Non-interactive invocation with and without `--yes` immediately applies and
  preserves scriptable behavior.
- TTY stdin + redirected stdout and TTY stdin + `TERM=dumb` use line prompts,
  not default inference and not the comma parser.
- Existing `.harness/`, `--force`, review preservation, adapter failures,
  AGENTS.md failures, `.gitignore` failures, and GitHub setup failures retain
  current outcomes and exit codes.
- The old comma-separated parser is no longer reachable from interactive init;
  selection values come from checkbox results or line-mode yes/no results.
- Existing GitHub files are resolved into the plan interactively; non-TTY skip
  and `--quiet` append/overwrite semantics remain unchanged.
- Fresh init and `--force` rerun cover preserved vs updated review files,
  explicit override precedence, and byte-for-byte non-TTY preservation.
- Non-TTY `--force` covers producer-only, model-only, incomplete producer/model,
  and complete explicit reconfiguration without borrowing persisted values.
- Opaque non-TTY preservation accepts malformed or unknown-version existing
  review files without parsing; interactive edit fails preflight or requires an
  explicit reset choice.
- Model discovery uses isolated home-directory fixtures for Codex and Claude,
  covers one/multiple/no-candidate and malformed/unreadable configurations, and
  never reads the test runner's real user configuration.
- Explicit Cancel, pre-apply Ctrl+C, and during-apply Ctrl+C assert their
  specified exit codes and write/step-ledger boundaries.

### Cross-platform CI

Run the `init`-focused test slice on:

- `ubuntu-latest`,
- `macos-latest`,
- `windows-latest`.

The UI abstraction allows deterministic tests without depending exclusively on
POSIX PTYs. A manual native Windows smoke run covering real arrow, space, enter,
back, cancel, ASCII fallback, and a path containing spaces remains recommended,
but it is non-blocking and may be recorded after authoring completion.

The Windows job must build/install the wheel and invoke the real console script:

```text
super-harness init --help
super-harness --workspace <temp-path-with-spaces> init [explicit non-TTY flags]
```

It must also assert that dispatching `init` does not import `fcntl` or unrelated
POSIX-only command modules. The Windows CI job is a mandatory merge gate;
mock-only UI tests do not satisfy native Windows acceptance. Manual Windows
terminal evidence increases confidence in real key handling but is not required
for `super-harness done` or merge.

The repository's existing full-suite strategy remains unchanged. Any Windows
startup/import blocker that prevents the real `init` path from running is in
scope for this change. Windows blockers reachable only after dispatching an
unrelated command are documented but do not widen this change or weaken `init`
acceptance.

## Acceptance criteria

1. A TTY user sees the approved five-stage guided rail.
2. Detected integrations and producers are preselected and can be toggled with
   arrow keys and space; selected options use a filled indicator plus green
   foreground when color is available, unselected options use an empty
   indicator plus normal foreground, and neither state uses a reverse-video row
   background. With color disabled, the indicators remain distinguishable.
3. Reviewer models come only from the existing workspace profile or detected
   CLI configuration: one candidate is automatic, multiple candidates use a
   select prompt, and no candidate disables the reviewer without text entry.
4. An interactive user can review, return, confirm, or cancel before writes.
5. Confirmation and cancel tests prove the workspace remains unchanged until
   apply begins.
6. `--yes` skips only the final interactive confirmation; non-TTY scripts do
   not require it.
7. Existing flags and non-interactive workflows retain their semantics,
   including GitHub-file skip/quiet behavior and force-rerun review preservation.
8. Plain fallback contains no dynamic control sequences and remains readable.
9. Unicode, ASCII, narrow-terminal, Windows-path, and CRLF scenarios pass.
10. Success and partial failure output name truthful completed/failed steps and
   a next or recovery command.
11. A real installed-wheel `super-harness init` invocation passes on native
    Windows without importing POSIX-only command modules.
12. The init-focused suite passes on Ubuntu, macOS, and Windows CI runners.
13. pytest, ruff, mypy, CLI-reference/doc checks,
    `super-harness decision check --changed`, and `super-harness verify` pass.

## Risks and mitigations

### Two new runtime dependencies

Questionary and Rich increase install surface. Pin compatible lower bounds,
exercise clean-wheel installation, and keep all library-specific behavior
behind the interactive backend.

### Rich and Questionary competing for terminal ownership

Do not keep Rich live rendering active while a Questionary prompt owns the
terminal. Render completed rows before/after each prompt and limit spinners to
executor operations.

### Output drift for scripts

The plain backend is a first-class implementation with tests. Interactive
formatting never becomes the only rendering path.

### Accidental business-logic rewrite

Extract orchestration behind the plan/executor seam while reusing existing
helpers. Existing integration tests remain and are expanded before behavior is
changed.

### Provider configuration drift

Keep Codex and Claude parsing in separate read-only adapters with fixture-based
tests. Unknown keys are ignored, model identifiers remain opaque strings, and a
missing or unsupported shape disables only the affected reviewer. Do not ship a
model catalog or silently switch to a CLI default.

### Overclaiming Windows support

The current eager command graph imports POSIX-only locking before `init` can
start. Lazy command dispatch plus platform-neutral init dependencies are
mandatory scope, and the Windows job exercises the installed console script.
Evidence still applies specifically to `init`; do not add a broad package
support claim unless the rest of the CLI has separate evidence.

## Documentation impact

- Update `docs/cli-reference.md` through its generator if the CLI surface adds
  `--yes` or changes help text.
- Update getting-started documentation with the interactive and non-interactive
  paths.
- Add a short key legend and one representative terminal capture or GIF.
- Keep this design and the later implementation plan marked with the same
  `change: init-interactive-wizard` identity.

## Settled decisions

- Use plain superpowers and super-harness lifecycle; do not use Facio Flow.
- Use Questionary + Rich.
- Support native Windows, macOS, Linux, and WSL for `init`.
- Build the complete wizard, not only the two multi-select prompts.
- Require a review/confirm stage before interactive writes; `--yes` skips that
  interactive confirmation.
- Use the guided-rail visual direction.
- Preserve partial-write truth and recovery guidance instead of broad rollback.
- Add targeted three-OS CI coverage and avoid package-wide Windows claims.
- Isolate `init` from POSIX-only eager imports through lazy command dispatch;
  do not broaden this change into Windows lifecycle-locking support.
- Preserve non-TTY immediate apply; final confirmation is interactive-only.
- Resolve GitHub existing-file decisions before apply and preserve current
  non-TTY skip / `--quiet` overwrite behavior.
- Preserve existing review config on non-TTY `--force` when no new review flags
  are supplied.
- Discover reviewer model candidates from workspace and CLI configuration;
  auto-select one, select among many, and disable the reviewer when none exist.
- Never request free-text models in interactive modes and never maintain a
  built-in vendor model catalog.
