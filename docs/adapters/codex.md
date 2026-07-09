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

This writes the hooks into `.codex/hooks.json`.

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
the merge gate); the review protocol (super-harness enforces the configured
reviewer-source verdict threshold, you produce the verdicts); source profiles
from `status` / `review prepare` (including context and agent-specific
`agent_options`, such as Codex `reasoning_effort`); and the turn-end authoring
check.

## Common issues

- **Edits aren't blocked** — you haven't `/hooks`-trusted the hook yet, or you
  reinstalled/moved the binary and need to re-trust it. The gate is INACTIVE until
  trusted.
- **`.codex/hooks.json` shows up in `git status`** — run `super-harness sync
  --gitignore`.

## See also

- [Claude Code adapter](claude-code.md) — the reference agent adapter.
- [Getting started](../getting-started.md) — the full lifecycle walkthrough.
- [Limitations & FAQ](../limitations.md) — v0.1 boundaries.
