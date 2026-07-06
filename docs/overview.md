# Overview

super-harness is an open-source, CI-first, framework-agnostic, agent-agnostic
harness that makes AI coding deterministic and reliable.

## The problem

Spec-driven tools like Spec Kit, OpenSpec, and Superpowers describe rules in
markdown templates that an agent reads and (probabilistically) complies with.
That is advisory: the agent can drift, skip the review, or edit code the spec
said was out of bounds, and nothing stops it. Human review catches some of this,
but it is late, it doesn't scale to agent-sized diffs, and vigilance drops after
a few good experiences.

## What super-harness adds

A harness embeds those constraints in the environment itself — hooks, CI, git,
processes — so violations are blocked deterministically, not just discouraged.
super-harness sits on top of your existing spec framework and agent; it is not a
replacement for either.

## What v0.1 ships

- **Lifecycle CLI** — `init` / `change` / `plan` / `review` / `implementation` /
  `done` / `on-merge` / `status` / `sync` drive a change end-to-end through a
  fixed state machine (see [Concepts](concepts.md)).
- **Hot-path PreToolUse gate** — decided in-process from one state snapshot;
  blocks Edit / Write tool calls in Claude Code (and `apply_patch` in Codex,
  experimental) when the current lifecycle state forbids them.
- **Cold-path PR gates** — via CI: PR metadata + lifecycle-state validation, the
  verification-runner sensor, and a merge gate.
- **Framework adapters** — OpenSpec, Superpowers (marker-driven, version-agnostic
  discovery), and Plain (fallback). See [Adapter docs](adapters/).
- **Agent adapters** — Claude Code (PreToolUse + SessionStart + Stop hooks,
  injects an AGENTS.md subsection) and Codex (experimental; same hook surface,
  requires a one-time `/hooks` trust step). See [Adapter docs](adapters/).
- **Decision conformance** — make a human decision actually bind the AI's code:
  referential integrity, text-lock (a ratified decision can't be silently
  rewritten), and executable checks (the code can't silently violate it). The
  flagship use is architecture-fitness rules — see [Adopting](adopting.md).
- **Derivable-doc drift gate** — regenerates docs that have a generator (CLI
  reference, state-machine diagram) and blocks if the committed copy drifted.
  Prose docs have no generator and are out of scope by design.
- **Bundled CI workflow** — `super-harness init --setup-github` deploys a GitHub
  Actions workflow and a PR template. All GitHub operations go through the `gh`
  CLI — no webhooks, no PATs, no bot account.

For the full command surface see the [CLI reference](cli-reference.md); for the
internals see [Architecture](ARCHITECTURE.md).

## Relationship to neighboring tools

super-harness is complementary to, not a replacement for, the spec-driven and
agent-wrapping projects in the ecosystem:

| Project | Relationship |
|---|---|
| GitHub Spec Kit / OpenSpec | Complementary — super-harness is an upper CI control layer that can stack on top. |
| Superpowers (obra) | Complementary — ships as a built-in framework adapter (marker-driven, version-agnostic discovery), or run in plain mode. |
| Archon | Different axis — Archon wraps agents (agent-wrapper); super-harness is cross-cutting and CI-first. |
| SpecFact | Complementary — SpecFact adds runtime contracts to OpenSpec specifically; super-harness provides cross-framework lifecycle above. |
| Anthropic Managed Agents | Closed-source hosted; super-harness is open-source self-hosted. |
