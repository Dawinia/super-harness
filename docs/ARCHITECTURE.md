# Architecture

How super-harness is put together, and why. This is the orienting map for
contributors; for command-level detail see [`cli-reference.md`](./cli-reference.md),
for the lifecycle states see [`state-machine.md`](./state-machine.md), and for the
reasoning behind individual subsystems see the design docs under [`plans/`](./plans/).

## 1. What it is, and where it sits

super-harness is a **harness**: the layer around an AI coding agent that turns a
human's decisions into constraints the environment enforces, rather than rules an
agent is merely asked to follow. In control-systems terms it is a *cybernetic
governor* — its regulated target is "what the human ratified," and its job is to
make drift away from that target either impossible to do silently or impossible to
do without leaving a trail.

It is a **layer on top of**, not a replacement for, the agent and the spec
framework:

- The **agent** (Claude Code) and its **process skills** (e.g. superpowers:
  brainstorming → writing-plans → TDD → code review) provide *feedforward guidance*
  — how to do a piece of work well, in-session. These are advisory and ephemeral.
- super-harness adds what those lack: **persistent, mechanical, cross-actor binding**
  — a target state that survives sessions, sensors that hold code to ratified
  decisions, and gates that hold even when an agent skips the soft rails. Agent =
  Model + Harness; the skills and super-harness are different organs of the same
  harness, not competitors.

Everything below is in service of three properties: **(1) required steps can't be
skipped silently, (2) work is forced to align with the layer above it, (3) when the
work betrays a human decision it surfaces back to the human and can't be laundered.**

## 2. The data plane: event-sourced

The system of record is an **append-only event log**, `.harness/events.jsonl`. Every
meaningful act — a change declared, a plan approved, an implementation completed, a
PR merged — is an immutable event. Nothing mutates events; new facts are appended.

Current state is **derived, never authoritative**: `state.yaml` is a cache produced
by a **pure fold** over the event log (`compute_target_state`). Any state can be
reproduced by replaying the log (`super-harness state rebuild`), which is also how
correctness is checked (`state verify`). This is the backbone that makes the gates
trustworthy — there is no hidden mutable state for an agent to corrupt; the trail is
the truth.

```
acts → append → events.jsonl ──pure fold──▶ state.yaml (cache)
                                   │
                                   ▼
                          gates read derived state
```

## 3. The lifecycle state machine

A **change** (identified by a kebab-case slug, carried in PR metadata / artifact
frontmatter — not the branch name) moves through an ordered set of states:
`INTENT_DECLARED → AWAITING_PLAN_REVIEW → PLAN_APPROVED →
IMPLEMENTATION_IN_PROGRESS → AWAITING_CODE_REVIEW → READY_TO_MERGE → ARCHIVED`,
with rejection/abandon branches. Transitions are driven by events; the full
state × event transition table is generated into
[`state-machine.md`](./state-machine.md) (kept in sync by `doc check`).

The lifecycle is **plain-mode complete via the CLI** (`change start`, `plan ready`,
`review approve|reject|skip`, `implementation start`, `done`, `on-merge`).
super-harness records and enforces that a verdict *exists*; it does not produce the
verdict — a human or the agent's own reviewer subagent does.

## 4. Gates: where enforcement happens

Two enforcement paths, sorted by how hard they are:

- **Hot-path (local, fast feedback — in-process):** the Claude Code adapter (and
  the experimental Codex adapter) installs a **PreToolUse** hook
  (`super-harness-hook`) that makes the gate decision
  **in-process** (`core.state_snapshot` → `gates.pre_tool_use.PreToolUseGate`) before
  every `Edit`/`Write`. No background daemon is required; the gate always enforces by
  reading `state.yaml` directly (one parse, never-raises, fail-open only on a missing
  or corrupt workspace). The **optional observer host** (`super-harness observe start`)
  runs watchdog observers that emit lifecycle events, but the gate does not depend on
  it. A file-based kill switch (`.harness/gate-disabled`) disables enforcement;
  `Bash` is never gated, so the switch is always reachable.
