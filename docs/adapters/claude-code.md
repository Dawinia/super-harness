# Claude Code adapter

The Claude Code adapter is super-harness's reference *agent* adapter in v0.1
(Codex ships as an experimental second adapter — see [Codex adapter](codex.md)).
It wires Claude Code's runtime to the
harness by registering three hooks directly in `.claude/settings.local.json`
(the per-machine, conventionally-gitignored settings file — NEVER the committed
shared `.claude/settings.json` — because the hook `command` pins a
machine-specific absolute path that must not be committed):

- a **PreToolUse** hook that invokes the `super-harness-hook` binary on
  every `Edit` / `Write` / `MultiEdit` / `NotebookEdit` tool call (the
  deterministic gate enforcement path),
- a **SessionStart** hook that invokes `super-harness change resume` (no
  slug, so it resolves the active change) and lets Claude Code inject the
  resulting context dump into the session, and
- a **Stop** hook (`super-harness-hook --agent claude-code --event stop`)
  that, when a turn ends, runs the ratified, `authoring_time`-opted-in tier-1
  decision checks once and — on a violation — blocks the stop with a
  **non-blocking advisory** (`{"decision":"block","reason": ...}`) naming the
  decision, so the agent self-corrects before the merge gate. It never undoes an
  edit, never blocks twice (loop-safe via `stop_hook_active`), and fails open.

Claude Code's PreToolUse contract treats process exit code `2` as a hard
deny — that is how the harness blocks an `Edit` / `Write` without a CC
plugin API: `super-harness-hook` decides **in-process** (loads `state.yaml`
once → `PreToolUseGate`), then exits `2` (block, stderr → model) or `0`
(allow). No background daemon required. JSON `permissionDecision: deny` would
be cleaner but is blocked upstream (OPEN-ITEMS #3); exit-2 is the v0.1 fallback.

Auto-detected when the workspace contains a `.claude/` directory. If
`.claude/` is absent at install time, `install_hooks` creates it.

## Capabilities

| Capability | Implementation |
|---|---|
| `detect` | `.claude/` directory exists at the workspace root |
| `install_hooks` | Merges three entries into `.claude/settings.local.json` (PreToolUse + SessionStart + Stop) without clobbering existing entries; snapshots+rolls back on failure |
| `inject_context` | Shells out to `super-harness change resume <slug>` and returns its stdout (best-effort; empty string on non-zero exit) |
| `format_stop_feedback` | Renders a turn-end conformance `Verdict` into Claude Code's Stop-hook `{"decision":"block","reason": ...}` (or `""` when clean) |
| `agents_md_subsection` | Static block explaining the PreToolUse gate + Stop advisory behavior and recovery commands |
| `capabilities` | `pre_tool_use_hook`, `post_tool_use_hook`, `session_start_hook`, `rules_file_injection`, `mcp_server`, `subprocess_execution`, `turn_end_feedback_hook` (all `True`); `session_end_hook`, `pre_commit_hook` (`False`) |

## Install

`super-harness init` auto-installs this hook when it detects a `.claude/`
directory — no separate step needed. To install explicitly (or stand-alone in
a repo that already ran `init`), use `super-harness adapter install
claude-code`; to skip the auto-install during `init`, pass `init --no-agent`.

```bash
super-harness adapter install claude-code
```

Mechanics:

1. Resolves `super-harness-hook` and `super-harness` to absolute paths via
   `shutil.which` — a missing binary raises `RuntimeError` before any write.
   Resolution happens at *install* time (not hook runtime) because Claude
   Code runs hooks with a minimal PATH; a bare reference would fail there.
2. Snapshots `.claude/settings.local.json` (or notes its absence) so the
   install is one transaction; if either merge below raises, the snapshot
   restores.
3. Merges a **PreToolUse** entry: `matcher: "Edit|Write|MultiEdit|NotebookEdit"`,
   `command: "<abs super-harness-hook> --agent claude-code"`, `timeout: 10`.
4. Merges a **SessionStart** entry (no `matcher` → fires on every session
   source): `command: "<abs super-harness> change resume"`, `timeout: 10`.
   Merges a **Stop** entry (no `matcher` → fires on every turn end):
   `command: "<abs super-harness-hook> --agent claude-code --event stop"`,
   `timeout: 10` (the outer bound; the inner authoring check budget is 8s).
5. Persists the row in `.harness/adapters.yaml` and injects the
   `<!-- super-harness agent: claude-code -->` subsection into `AGENTS.md`
   (replacing the no-agent anchor written by `init`).

Each merge backs up `.claude/settings.local.json` to
`settings.local.json.super-harness-backup.<time_ns>` before writing. Re-installs
are idempotent: an unchanged file is not rewritten, no backup produced.

## What it injects into AGENTS.md

The Claude Code subsection teaches the agent that a deterministic gate is
enforced and how to recover when an edit is blocked. Excerpt from
`ClaudeCodeAdapter.agents_md_subsection()` (the gate-block portion; the full
subsection also carries a review-protocol section):

```markdown
<!-- super-harness agent: claude-code -->
### super-harness (Claude Code)

A **PreToolUse** hook is enabled for this workspace. `Edit` / `Write` /
`MultiEdit` / `NotebookEdit` tool calls are blocked by super-harness when the
current change state forbids the mutation (deterministic gate enforcement).

A **Stop** hook also runs a turn-end authoring-time conformance check: when you
finish a turn, any ratified decision that opted in (`authoring_time: true`) has its
check run once, and a failing check blocks the stop with a **non-blocking advisory**
naming the violated decision — so you can self-correct before the merge gate. It never
undoes your edit and never blocks twice (it nudges once per turn); the merge gate is
the authoritative floor.

When a tool call is blocked by the gate:
- Run `super-harness status` to see the current change, its state, and why the
  edit was rejected, plus the next valid step.
- Resume context for a change with `super-harness change resume <change_id>`.
- **If a tool call is blocked by the gate:** stop, and surface the block plus the
  next valid step (`super-harness status`) to the human. Do **not** try to disable
  or work around the gate yourself — overriding it is a human-only decision, and any
  bypass is recorded and disclosed at the merge gate.
<!-- /super-harness agent: claude-code -->
```

The marker comments are load-bearing — install / uninstall locate the block
by exact marker match. Re-run `adapter install claude-code` if it drifts.

## Common issues

- **`adapter install claude-code` exits with `super-harness-hook not found
  on PATH`.** The PreToolUse shim is a separate console entry point that
  ships with the same wheel. Reinstall the package: `pipx reinstall
  super-harness` (or `pip install --force-reinstall super-harness`). Verify
  with `command -v super-harness-hook`.
- **`.claude/settings.local.json` reports `not valid JSON`.** The merge layer
  refuses to splice into a malformed settings file. Open the file, fix the
  JSON, and re-run. The previous super-harness run did not write — the
  snapshot rollback rules out a partial write.
- **`Edit` tool calls always blocked.** Run `super-harness status`. The
  hook output's `reason` field tells you which lifecycle rule fired (no
  active change / change is still in `INTENT_DECLARED` or
  `AWAITING_PLAN_REVIEW` / etc.). The gate only allows mutations at
  `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`, or `CODE_REVIEW_REJECTED`
  (see `src/super_harness/gates/decisions.py`). `AWAITING_PLAN_REVIEW →
  PLAN_APPROVED` advances via `review approve --reviewer plan-reviewer`; when
  `.harness/policy.yaml` requires multiple independent sources, repeat the
  approval with distinct configured `--source` values until the threshold is
  met. That source-threshold gate ships in v0.1; only automatic headless
  reviewer execution is deferred (see [Limitations](../limitations.md)).
  Framework adapters auto-emit `plan_ready` when
  their artifacts exist (OpenSpec watches `tasks.md`). If the gate is genuinely
  malfunctioning, a **human** (never an agent) can apply the emergency override
  documented in getting-started troubleshooting; any bypass is recorded and
  disclosed at the merge gate.
