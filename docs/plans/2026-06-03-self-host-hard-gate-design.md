# Self-host hard gate (HG-D step 2) — design

Date: 2026-06-03
Status: design (pre-implementation)
Scope: turn on the deterministic PreToolUse edit-gate for super-harness's **own**
development (dogfood), Claude Code only. Both preconditions are done: HG-13
(plain `plan ready` emitter, PR #30) and the daemon-start flaky fix (PR #33).

> This document is brainstorming output, not a lifecycle plan — it intentionally
> carries **no** `change:` / `stage:` frontmatter so the framework observer does
> not mint a change from it. The implementation plan is produced separately via
> writing-plans.

---

## 1. Goal & the one real risk

Move super-harness from **observe** mode (HG-D step 1: gate records verdicts,
blocks nothing) to **enforce** mode (PreToolUse hook deterministically blocks
`Edit`/`Write`/`MultiEdit`/`NotebookEdit` when the active change's lifecycle
state forbids it) — on the maintainer's own machine.

**Central risk = self-lock.** The gate is not file-scoped: it applies the single
active change's state to *every* edit. A change parked in a blocking state
freezes all editing — including the edits needed to fix a harness bug. The design
must guarantee a fast, robust, agent-independent way to disable enforcement.

---

## 2. Enforcement model — two layers (roadmap design notes)

super-harness enforcement is **two layers, only one of which is agent-specific.**

- **Layer 1 — edit-time veto (agent-specific, runtime-dependent).** A
  pre-edit/pre-tool hook the agent runtime calls and respects. Fast, real-time.
  Requires the agent to expose a *blocking* pre-mutation hook.
- **Layer 2 — merge-time gate (universal, agent-agnostic).** The CI cold-path
  gate blocks the PR/merge regardless of agent — works for any agent, or a human
  with no agent at all. This is the always-on deterministic backstop.

super-harness does **not** depend on every agent having a hook: Layer 2 is the
universal guarantee; Layer 1 is a per-agent fast-feedback enhancement where the
runtime supports it.

### Which agents can do Layer 1 today (verified 2026-06-03, web-sourced)

| Agent | Edit-time deterministic block? | Notes |
|---|---|---|
| **Claude Code** | ✅ | `PreToolUse`, exit 2 (our reference adapter) |
| GitHub Copilot CLI | ✅ | `preToolUse`, JSON deny, fail-closed (closest analog) |
| Gemini CLI | ✅ | `BeforeTool`, exit 2 / JSON deny |
| Charm Crush / Amp / OpenCode | ✅ | each has a before-tool block hook |
| **Codex CLI** | ❌ (today) | `PreToolUse` exists but `apply_patch` edits are **not** reliably intercepted (openai/codex#16732); only shell calls fire |
| **Cursor** | ❌ | only `afterFileEdit` (post-edit observe); no pre-edit hook |
| Aider | ❌ | no hook layer; lint/test is post-edit reactive |

Caveat: Copilot CLI & OpenCode have **subagent-bypass bugs** (hook not enforced
on task-tool subagents) — load-bearing for a security gate; verify per version.

`AgentAdapter.capabilities["pre_tool_use_hook"]` already expresses "has Layer 1".
Only the `claude-code` adapter is implemented today. **Adding adapters for
Copilot/Gemini/etc., and the Codex/Cursor Layer-2-only fallback handling, is a
separate roadmap track — out of scope here** (see §7).

---

## 3. Gate mechanics (recap of current behavior)

- PreToolUse matcher = `Edit|Write|MultiEdit|NotebookEdit`. **Bash is NOT gated**
  — always available as an escape channel.
- Decision = the active change's lifecycle state (first non-terminal change in
  `state.yaml`, or `SUPER_HARNESS_CHANGE_ID` override). **Not file-scoped.**
- Allow states: `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`,
  `CODE_REVIEW_REJECTED`. **No active change → allow.** All other states block.
- Claude Code shim: exit `2` = block (stderr → model), exit `0` = allow.
- A missing/non-executable hook command → Claude Code **fails open** (allows,
  with stderr noise). So a stale committed absolute path is noisy + silently
  non-enforcing for others, not a hard error.

---

## 4. Changes in this PR (B)

### 4.1 `adapter install claude-code` → write `.claude/settings.local.json`

Today the adapter writes the **shared/committed** `.claude/settings.json` and
pins the **machine-specific absolute path** to `super-harness-hook`. That combo
is wrong for any multi-machine repo (team or open-source): the path is invalid on
other machines → stderr noise + silent non-enforcement (§3).

The hook is inherently per-machine, so it belongs in the per-machine,
conventionally-gitignored `.claude/settings.local.json`. Claude Code merges and
runs hooks from **both** `settings.json` and `settings.local.json` (verified), so
enforcement is identical; only the file (and its commit semantics) changes.

- Change `ClaudeCodeAdapter.install_hooks` / `on_uninstall` target to
  `settings.local.json`.
- Annotate spec §3.5 (it currently says `settings.json`) — docs reconcile.
- Backup/merge/idempotency logic is unchanged (path-parameterized already).

### 4.2 `super-harness init` auto-installs the agent adapter

Currently onboarding is two steps (`init`, then manual `adapter install
claude-code`). For a pure end-user that's friction. `init` should detect the
Claude Code agent (`.claude/` present) and run the agent adapter's
`install_hooks` automatically — one-command onboarding after `pipx install`.

- Default-on when `.claude/` is detected; `init --no-agent` (skip flag) opts out.
- Safety: the gate is **dormant until a change is active** (no active change →
  allow), so `init` never surprises a new user by blocking edits.
- Registers the agent in `adapters.yaml` (same bookkeeping as manual install).

### 4.3 File-based kill switch (the self-lock "big red button")

The hook checks for a sentinel file **at the very start of `_decide`** and
fail-opens (allow) if present — before touching the daemon or state:

```
if (root / ".harness" / "gate-disabled").exists():
    return "allow", "gate disabled via .harness/gate-disabled"
```

Why a file (not env / not "stop the daemon"):

- **`daemon stop` flaps** — `gate_pre_tool_use`'s unreachable branch allows *and*
  fire-and-forget **respawns** the daemon, so the next edit is gated again. Not a
  stable disable.
- **env var** can't be set for an already-running Claude Code session.
- A file toggled by **ungated Bash** (`touch` / `rm`) is instant, stable, and
  works even if the daemon is wedged or `state.yaml` is corrupt (the hook
  short-circuits before reading either).

- Add `.harness/gate-disabled` to `init`'s managed `.gitignore` block (local
  toggle, never committed — same treatment as `.state.lock`).
