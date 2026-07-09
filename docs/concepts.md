# Concepts

## The lifecycle state machine

A *change* moves through a sequence of states. Each transition is caused by a
recorded event; the gate reads the current state to decide what is allowed. The
happy path:

```
INTENT_DECLARED
  → AWAITING_PLAN_REVIEW      (plan_ready, from the framework adapter or `plan ready`)
  → PLAN_APPROVED             (plan_approved, from a reviewer verdict)
  → IMPLEMENTATION_IN_PROGRESS (implementation_started)
  → AWAITING_CODE_REVIEW      (implementation_complete, emitted by `done`)
  → READY_TO_MERGE            (code_review_passed, from a reviewer verdict)
  → ARCHIVED                  (merged, after the PR lands on main)
```

Reviews can send a change back: a rejected plan goes to PLAN_REJECTED (re-emit
`plan_ready` to retry) and a failed code review goes to CODE_REVIEW_REJECTED; a
change can also be ABANDONED. The state machine is fixed, not configurable — see
the generated [state-machine diagram](state-machine.md) for the authoritative
transition matrix.

## super-harness does not review your code for you

The gate enforces that the configured number of independent review source
verdicts is recorded before the lifecycle proceeds. It does **not** run the
review. You, a human reviewer, or an agent-owned reviewer process produces the
verdicts; the harness validates the policy and counts accepted sources. This is
deliberate: the harness is a governor, not a reviewer.

There are three separate axes:

- **Reviewer roles** are lifecycle positions, for example `plan-reviewer` and
  `code-reviewer`.
- **Reviewer strategy** tells the actor who should produce the verdict for that
  role: `subagent`, `human`, or `hybrid`.
- **Reviewer sources** are configured labels, for example `subagent`,
  `external`, or `human`. They are not commands. If `.harness/policy.yaml` sets
  `min_independent: 2`, approvals must arrive from two distinct configured
  `--source` values before the lifecycle milestone is emitted.
- **Reviewer source profiles** are optional execution hints attached to those
  labels: the concrete agent family, the intended context window
  (`bundle-only`, `incremental`, or `full-change`), and that agent's own
  `agent_options`. The options intentionally stay under each source because
  different agents use different names and categories for effort, model, and
  mode. Root-level `effort` / `mode` on a source is rejected; put those knobs
  under `agent_options` with an explicit `agent`.

`super-harness status` surfaces the active role, strategy, accepted source
count, remaining configured sources, and any remaining source profiles. `review
prepare` also embeds the active reviewer's source policy in the prepared bundle
so a docs-only or delta follow-up can stay scoped to the intended context.

## super-harness does not spawn your agent

The harness never launches a coding agent. The relationship is inverted: your
agent calls the harness (via hooks and CLI), and the harness gates what the agent
is allowed to do. Reviews happen because the gate *requires* enough configured
source verdicts before advancing — the content of each review is produced by the
agent or human, the *occurrence* and independence threshold are enforced
mechanically.

## Two gate paths

- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code (and
  `apply_patch` in Codex, experimental — see [Adapter docs](adapters/)) when the
  current state forbids them. No resident process is on the decision path.
- **Cold path** — CI gates on the PR: metadata + lifecycle-state validation, the
  verification-runner sensor, and the merge gate.