- **Cold-path (CI, un-bypassable):** the bundled `super-harness.yml` workflow runs
  the gates that actually guard `main` — `pr-validate`, `verification`,
  `attest-verify`, `decision-check`, `doc-check`, plus `pr-decorate` and `on-merge`.
  Because branch protection makes these required, the hard guarantees live here. The
  hot-path is the cooperative-agent early-warning; CI is the floor.

All GitHub interaction goes through the `gh` CLI — no webhooks, no PATs, no bot
account.

## 5. Adapters

Adapters keep the core framework- and agent-agnostic:

- **Framework adapters** translate a framework's artifacts into lifecycle events.
  `openspec` (emits `intent_declared` from `proposal.md`, `plan_ready` from
  `tasks.md`, contributes a strict-validate verification check); `superpowers`
  (marker-driven, version-agnostic: discovers design/plan artifacts by a `change:`
  frontmatter marker and `stage:`); `plain` (fallback).
- **Agent adapters** wire the harness into an agent's hook surface. `claude-code`
  and `codex` write the PreToolUse + SessionStart + Stop hooks and inject an `AGENTS.md`
  section that tells the agent the conventions (lifecycle, review protocol, scope
  discipline); Codex additionally needs a one-time `/hooks` trust step.

## 6. Verification

`verify` / `done` run the **verification runner** over three layers, all configured
in `.harness/verification.yaml`: **baseline** checks (lifecycle ordering,
scope-vs-plan), **adapter-provided** checks (e.g. OpenSpec strict validate), and
**user** checks. `done` runs `verify` internally and, on pass, advances the change to
`AWAITING_CODE_REVIEW`. Checks are fail-closed and report per-check verdicts.

## 7. Decision conformance — the core "decisions bind code" subsystem

This is super-harness's distinctive niche: holding code to the decisions a human
ratified. It is built as a stack of increasingly strong rungs, all surfaced through
one command, `decision check` (run locally as a sensor + in CI as the gate):

- **Decision records + anchors** — ratified records in `docs/decisions/<id>.md`
  (states: proposed / ratified / superseded / retired); `@decision:<id>` code
  anchors root in a ratified record. `decision check` enforces **referential
  integrity** (anchor → no ratified record = block; ratified record → no anchor =
  warn).
- **Text-lock** (the *decision* end) — `ratify` freezes a SHA-256 of a
  minimally-normalized body; a later body change without re-ratify is an integrity
  violation (highest-priority block). Re-ratify is the only unlock, and it lands in
  the git diff. Closes the "AI softens the ratified claim to excuse its code" hole.
- **Executable checks** (the *code* end) — a decision may carry an inline runnable
  check + a counterexample (both in the body, so the text-lock hash covers them for
  free). At `ratify` a **two-sided bite-test** proves the check is real: it must pass
  on the current tree (run unfiltered, which also self-detects an over-wide check
  that would scan its own counterexample) and fail with the counterexample
  materialized in a throwaway sandbox. `decision check` then runs each check; a
  violation blocks. `--changed` narrows to checks whose anchored files moved (local
  speed; non-git falls back to full — never under-runs).

**The conceptual model** (umbrella design, `plans/2026-06-05-...`): documentation /
knowledge work splits into two arms — a **conformance** arm (feedback / sensors:
freeze a decision, detect drift, with a strength ladder *executable check > derivable
regen-diff > prose rationale*) and a **sedimentation** arm (feedforward: capturing
decaying knowledge). super-harness builds the conformance arm; the sedimentation arm
is a future subsystem, not yet implemented. The **checkability tiers** flow from
this: tier-1 (runnable check → hard anchor, can block); tier-2 (acceptance criterion,
independent re-review — deferred); tier-3 (nothing checkable → recorded as *context*,
never gates). `decision check` prints a `hard:context` ratio so the system can't
quietly decay into all-advisory.

