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

The gate enforces that the configured independent review sources produce valid
receipts before the lifecycle proceeds. It does **not** run, spawn, or supervise
the reviewer. A caller invokes an external producer, or a human reviews the
packet; the harness freezes the contract, imports results, and closes the round
deterministically. This is deliberate: the harness is a governor and protocol
compiler, not a reviewer executor.

The configuration separates shared governance from user-local execution:

- **Reviewer roles** are lifecycle positions, for example `plan-reviewer` and
  `code-reviewer`.
- **Reviewer sources** are evidence-provenance labels, for example `codex`,
  `claude`, or `human`; they are not commands or installed agents.
- **Tracked governance** in `.harness/review-governance.yaml` fixes each role's
  participant set, independence requirement, automatic-round ceiling, optional
  distinct-model-family rule, and optional per-role `blocking_severity` (default
  `major`) — the finding severity at or above which a **code-review** round
  rejects; findings below it pass with the finding left open (still surfaced by
  `super-harness report`). Plan review always rejects on any checklist fail.
- **User-local profiles** in the gitignored
  `.harness/review-profiles.local.yaml` select an explicit producer protocol,
  model, cost class, and producer-specific `agent_options` for automated sources.
  No global `effort` vocabulary or implicit model exists.

`super-harness status` is the resume/recovery surface. `review prepare` compiles
a replaceable draft packet with the exact target commit, Git range/files/argv,
checklist, canonical prompt, and profile digests. `review begin` freezes one
round and writes per-run invocation files; the caller runs those invocations
outside super-harness, unchanged. Completed results enter through `review result
import`; crashes enter through `review run fail`. Direct `review approve|reject`
cannot create new evidence. A lifecycle milestone is emitted only after every
required source in the round is terminal. Closure uses the governance frozen at
`review begin`, never a subsequently edited policy. A valid rejecting result
takes precedence over a peer producer failure so discovered findings enter the
fix loop; `execution_failed` is reserved for rounds whose imported results pass
but whose required source set is incomplete. Only those passing results may be
retained for a failed-source retry of the identical frozen contract.

A trustworthy result at an ancestor commit can become that source's incremental
baseline; otherwise the source receives the full in-scope change. Findings are
strictly limited to the frozen target, but a reviewer may read any unchanged
repository material needed as supporting architectural context. If the target
is insufficient, it returns `scope_sufficient: false` with a finding instead of
widening itself to the whole PR.

All committed code fixes, refactors, tests, and docs after a source baseline are
batched into one follow-up assignment. A code-review finding does not cause plan
review by itself. If the fix changes the approved plan, scope, or requirements,
declare that semantic change explicitly with `plan redeclare`; undeclared
plan/spec drift is rejected. All required sources finish before edits resume,
so findings are collected and fixed as one batch. Automatic rounds are bounded;
an exhausted or explicitly expensive round requires one-shot human authorization,
but no token estimate is a hard gate that can make review unavailable.

## super-harness does not spawn your agent

The harness never launches a coding agent or reviewer producer. The relationship
is inverted: your agent or terminal calls the harness (via hooks and CLI), and
the harness gates what the agent may do. For automated review it returns a frozen
argv/stdin/output contract to its caller; for human review it provides compact
inspection metadata plus a short-lived, TTY-confirmed nonce. The caller owns
process execution, while occurrence, scope, receipts, independence, and round
closure are enforced mechanically.

## Two gate paths

- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code (and
  `apply_patch` in Codex, experimental — see [Adapter docs](adapters/)) when the
  current state forbids them. No resident process is on the decision path.
- **Cold path** — CI gates on the PR: metadata + lifecycle-state validation, the
  verification-runner sensor, and the merge gate.