- The block stderr message names the switch so a blocked agent learns the exit.

### 4.4 Enable on this repo + verify (dogfood)

Run the (now auto) install on this repo so the gate is live for maintainer
development; verify it blocks in a blocking state, allows in `PLAN_APPROVED`, and
that the kill switch + other escapes work. Document the workflow + kill switch in
AGENTS.md / conventions.

---

## 5. Escape hatches (ranked) — self-lock safety

1. **`.harness/gate-disabled`** (new, primary): `touch` to disable, `rm` to
   re-enable. Ungated Bash; daemon-independent; non-flapping.
2. **Ungated Bash + CLI verbs**: move the change to an allow state —
   `super-harness review approve|reject|skip`, etc.
3. **`adapter uninstall claude-code`**: removes the hook entirely (heavier;
   re-install after).
4. *(not a disable)* `daemon stop` — flaps (respawns); documented as such.

---

## 6. Plan-doc chicken-and-egg (no new code; document the workflow)

Because the gate is not file-scoped, `AWAITING_PLAN_REVIEW` blocks editing the
plan doc too. The superpowers adapter resolves this by **minting the change from
the plan doc's frontmatter**:

1. Write the plan doc while **no change is active** → edits allowed.
2. Saving it with the right frontmatter emits `plan_ready` → `AWAITING_PLAN_REVIEW`.
3. To revise: `review reject` → `PLAN_REJECTED` (allows) → edit → re-ready; or
   `review approve` → `PLAN_APPROVED` → write code.
4. All CLI verbs run via **ungated Bash**, so the lifecycle is always drivable.

---

## 7. Out of scope / open items (roadmap)

- **Multi-agent adapters** (Copilot CLI, Gemini CLI, Crush, Amp, OpenCode) and
  **Codex/Cursor Layer-2-only fallback** handling.
- **Framework adapter auto-install in `init`** (sibling to §4.2; superpowers is
  already installed on this repo, so not needed now).
- **"One committed config enforces the whole team"** via a portable bare-name
  command (`super-harness-hook`) instead of an absolute path — needs a
  PATH-robustness decision (restricted-PATH fail-open risk); defer to v0.2.
- **Subagent-bypass caveat** for non-Claude agents (security note for adapters).

---

## 8. Testing strategy (for writing-plans / TDD)

- **§4.1 settings.local.json**: unit tests on `install_hooks` — writes the local
  file; idempotent re-install; backup on pre-existing; `on_uninstall` restore.
- **§4.2 init auto-install**: integration — `init` with `.claude/` present
  installs the hook + registers in `adapters.yaml`; `--no-agent` skips; `.claude/`
  absent is a no-op.
- **§4.3 kill switch**: unit test `hook_entry._decide` — sentinel present →
  `allow` regardless of active-change state; absent → normal decision.
- **§4.4 dogfood**: scripted/manual verification on this repo (block in a
  blocking state, allow in `PLAN_APPROVED`, kill switch toggles enforcement).
</content>
</invoke>
