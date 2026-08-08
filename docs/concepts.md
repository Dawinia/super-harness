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

In PLAN_REJECTED the gate blocks source edits as usual, but **revising the change's
own plan document is authorized**: if `plan ready` recorded the plan doc as a plan
artifact (a marked `.md` in the declared scope), editing that file through the normal
`Edit`/`Write` tools is allowed — the reject-loop revise path needs no shell bypass.
Source files stay blocked; only the recorded marked-`.md` plan doc is editable there.

## Recording what not to do

Some of what you learn while building is negative: don't do this, here be dragons,
doing X also requires Y. super-harness has no separate place for that. A `proposed`
decision record *is* the place.

A proposed record is the state for "we got burned, we haven't settled the rule yet".
Filing one costs nothing — `decision check` excludes anything not yet ratified from every
verdict, so it cannot block a teammate — and it stays out of the tier tally. Its
advantage over a notes file is the **exit**: `ratify` it once you can state the rule (arm
it with a check if the violation has a mechanical signature), or `retire` it once it stops
being true. A note has no exit, which is why notes files fill up with entries nobody can
tell are still valid.

One exception, worth knowing before you act on the above: **filing is free, anchoring is
not.** Attaching a `@decision:` sentinel to a record that is not yet ratified makes it a
dangling-up reference, which is a hard CI failure. Anchoring a record at the site it
describes is otherwise good practice — just wait until the record is ratified.

Two shapes turn up, and only one of them lasts. A **defect** record says "the current
code has this hole"; it names the fix and dies when the fix ships — as a proposed
decision it exits cleanly, whereas as a note it would sit there advising you about a
hole that no longer exists. A **precondition** record says "doing X requires also
doing Y and Z"; no fix retires it, because it constrains code not yet written. The
second kind is what is worth ratifying.

This is not a preference. Standalone lessons-learned repositories have a documented
failure record — NASA's LLIS, audited in OIG report IG-12-012, went years with only
one contributing center and could not be searched usefully by its own knowledge
architect. The forms that survive in the field all impose an entry condition:
an executable detector (Clippy's lints), a subject stable enough not to move
(PostgreSQL's "Don't Do This"), or a named replacement (the AntiPatterns catalogue).
A decision record can carry all three, and adds two those forms lack: a lifecycle
that retires it, and anchors that flag it as suspect when the code it describes
changes.

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
- **The checklist is what the reviewer is asked to judge**, and its items carry
  definitions that are rendered into the frozen prompt. A plan is judged on four
  questions — `architecture` (does the design hold up), `tech-choices` (can the
  chosen mechanisms carry what is assigned to them), `conventions` (does it match
  this repository's ratified decisions and practice), and `spec-coverage` (is
  everything the spec asks for covered). Override the item list per project in
  `.harness/review-checklists.yaml`; ids you invent render without a definition, and
  must be non-blank, printable, single-line text — the same string is the prompt line,
  the verdict schema's `enum` value and part of the digest, so a blank or non-printable
  id is refused rather than carried into all three.
- **Two prompt instructions, one of them role-scoped.** *Every* reviewer is asked
  to be exhaustive rather than stop at the worst finding — that can only add
  findings. Only **plan** review carries the consequence gate: following the
  document literally, would the implementer build the wrong thing, get stuck, or
  would two implementers build different things? If not, it is not a finding, and
  wording, cross-reference numbering, arithmetic and prose consistency never are.
  That gate suppresses findings and its wording was measured on plan review, so
  code review gets none until one is measured for code. Both changes move
  `prompt_digest` and `contract_digest` for **both** roles: a packet prepared
  before an upgrade is stale and must be re-prepared, including an in-flight
  code-review round.
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

## What the gate governs

> The gate governs files that will enter git as product. It does not govern the
> change's own thinking artifacts.

That one sentence explains every narrowing in the state table:

- **Source stays blocked** until a plan is approved — that is the whole point.
- **The change's plan document is writable in `INTENT_DECLARED`**, at any path
  matching `.harness/plan-paths.yaml` (default `docs/plans/*<slug>*.md`). Every
  pattern must contain `{slug}`, so the allowance is bound to the active change and
  can never name `AGENTS.md` or a ratified decision record. That file is *tracked*,
  so widening the allowance is itself a gated edit.
- **The change's scratch area `.harness/scratch/<slug>/` is writable in every
  state**, including terminal ones. It is gitignored, never enters a review bundle,
  and never reaches a merge gate — blocking it would prevent nothing and would only
  push an agent toward the shell to get around the gate.

Blocking a file the reviewer will never see buys no safety; it only makes the
harness something to route around. See [Limitations](limitations.md) for the
residuals.

## Two gate paths

- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code (and
  `apply_patch` in Codex, experimental — see [Adapter docs](adapters/)) when the
  current state forbids them. No resident process is on the decision path.
- **Cold path** — CI gates on the PR: metadata + lifecycle-state validation, the
  verification-runner sensor, and the merge gate.
