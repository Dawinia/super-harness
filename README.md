# super-harness

> The missing CI layer for spec-driven AI coding workflows.

**English** | [简体中文](README.zh-CN.md)

## What is super-harness?

An open-source, CI-first, framework-agnostic, agent-agnostic harness that makes
AI coding deterministic and reliable. Spec-driven tools describe rules in
markdown that agents read and (probabilistically) comply with; a harness embeds
those constraints in the environment itself — hooks, CI, git, processes — so
violations are blocked deterministically, not just discouraged. It sits on top of
your existing spec framework and agent; it is not a replacement for either.

See the [Overview](docs/overview.md) for the problem it solves, what v0.1 ships,
and how it relates to neighboring tools like Spec Kit, OpenSpec, and Superpowers.

## Install

```bash
pipx install super-harness
brew install gh && gh auth login   # gh is a prerequisite for init --setup-github
```

## Quickstart

Bootstrap a repo and watch the gate block an out-of-lifecycle edit — the whole
point of the tool:

```bash
pipx install super-harness
cd your-repo && super-harness init            # create the .harness/ data plane
super-harness adapter install claude-code     # wire your agent (auto-done by init if .claude/ exists)
#   or, for Codex:  super-harness adapter install codex   → then run /hooks in Codex to trust it
super-harness change start "my-change"        # → INTENT_DECLARED
# now have your agent (or you) try to edit code → the gate blocks it,
# because no plan review has happened yet. That block is the product.
```

That is the shortest path to *seeing* super-harness work. The full arc — install
a framework adapter, get the plan reviewed, implement, verify, review, merge — is
the 10-minute [Getting started](docs/getting-started.md) walkthrough. To inspect a
pre-seeded non-trivial `.harness/` state without running anything, see the in-tree
demo [`examples/demo-openspec-claude/`](examples/demo-openspec-claude/). The
walkthrough also shows how to configure reviewer sources and `min_independent`
when a change needs more than one independent review verdict, including
agent-specific source profiles such as Codex `reasoning_effort` or a subagent
runner's own `effort` knob.

## Links

- [Documentation index](docs/README.md)
- [Overview](docs/overview.md) — what it is, what v0.1 ships, neighboring tools
- [Getting started](docs/getting-started.md) — full end-to-end walkthrough
- [Concepts](docs/concepts.md) — lifecycle, and what the harness does *not* do
- [Adopting](docs/adopting.md) — lock architecture rules in your own project
- [Limitations & FAQ](docs/limitations.md)
- [Agent adapters](docs/adapters/) — [Claude Code](docs/adapters/claude-code.md) · [Codex](docs/adapters/codex.md) (experimental)
- [CLI reference](docs/cli-reference.md)
- [Architecture](docs/ARCHITECTURE.md)

## License

MIT — see [`LICENSE`](LICENSE).
