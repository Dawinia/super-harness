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

The gate enforces that a review *verdict is recorded* before the lifecycle
proceeds. It does **not** run the review. You — or, per the injected AGENTS.md
protocol, your agent's own reviewer subagent — produce the verdict; the gate only
checks one exists. This is deliberate: the harness is a governor, not a reviewer.

The per-reviewer **strategy** is set in `.harness/policy.yaml`:

- `subagent` — an interactive agent dispatches its own reviewer subagent.
- `human` — a person records the verdict (pick this when a token budget rules out
  subagent review).
- `hybrid` — a mix.

`super-harness status` surfaces the active strategy.

## super-harness does not spawn your agent

The harness never launches a coding agent. The relationship is inverted: your
agent calls the harness (via hooks and CLI), and the harness gates what the agent
is allowed to do. Reviews happen because the gate *requires* a verdict before
advancing — the content of the review is produced by the agent or human, the
*occurrence* of the review is enforced mechanically.

## Two gate paths

- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code when the
  current state forbids them. No resident process is on the decision path.
- **Cold path** — CI gates on the PR: metadata + lifecycle-state validation, the
  verification-runner sensor, and the merge gate.
