# Limitations & FAQ

Trust is built on honesty about scope. The following are not in v0.1 — most are
deliberately deferred to a later version; one is blocked by an upstream bug
(flagged explicitly).

## What v0.1 does NOT ship yet

**Framework adapters (v0.2+):**
- Spec Kit framework adapter
- BMAD framework adapter

**Agent adapters (v0.2+):**
- Cursor / Codex / Aider agent adapters — platform hook capabilities vary;
  Claude Code is the reference adapter for v0.1.

**Process / orchestration:**
- Unattended CI auto-review — a headless reviewer that produces the verdict with
  no human or interactive agent present (e.g. shelling out to a headless Claude
  run). v0.1 ships the verdict *recording* path (`review approve | reject | skip`)
  and the agent-driven review protocol (the injected AGENTS.md tells the Claude
  Code agent to dispatch its own reviewer subagent and record the verdict), but it
  does not itself run an LLM review. Tracked as a follow-up.
- Daemon-autonomous event-driven dispatch — v0.1 uses CLI one-shot dispatchers
  (e.g., `super-harness on-merge` dispatches the merged-event sensors).

**Gates not yet wired:**
- Cold-path pre-commit / pre-push gates — need git-hook install infrastructure.
- `gate check pr-open` / `gate check pr-merge` — the underlying machinery ships
  via `pr validate`, but the `gate check` wiring is deferred.

**Blocked by upstream:**
- JSON `permissionDecision: deny` for Edit / Write — super-harness currently uses
  exit-2 block instead, because of an upstream Claude Code fail-open bug on
  Edit/deny.

**Platform / integration:**
- MCP integration — v0.2+.
- Windows support — v0.2+; the optional observer host currently uses POSIX
  `os.fork` and `fcntl.flock`.

## FAQ

**Does super-harness run the review or write the verdict?**
No. It enforces that a verdict exists before the lifecycle advances; the agent or
human produces it. See [Concepts](concepts.md).

**Does it spawn or manage my coding agent?**
No. Your agent calls the harness; the harness gates the agent. See
[Concepts](concepts.md).

**How does a plain-mode change advance past INTENT_DECLARED?**
Plain mode advances with the manual verb `super-harness plan ready`
(INTENT_DECLARED → AWAITING_PLAN_REVIEW); framework adapters emit `plan_ready`
automatically from their artifacts (OpenSpec from `tasks.md`).

**Does the drift gate check my prose docs?**
No. `super-harness doc check` only regenerates docs that have a generator (the CLI
reference, the state-machine diagram). Prose has no ground truth to diff against
and is out of scope by design.
