# Quickstart agent adapters + Chinese README — Design

> Two adopter-facing docs improvements requested 2026-07-06 (follow-up to the
> docs-IA cut PR#73): (1) the README Quickstart should show how to wire the agent
> adapters (Claude Code + Codex), and (2) ship a Chinese `README.zh-CN.md` linked
> from the README. (1) also **resolves the deferred F2** from PR#73 — whether the
> Codex agent adapter is a shipped v0.1 surface — in the "shipped (experimental)"
> direction, so all agent-adapter framing across the docs is reconciled to match.
> Companion plan: `2026-07-06-quickstart-adapters-and-zh-readme-plan.md`.

## 1. Problem

**Quickstart gap.** The PR#73 Quickstart bootstraps a repo and shows the gate
blocking an edit, but never shows how the reader wires their agent to the gate.
For Claude Code `init` auto-installs the hook when `.claude/` exists, but that is
implicit; for Codex there is no on-ramp at all in the Quickstart.

**F2 — inconsistent Codex framing (from PR#73 review).** `CodexAdapter` is a
registered built-in (`register_builtin("codex", CodexAdapter)`, installable via
`adapter install codex`) with working PreToolUse / SessionStart / Stop hooks
(capabilities `pre_tool_use_hook`, `post_tool_use_hook`, `session_start_hook`,
`turn_end_feedback_hook` all true; `post_tool_use_hook` spike-verified to fire
under `codex exec`, 2026-07-01). But the docs still frame Codex as "v0.2+" and
call Claude Code "the only agent adapter shipped in v0.1." That contradiction was
deferred in PR#73; adding Codex to the Quickstart forces it. **Decision (user,
2026-07-06): treat Codex as a shipped, experimental v0.1 agent adapter and
reconcile every agent-adapter mention to match.**

**No Chinese entry point.** The audience during the private phase includes
Chinese-reading collaborators/adopters; the landing README is English-only.

## 2. Decisions

- **Quickstart shows agent-adapter install** for both Claude Code and Codex, with
  Codex's required `/hooks` trust step called out inline. It stays a copy-paste
  block that still ends in the gate blocking an edit.
- **Codex is a shipped v0.1 agent adapter (experimental).** New peer adapter doc
  `docs/adapters/codex.md` (mirrors `claude-code.md`), and every "v0.2+ / only
  agent adapter" mention updated: `overview.md`, `limitations.md`,
  `getting-started.md`, `docs/adapters/claude-code.md`, `docs/README.md`,
  `docs/ARCHITECTURE.md`. Codex's real caveats are stated honestly, not hidden:
  the `/hooks` trust step (gate INACTIVE until trusted) and the coverage caveat
  (Codex PreToolUse intercepts only simple shell + `apply_patch`, narrower than
  Claude Code; the CI cold floor backs the gap).
- **Cursor / Aider stay v0.2+** — they are genuinely not registered built-ins.
- **Chinese README = one file `README.zh-CN.md`** (translation of the 57-line
  landing README), with a language-switcher link at the top of both files. The
  `docs/` narrative layer stays English (translating the whole tree is a large
  ongoing sync burden — the "two homes" problem — and out of scope here).

## 3. Design

### 3.1 Quickstart (README.md)

Add an agent-adapter install step; keep the visible payoff. Shape:

```bash
pipx install super-harness
cd your-repo && super-harness init          # create the .harness/ data plane
super-harness adapter install claude-code   # wire the agent (auto-done by init if .claude/ exists)
#   or, for Codex:  super-harness adapter install codex   → then run /hooks in Codex to trust it
super-harness change start "my-change"       # → INTENT_DECLARED
# now have your agent (or you) try to edit code → the gate blocks it
```

A one-line pointer sends readers to the per-agent adapter docs for detail
(`docs/adapters/claude-code.md`, `docs/adapters/codex.md`).

### 3.2 Codex adapter doc (docs/adapters/codex.md)

New peer to `claude-code.md`, same section skeleton (Title / Capabilities /
Install / What it injects into AGENTS.md / Common issues / See also), authored
from the authoritative facts in `src/super_harness/adapters/agent/codex.py`:

- Registers PreToolUse + SessionStart + Stop hooks into `.codex/hooks.json`
  (same shape as Claude's settings.json hooks; matcher `^(apply_patch|Edit|Write)$`).
- **Required trust step:** after `adapter install codex`, the gate is INACTIVE
  until the human runs `/hooks` in Codex to trust the hook (trust keyed to the
  hook's hash; re-trust after reinstall/relocate). On a pre-existing repo also run
  `super-harness sync --gitignore` so `.codex/hooks.json` is ignored.
- **Coverage caveat:** Codex PreToolUse intercepts only simple shell +
  `apply_patch`; it does not see WebSearch or other non-shell/non-MCP tools, so
  real-time coverage is narrower than Claude Code. The CI cold floor backs the gap.
- **API stability: experimental (v0.1).**

### 3.3 Agent-adapter framing reconciliation

| File | Change |
|---|---|
| `docs/overview.md` | "Agent adapter — Claude Code (…)" → "Agent adapters — Claude Code and Codex (experimental)", each with its hook surface; link both adapter docs. |
| `docs/limitations.md` | Remove Codex from the "Agent adapters (v0.2+)" list → "Cursor / Aider agent adapters"; drop the "Claude Code is the reference adapter" phrasing (Codex now also ships). |
| `docs/getting-started.md` | "Agent adapters: `claude-code`." → "`claude-code` and `codex` (experimental)." (Walkthrough stays on the OpenSpec + Claude Code pair.) |
| `docs/adapters/claude-code.md` | "the only agent adapter shipped in v0.1" → "the reference agent adapter in v0.1 (Codex ships as an experimental second adapter — see [Codex adapter](codex.md))." |
| `docs/README.md` | Adapter-docs pointer: "OpenSpec, Claude Code, Plain" → add Codex; add a narrative/reference link to `codex.md`. |
| `docs/ARCHITECTURE.md` | Descriptive line "`claude-code` writes the PreToolUse + SessionStart hooks…" → note `codex` also wires the same hook surface (one-clause add; the line is illustrative, not a false claim, but kept consistent). |

### 3.4 Chinese README (README.zh-CN.md)

Full translation of the slimmed English README (positioning / install /
Quickstart / links). Both files get a top language switcher:
`[English](README.md) | [简体中文](README.zh-CN.md)` (and the reciprocal). The
Chinese README's links point at the same English `docs/` (not translated).

## 4. Non-goals

- No translation of the `docs/` narrative layer (English stays canonical).
- No Cursor / Aider adapter (not shipped).
- No code / behavior change; no new decision record.
- No docs-site generator.

## 5. doc-refs gate note

Adapter *identifiers* (`claude-code`, `codex`, `openspec`, `superpowers`, `plain`)
are lowercase/hyphen → safe inline. The proper nouns "Codex", "Claude Code",
"Cursor", "Aider", "WebSearch" must be plain text (no inline backticks).
`super-harness doc check` is the backstop. The Chinese README uses the same
identifier conventions.

## 6. Verification

- `super-harness doc check` = 0, `super-harness decision check` = 0.
- All relative links resolve (incl. the new codex.md and the two-way README
  language switcher).
- No agent-adapter mention anywhere in live docs still says Codex is v0.2+ or that
  Claude Code is the only v0.1 agent adapter (grep).
- Quickstart smoke: the `adapter install claude-code` path still reaches
  INTENT_DECLARED and blocks an edit (already proven in PR#73; re-confirm the
  command sequence parses).
- Full suite green (docs-only; no code delta).

## 7. Scope (self-host)

New: `README.zh-CN.md`, `docs/adapters/codex.md`, design + plan.
Modified: `README.md`, `docs/overview.md`, `docs/limitations.md`,
`docs/README.md`, `docs/getting-started.md`, `docs/adapters/claude-code.md`,
`docs/ARCHITECTURE.md`, `docs/concepts.md`. (12 files.)
