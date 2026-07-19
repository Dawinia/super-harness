# Codex adapter

The Codex adapter wires the OpenAI Codex CLI to super-harness. It is the second
agent adapter in v0.1 and is **experimental** (API stability may change). Claude
Code remains the reference adapter — see [Claude Code adapter](claude-code.md).

It registers three hooks into `<repo>/.codex/hooks.json` (the same shape as
Claude's settings.json hooks block; only the matcher + marker differ): a
**PreToolUse** gate, a **SessionStart** context hook, and a turn-end **Stop**
authoring-conformance advisory.

## Installed hooks

- **PreToolUse gate** — blocks `apply_patch` (and `Edit` / `Write`) edits when the
  current lifecycle state forbids the mutation, via stdout `permissionDecision`.
- **SessionStart** — emits developer context at the start of a session.
- **Stop (turn-end feedback)** — runs the authoring-time conformance check for any
  ratified decision that opted in (`authoring_time: true`) and feeds a failure back
  as a non-blocking advisory.

## Install

```bash
super-harness adapter install codex
```

This writes all three hooks into `.codex/hooks.json` as one settings
transaction. Before the first write, super-harness freezes the original and
desired bytes plus the resolved `super-harness-hook` and `super-harness`
executable paths. Apply revalidates those inputs; settings or PATH drift stops
the install before a backup or write and asks you to review again.

Backup behavior reflects the final transaction, not each hook:

- A fresh `.codex/hooks.json` creates no backup.
- A changed existing file creates exactly one sibling
  `hooks.json.super-harness-backup.<time_ns>` containing the exact original
  bytes.
- An already-current file is not rewritten and creates no backup.

Writes and uninstall use the same exclusive sibling lock and atomic replace.
A concurrent live writer is refused; a lock whose owner process is no longer
alive is reclaimed, while a fresh corrupt lock is left alone until it is old
enough to be safely treated as stale. Symlinked settings files are refused so a
replace cannot unexpectedly modify or detach a linked target. These paths use
stdlib platform checks, including native Windows process-liveness handling.

> **Required trust step.** After `adapter install codex` the gate is **INACTIVE**
> until you run `/hooks` in Codex and trust the super-harness hook. Codex skips
> new or changed hooks until a human trusts them (trust is keyed to the hook's
> hash); if you reinstall or relocate the `super-harness-hook` binary, re-trust it.

On a pre-existing repo, also run `super-harness sync --gitignore` so
`.codex/hooks.json` is ignored by git.

**Coverage caveat.** The PreToolUse gate is registered for the edit tools
`apply_patch` / `Edit` / `Write` (in practice `apply_patch`, Codex's patch
primitive). It does not gate Bash / shell commands or non-shell tools such as
WebSearch, so real-time coverage is narrower than Claude Code's. The CI cold-path
gates back the gap: even an edit the hot path misses is caught before merge.

## What it injects into AGENTS.md

`adapter install codex` injects a super-harness (Codex) subsection into
`AGENTS.md` telling the agent the conventions: a PreToolUse hook gates the
workspace; when a tool call is blocked, run `super-harness status` for the next
step and `super-harness change resume <change_id>` to restore context; never work
around the gate (overriding is a human-only decision, recorded and disclosed at
the merge gate); the receipt-backed review protocol; tracked governance plus
gitignored user-local profiles (including an explicit model and Codex-specific
`reasoning_effort`); and the turn-end authoring check.

The generated protocol is deliberately compatible with a CLI that cannot spawn
an agent. `review prepare` compiles the target and `review begin` writes exact,
caller-owned invocation files; super-harness never starts Codex. The caller runs
every argv/stdin contract outside the harness, unchanged, then uses `review
result import` or `review run fail`. All issued sources finish before editing.
A code-only finding fix is batched with docs follow-ups and prepared once; it does
not repeat plan review. Findings remain scoped to the exact Git target, while
unchanged repository material may be read as supporting architectural context.
Human receipts use draft plus TTY confirmation, which a code agent must not
self-confirm.

Codex reviewer invocations combine `--output-schema` and
`--output-last-message` for the schema-bound verdict with `--json` for a
separate JSONL telemetry stream. The frozen invocation records the stdout
capture path explicitly. On import, super-harness preserves available thread,
usage, duration, and compact tool evidence in the receipt; fields the Codex CLI
does not report remain unknown and do not block review.

## Common issues

- **Edits aren't blocked** — you haven't `/hooks`-trusted the hook yet, or you
  reinstalled/moved the binary and need to re-trust it. The gate is INACTIVE until
  trusted.
- **`.codex/hooks.json` shows up in `git status`** — run `super-harness sync
  --gitignore`.
- **Install says the settings or executable plan is stale** — another process or
  PATH update changed an input after review. Rerun init or `adapter install
  codex`; do not copy the old reviewed plan forward.
- **Install reports an update already in progress or an unsafe symlink** — let
  the other settings update finish, or replace the symlinked settings file or
  parent directory with a regular workspace-local path, then retry. Planning,
  apply, and uninstall reject symlinks from the workspace root through
  `.codex/hooks.json`. Stale owner locks are reclaimed automatically.

## Uninstall

```bash
super-harness adapter uninstall codex
```

Uninstall uses the same lock and atomic-write path as install. If a pristine
pre-install backup exists, it restores the earliest one. If installation began
with no settings file and therefore made no backup, uninstall removes only the
marker-owned PreToolUse, SessionStart, and Stop hooks. Unrelated settings and
hooks are preserved, empty hook scaffolding is pruned, and the settings file is
removed only when nothing user-owned remains. The `.codex/` directory itself is
not deleted.

## See also

- [Claude Code adapter](claude-code.md) — the reference agent adapter.
- [Getting started](../getting-started.md) — the full lifecycle walkthrough.
- [Limitations & FAQ](../limitations.md) — v0.1 boundaries.
