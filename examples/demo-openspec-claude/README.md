# demo-openspec-claude

A minimal OpenSpec + Claude Code workspace seeded at `IMPLEMENTATION_IN_PROGRESS`.
Opening it in Claude Code lets you watch super-harness's gate, sensors, and
post-merge L1 follow-up wire up around you while you complete one small change.

**Why seeded mid-lifecycle?** v0.1's public CLI does not yet expose verbs to
advance state from `AWAITING_PLAN_REVIEW` to `PLAN_APPROVED` — multi-stage
plan-reviewer tooling is deferred to v0.2 (see the project README's "What v0.1
does NOT ship yet" section). The demo seeds past the plan-review middle states
(`intent_declared` + `plan_ready` + `plan_approved` + `implementation_started`
all pre-emitted in `.harness/events.jsonl`) so visitors can exercise the
`Edit` / `verify` / `done` slice that v0.1 actually ships end-to-end.

This is the canonical onboarding demo for `super-harness` v0.1. It lives
in-tree so the README on GitHub doubles as a tutorial; Phase 16 will reuse this
folder as an end-to-end test fixture.

## Prerequisites

- `super-harness` installed and on `PATH` (`pipx install super-harness`).
- OpenSpec Node CLI (`@fission-ai/openspec@1.3.1+`) — required for the
  framework-adapter verification check (`openspec validate <slug> --strict`).
- `gh` authenticated (`gh auth login`) — required for `super-harness pr` and
  `on-merge` plumbing, not for browsing the seed state.
- Claude Code — required to exercise the PreToolUse gate and SessionStart hook
  end-to-end (which is the point of the walkthrough). To follow along without
  Claude Code, run the listed `super-harness` commands directly from a shell;
  the gate enforcement step won't apply (super-harness's gate hooks into
  Claude Code's PreToolUse event), but the lifecycle / verify / done flow
  still works.

## A note on `.claude/settings.json` and absolute paths

The committed `.claude/settings.json` references the hook binaries by their
**bare names** (`super-harness-hook` and `super-harness`). A real install
writes **absolute paths** resolved via `shutil.which` at adapter-install time
(see `adapters/agent/claude_code.py`), since Claude Code spawns hooks with a
minimal `PATH` and the bare names will not resolve at runtime.

The bare names are deliberate for the committed copy:

- They are stable across machines, so the file is readable as documentation.
- They make the file diff-friendly across contributors.
- Anyone wiring this demo into their own Claude Code session re-runs
  `super-harness adapter install claude-code` (see step 1 below) which rewrites
  `settings.json` with their machine's actual absolute paths.

## What's already done in this folder

- OpenSpec layout under `openspec/`:
  - `changes/demo-add-greeter/proposal.md` + `tasks.md` (the seed change).
  - `specs/greeter/spec.md` (initial capability spec the change targets).
- super-harness initialized: `.harness/state.yaml`, `events.jsonl`,
  `verification.yaml`, `gates.yaml`, `source-paths.yaml`, etc.
- OpenSpec + Claude Code adapters installed: `.harness/adapters.yaml`.
- Lifecycle pre-seeded to `IMPLEMENTATION_IN_PROGRESS`:
  `intent_declared` + `plan_ready` + `plan_approved` +
  `implementation_started` already emitted into `.harness/events.jsonl`. See
  the bold note at the top of this README for why we seed past the
  plan-review middle states.
- `AGENTS.md` rendered with the framework + agent subsections.
- `.claude/settings.json` with PreToolUse + SessionStart hooks (bare-name
  placeholders — see the note above).
- A stub `src/greeter.py` (raises `NotImplementedError`) and a placeholder
  `tests/test_greeter.py` (asserts the stub raises) that the proposal asks
  you to flesh out.

Run `super-harness --workspace examples/demo-openspec-claude/ status` from the
repo root and you should see:

```
demo-add-greeter: IMPLEMENTATION_IN_PROGRESS
  last: implementation_started @ <timestamp>
```

## What you do

1. Re-pin the hook abs paths for your machine:

   ```
   cd examples/demo-openspec-claude
   super-harness adapter install claude-code
   ```

   This rewrites `.claude/settings.json` so Claude Code can actually invoke the
   hooks (replaces the bare names with `shutil.which`-resolved abs paths). Do
   not commit the result back — the bare-name version is the canonical
   committed form.

2. Open the folder in Claude Code. The `SessionStart` hook runs
   `super-harness change resume` which dumps the active change context (the
   `demo-add-greeter` proposal + tasks).

3. Read the proposal and tasks:

   ```
   openspec/changes/demo-add-greeter/proposal.md
   openspec/changes/demo-add-greeter/tasks.md
   ```

4. Edit `src/greeter.py` to implement the proposal — the PreToolUse gate
   allows `Edit` because the seeded state is `IMPLEMENTATION_IN_PROGRESS`
   (per the gate matrix in `super_harness/gates/decisions.py`, mutations are
   only allowed at `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`, and
   `CODE_REVIEW_REJECTED`). Replace the placeholder
   `tests/test_greeter.py` body with real coverage of `greet()`.

5. Advance:

   ```
   super-harness done          # runs verify internally, then on a pass
                               # advances to AWAITING_CODE_REVIEW
   ```

   (`super-harness verify` is also exposed if you want to run the checks
   without advancing state; `done` calls the same checks first, so you do
   not need to invoke `verify` separately.)

   Watch `.harness/events.jsonl` grow and `.harness/state.yaml` track the new
   `current_state`.

## Troubleshooting

- **PreToolUse hook blocks an `Edit` you expected to work**: run
  `super-harness --workspace . status`. v0.1's gate matrix
  (`super_harness/gates/decisions.py`) only allows `Edit` / `Write` at
  `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`, and `CODE_REVIEW_REJECTED`;
  every other state blocks mutations. If the state has regressed (for example
  an `intent_abandoned` event landed) or you're on a fresh clone where the
  seeded events somehow got dropped, re-derive with `super-harness state
  rebuild` and confirm `current_state` is `IMPLEMENTATION_IN_PROGRESS`. If
  the gate config looks right but the hook isn't firing at all, you probably
  skipped step 1 — bare hook names in `.claude/settings.json` will not resolve
  under Claude Code's minimal `PATH`; `super-harness adapter install
  claude-code` repins abs paths.
- **`openspec validate` fails with "command not found"**: install the OpenSpec
  Node CLI (`npm i -g @fission-ai/openspec`). super-harness's
  `verification.yaml` registers `openspec validate ${SLUG} --strict --json` as
  an adapter-provided check; without the binary the check fails.
- **`super-harness status` says "no active change"**: you're running from the
  wrong directory. Either `cd` into `examples/demo-openspec-claude` or pass
  `--workspace examples/demo-openspec-claude/` from the repo root.

## Cross-references

- `../../docs/getting-started.md` — the global onboarding doc.
- `../../docs/adapters/openspec.md` — OpenSpec framework adapter reference.
- `../../docs/adapters/claude-code.md` — Claude Code agent adapter reference.
