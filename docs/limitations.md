# Limitations & FAQ

Trust is built on honesty about scope. The following are not in v0.1 — most are
deliberately deferred to a later version; one is blocked by an upstream bug
(flagged explicitly).

## What v0.1 does NOT ship yet

**Framework adapters (v0.2+):**
- Spec Kit framework adapter
- BMAD framework adapter

**Agent adapters (v0.2+):**
- Cursor / Aider agent adapters — platform hook capabilities vary. Claude Code is
  the reference v0.1 adapter; Codex ships as an experimental second adapter (see
  [Codex adapter](adapters/codex.md)).

**Custom plugins (v0.2+):**
- Custom sensors / gates / framework adapters loaded from `.harness/*.yaml`
  (`path` + `class`). v0.1 is builtin-only: loading contributor Python in-process
  needs a trust/sandbox model, which lands with the plugins themselves in v0.2. A
  non-builtin entry is rejected — it is not executed.

**Process / orchestration:**
- Reviewer execution and supervision. super-harness compiles frozen Codex CLI and
  Claude CLI invocation contracts, parses completed outputs, and records receipts;
  it never starts, monitors, retries, or kills those processes. The caller owns
  execution. This boundary is intentional, not a pending headless-executor feature.
- Host-native subagents are not a universal review primitive. The CLI does not
  assume `spawn_agent`, Claude `Task`, or another in-session subagent API exists.
  Evidence comes from configured external producer protocols or a human receipt.
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
No. It freezes scope, prompt, requested profile, and output schema; the caller
runs an external producer or a human reviews the packet. super-harness then
validates and imports the result receipt before deterministic round closure. See
[Concepts](concepts.md).

**Does cost control impose a token limit?**
No. Review usefulness comes first. The protocol controls exact inspection scope,
explicit models/options, silent retry, and automatic-round multiplication. Usage
telemetry is optional and a missing estimate never makes review unavailable.

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