The craft of *writing* a check that bites without false positives (brittle
one-token signatures, scoping the grep to source paths, when to leave a decision
context-only) is taught in the **"Arming a decision"** recipe in the rendered
`AGENTS.md` super-harness section — not duplicated here.

## 8. Derivable-doc drift

Docs that have a generator (the CLI reference, the state-machine table) are the
conformance arm's **regen-diff rung**: `doc check` regenerates and diffs them,
blocking on drift (`--fix` rewrites). Registered in `.harness/derived-docs.yaml`.
Prose docs (this file, the README) have no ground truth and are deliberately **out of
scope** — they are maintained by discipline, not gated. (That gap is the
sedimentation arm's territory.)

## 9. Attestation

`attest write` snapshots a change's complete, ordered event slice to
`.harness/attestations/<slug>.jsonl`; the CI `attest-verify` job blocks a merge
unless every changed file is covered by such an attestation. This is the mechanism
that makes "you skipped a lifecycle step" un-mergeable. (On a solo-owner repo a
review can be self-signed — `attest verify` says so explicitly — so this is a trail +
floor, not a proof of independence; see §11.)

## 10. Design principles (recurring across the code)

- **Event-sourcing**: append-only log is truth; state is a pure-fold cache.
- **Pure / impure layering**: pure logic in `core/` (parse, fold, referential
  checks) stays free of I/O; subprocess / sandbox / git / network live in dedicated
  modules (e.g. `core/check_runner.py`, `engineering/gh.py`, `daemon/` observer host).
  This keeps the load-bearing logic unit-testable and the trust surface small.
- **Fail-closed**: ambiguity blocks. A check that times out, can't run, or emits
  garbage counts as a failure, not a pass.
- **Layered hardness**: soft local sensors for the cooperative agent, hard
  un-bypassable CI gates for the guarantee.
- **Everything through `gh`**: no privileged automation surface.

### Module map

| Area | Where |
|---|---|
| Pure core (records, fold, checks) | `src/super_harness/core/` (`decisions.py`, `decision_check.py`, `check_runner.py`, `doc_check.py`, `anchor_scanner.py`, `frontmatter.py`, `source_scope.py`) |
| CLI verbs | `src/super_harness/cli/` |
| Lifecycle state / events | `src/super_harness/core/` (state fold) + `.harness/events.jsonl` |
| Observer host (optional framework-event watcher) | `src/super_harness/daemon/` |
| Adapters | `src/super_harness/adapters/` |
| Sensors / verification | `src/super_harness/sensors/` |
| Attestation / gh | `src/super_harness/engineering/` |
| Generators for derivable docs | `scripts/gen_*.py` |

## 11. Honest limits

super-harness deliberately does not claim more than it can guarantee
(`plans/2026-06-12-...` §6, and the bedrock note in the design docs):

- **Solo-owner ceiling.** Anyone who controls the repo controls its CI and can
  self-sign reviews. In-repo gates are therefore *forgeable by the owner* — they
  raise the cost of, and leave a trail for, silent drift; they are not a proof
  against a determined owner. The hard guarantees are strongest **across actors**
  (a second contributor is bound by gates they didn't author).
- **AI-written checks can be shallow.** An executable check + counterexample is
  written by the agent; "show it biting" raises the bar but a determined agent can
  craft a weak check. Real teeth, bounded to *what the check actually tests*.
- **The semantic core is human-judged.** Whether a check is *faithful to the
  decision* is a one-time human judgement at ratify time — pushed forward and frozen,
  not eliminated. Pure intent ("why X", "be elegant") has no mechanical ground truth;
  the honest program is to squeeze every checkable part into a mechanical fact and
  fence the irreducible remainder with counterexamples, trails, and forced human
  re-look.
