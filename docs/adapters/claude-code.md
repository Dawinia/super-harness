# Claude Code adapter

The Claude Code adapter is super-harness's reference *agent* adapter and the
only agent adapter shipped in v0.1. It wires Claude Code's runtime to the
harness by registering two hooks directly in `.claude/settings.json`:

- a **PreToolUse** hook that invokes the `super-harness-hook` binary on
  every `Edit` / `Write` / `MultiEdit` / `NotebookEdit` tool call (the
  deterministic gate enforcement path), and
- a **SessionStart** hook that invokes `super-harness change resume` (no
  slug, so it resolves the active change) and lets Claude Code inject the
  resulting context dump into the session.

Auto-detected when the workspace contains a `.claude/` directory. If
`.claude/` is absent at install time, `install_hooks` creates it.

## Capabilities

| Capability | Implementation |
|---|---|
| `detect` | `.claude/` directory exists at the workspace root |
| `install_hooks` | Merges two entries into `.claude/settings.json` (PreToolUse + SessionStart) without clobbering existing entries; snapshots+rolls back on failure |
| `inject_context` | Shells out to `super-harness change resume <slug>` and returns its stdout (best-effort; empty string on non-zero exit) |
| `agents_md_subsection` | Static block explaining the PreToolUse gate behavior and recovery commands |
| `capabilities` | `pre_tool_use_hook`, `post_tool_use_hook`, `session_start_hook`, `rules_file_injection`, `mcp_server`, `subprocess_execution` (all `True`); `session_end_hook`, `pre_commit_hook` (`False`) |

## Install

```bash
super-harness adapter install claude-code
```

Mechanics:

1. Resolves `super-harness-hook` and `super-harness` to absolute paths via
   `shutil.which` — a missing binary raises `RuntimeError` before any write.
2. Snapshots `.claude/settings.json` (or notes its absence) so the install
   is one transaction; if either merge below raises, the snapshot restores.
3. Merges a **PreToolUse** entry: `matcher: "Edit|Write|MultiEdit|NotebookEdit"`,
   `command: "<abs super-harness-hook> --agent claude-code"`, `timeout: 10`.
4. Merges a **SessionStart** entry (no `matcher` → fires on every session
   source): `command: "<abs super-harness> change resume"`, `timeout: 10`.
5. Persists the row in `.harness/adapters.yaml` and injects the
   `<!-- super-harness agent: claude-code -->` subsection into `AGENTS.md`
   (replacing the no-agent anchor written by `init`).

Each merge backs up `.claude/settings.json` to
`settings.json.super-harness-backup.<time_ns>` before writing. Re-installs
are idempotent: an unchanged settings file is not rewritten, no backup is
produced.

## What it injects into AGENTS.md

The Claude Code subsection teaches the agent that a deterministic gate is
enforced and how to recover when an edit is blocked. Verbatim from
`ClaudeCodeAdapter.agents_md_subsection()`:

```markdown
<!-- super-harness agent: claude-code -->
### super-harness (Claude Code)

A **PreToolUse** hook is enabled for this workspace. `Edit` / `Write` /
`MultiEdit` / `NotebookEdit` tool calls are blocked by super-harness when the
current change state forbids the mutation (deterministic gate enforcement).

When a tool call is blocked by the gate:
- Run `super-harness status` to see the current change, its state, and why the
  edit was rejected, plus the next valid step.
- Resume context for a change with `super-harness change resume <change_id>`.
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
- **`.claude/settings.json` reports `not valid JSON`.** The merge layer
  refuses to splice into a malformed settings file. Open the file, fix the
  JSON, and re-run. The previous super-harness run did not write — the
  snapshot rollback rules out a partial write.
- **`Edit` tool calls always blocked.** Run `super-harness status`. The
  hook output's `reason` field tells you which lifecycle rule fired (no
  active change / change is still in `INTENT_DECLARED` / etc.). Advance the
  change first (e.g. `super-harness plan ready <slug>`); do not work around
  the block by editing settings.json.
- **SessionStart never injects context.** Confirm the hook is registered
  (`jq '.hooks.SessionStart' .claude/settings.json`); if absent, re-run
  `adapter install claude-code`. If present but no slug is active,
  `change resume` exits 0 with empty stdout — start one with `change start`.
- **`adapter uninstall claude-code` leaves entries in
  `.claude/settings.json`.** Uninstall restores the *earliest* timestamped
  backup. If no backup exists (the file was unchanged at install time so
  no backup was written) entries remain — remove the entry whose command
  contains `--agent claude-code` and the one containing `change resume` by
  hand.

## Uninstall

```bash
super-harness adapter uninstall claude-code
```

Mechanics (reverse of install):

1. `on_uninstall()` restores the *earliest*
   `settings.json.super-harness-backup.<ts>` backup (the truly pristine
   pre-install copy). If no backup exists the file is left untouched
   (documented v0.1 limitation — clean per-entry removal is Phase 9+).
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