- **SessionStart never injects context.** Confirm the hook is registered
  (`jq '.hooks.SessionStart' .claude/settings.local.json`); if absent, re-run
  `adapter install claude-code`. If present but no slug is active,
  `change resume` exits 0 with empty stdout — start one with `change start`.
- **`adapter uninstall claude-code` leaves entries in `.claude/settings.local.json`.**
  By design — uninstall restores the *earliest* pre-install backup; later
  user edits are preserved. See Uninstall below for details.

## Uninstall

```bash
super-harness adapter uninstall claude-code
```

Mechanics (reverse of install):

1. `on_uninstall()` restores the *earliest*
   `settings.local.json.super-harness-backup.<ts>` backup (the truly pristine
   pre-install copy). If no backup exists the file is left untouched (v0.1
   limitation — clean per-entry removal is tracked as OPEN-ITEMS #9 for a
   future release).
2. Removes the `claude-code` row from `.harness/adapters.yaml`
   (verification.yaml prune is a no-op — Claude Code adds no checks).
3. Removes the `<!-- super-harness agent: claude-code -->` subsection from
   `AGENTS.md`, restoring the no-agent anchor so a future re-install has
   somewhere to land.

The `.claude/` directory is not deleted, even if `install_hooks` created it.

## See also

- [`docs/getting-started.md`](../getting-started.md) — the end-to-end
  walkthrough that installs this adapter alongside `openspec`.
- [`docs/cli-reference.md`](../cli-reference.md) — the full
  `super-harness adapter` command surface.
- [`examples/demo-openspec-claude/`](../../examples/demo-openspec-claude/) —
  a runnable demo wiring Claude Code + OpenSpec through the full lifecycle.
