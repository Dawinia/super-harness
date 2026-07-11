---
change: review-contract-compiler
stage: plan
tier_hint: Normal
scope:
  files:
    - .harness/attestations/review-contract-compiler.jsonl
    - .harness/policy.yaml
    - AGENTS.md
    - docs/adapters/claude-code.md
    - docs/adapters/codex.md
    - docs/adapters/plain.md
    - docs/cli-reference.md
    - docs/concepts.md
    - docs/getting-started.md
    - docs/plans/2026-07-11-review-contract-compiler.md
    - private/CAPABILITY-CONVERGENCE-LEDGER.html
    - private/CAPABILITY-CONVERGENCE-LEDGER.md
    - private/NEXT-SESSION-PROMPT.md
    - private/OPEN-ITEMS.md
    - src/super_harness/adapters/agent/claude_code.py
    - src/super_harness/adapters/agent/codex.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/review.py
    - src/super_harness/core/review_bundle.py
    - src/super_harness/core/scope_match.py
    - src/super_harness/engineering/review_contract.py
    - src/super_harness/engineering/reviewer_policy.py
    - tests/integration/cli/test_init.py
    - tests/unit/adapters/test_claude_code.py
    - tests/unit/adapters/test_codex.py
    - tests/unit/cli/test_review.py
    - tests/unit/cli/test_review_prepare.py
    - tests/unit/cli/test_review_verdict_gate.py
    - tests/unit/core/test_review_bundle.py
    - tests/unit/core/test_scope_match.py
    - tests/unit/engineering/test_review_contract.py
    - tests/unit/engineering/test_reviewer_policy.py
---

# Review Contract Compiler

## Goal

Make the normal code-agent review path cost-disciplined by compiling repository
state into an exact review contract. A docs-only or code-review-fix follow-up
must not naturally trigger a whole-PR review, inherit the main session's XHigh
effort, or repeat plan review when the approved plan, scope, and requirements
did not change.

This change is a Normal lifecycle slice. It does not execute reviewers.

## Problem

PR #78 proved that the multi-independent-source gate works, but exposed four
execution failures outside the gate itself:

1. A code-review finding fix was treated as a reason to repeat plan review even
   when plan, scope, and requirements were unchanged.
2. Docs-only and follow-up changes were split into repeated broad reviews rather
   than one committed scoped delta.
3. Reviewer sessions inherited the main session's XHigh reasoning instead of
   using each source profile's Medium, agent-specific options.
4. `bundle-only` and `incremental` source profiles still received a bundle whose
   natural interpretation was the whole PR.

The current `review prepare` output is a deterministic manifest, but it only
describes the full `base...HEAD` in-scope file set and embeds source profiles as
passive hints. It does not compile an executable inspection range or a canonical
review task for each source.

## Product Decisions

### Code-agent-first CLI

The primary consumer is a code agent. Every new argument, field, and prompt can
create another decision branch, so the normal path gains no new CLI verb or
argument. The existing path remains:

```text
status (only on resume/uncertainty)
review prepare
review approve|reject --source ... --verdict-file ...
```

This slice does not introduce a cross-lifecycle action schema. Existing `status`
output remains the resume and recovery surface. On the normal review path,
`review prepare` compiles the participant order, exact assignments, and canonical
prompts into the bundle so the code agent can dispatch without another policy
decision.

### Review responsibility versus inspection target

A follow-up reviewer remains responsible for the mergeability of the whole
change, but its default inspection target is every committed in-scope change
since that source's latest trustworthy review. The target is not limited to the
lines named by prior findings; it includes all code, tests, docs, and refactors
committed after the source baseline.

The reviewer may read unchanged affected context, but findings must be caused by
the target delta, expose a dependency or regression made relevant by it, or
resolve a prior finding. Unrelated pre-existing issues are out of this review.

### Plan-review routing

`CODE_REVIEW_REJECTED` defaults to a code-review follow-up. A finding does not
route back to plan review by itself. If the correct fix changes the approved
plan, scope, or requirements, the code agent uses the existing explicit command:

```text
super-harness plan redeclare <change> --reason "..."
```

An undeclared plan/spec mutation is a validation error, not an inferred plan
review. A redeclaration invalidates prior code-review baselines.

### Participants and runner profiles

The repository configures the normal source set once; the code agent does not
select reviewers at runtime. A role-level `participants` list names source
profiles in execution order. A source profile binds one source label to one
runner and its opaque, agent-specific options.

```yaml
reviewers:
  sources:
    subagent:
      agent: task-subagent
      context: incremental
      agent_options:
        effort: medium
    external:
      agent: codex
      context: bundle-only
      agent_options:
        reasoning_effort: medium
        sandbox: read-only
  code-reviewer:
    participants: [subagent, external]
```

The harness never discovers installed agents, chooses from an agent pool, maps
one agent's option names to another's, or silently falls back to the main session
configuration.

### Temporary current bundle

The bundle remains a gitignored derived file at:

```text
.harness/pending-reviews/<change>/<reviewer>.bundle.json
```

Each prepare replaces the current bundle for that change/reviewer. Durable
review evidence lives in verdict events and committed attestations, not in old
bundle files. Both structured code-review approval and rejection reject stale
verdicts whose digest no longer matches the current prepared target.

## Architecture

### Policy resolution

Extend `ReviewerIndependencePolicy` with resolved participants.

- `participants` must be a distinct list of configured source labels.
- When both `participants` and `min_independent` are present, their counts must
  match. New generated config may infer the threshold from participants.
- Without participants, exactly `min_independent` configured sources are an
  unambiguous legacy participant set.
- More configured sources than the threshold without participants is a config
  ambiguity, not an invitation to select the first YAML entries.
- Historical single-source policies without source profiles retain the existing
  strategy path and do not receive source-specific assignments.

### Baseline resolution

Add a pure engineering-layer resolver over append-ordered change events.

For each participant source:

1. Find its latest result for the reviewer.
2. An approval is complete. A structured rejection is complete only when its
   checklist covers every required item. Bare/incomplete rejection is partial.
3. Partial rejection invalidates older baselines for that source.
4. A complete result is usable only when its automatically recorded
   `reviewed_head` is an ancestor of current HEAD and no later plan redeclaration
   invalidated it.
5. A usable baseline produces an incremental inspection range. Otherwise the
   assignment uses the full in-scope change.
6. A source profile with `context: full-change` always uses full-change.

Old events without `reviewed_head` remain valid history but cannot establish an
incremental baseline.

### Contract assembly

Keep core bundle assembly deterministic and adapters-free. Add Git helpers that
resolve HEAD, verify ancestry, and list in-scope changes between explicit refs.
The engineering-layer contract compiler combines the core bundle with policy,
events, and source assignments.

The bundle keeps existing compatibility fields and adds:

```yaml
target_head: <sha>
plan_review_required: false
assignments:
  - source: subagent
    agent: task-subagent
    context: incremental
    agent_options: {effort: medium}
    inspection:
      mode: incremental
      base: <reviewed-head>
      head: <target-head>
      files: [...]
      diff_argv: [git, diff, <base>..<head>, --, ...]
    prompt: <canonical prompt>
```

The bundle is a manifest, not an embedded patch. `diff_argv` is an argv array,
not a shell string. `bundle-only` means the prepared target is authoritative; it
does not forbid read-only access to directly affected context.

### Canonical reviewer prompt

Render the prompt from structured contract data. It must tell the reviewer to:

- review only the assigned target delta;
- read unchanged files only for direct affected context;
- report only target-related or prior-finding issues;
- continue through the full assigned target after finding a blocker;
- return a partial rejection when scope is insufficient instead of expanding;
- return a verdict only, without editing or invoking harness verdict commands.

The main code agent applies `agent_options` when creating an independent reviewer
session, passes the generated prompt unchanged, collects every participant's raw
verdict, and only then records the results.

### Verdict recording

Do not add verdict-file fields. The CLI derives and records `reviewed_head` from
the current prepared bundle when a source verdict is accepted.

- Code-review approve remains checklist-complete and freshness-required.
- Structured code-review reject gains the same digest freshness check.
- A reject with incomplete checklist remains legal, but is partial and cannot
  establish a baseline.
- A stale verdict is not recorded and cannot become review evidence.
- Existing `review skip` semantics remain unchanged.

## Error Semantics

`review prepare` fails closed for an invalid reviewer state, dirty in-scope tree,
ambiguous participant policy, undeclared plan/spec drift, or an unresolvable Git
target. Missing/partial/non-ancestor source baselines are not errors; they compile
to full-change assignments.

Out-of-scope drift remains visible but excluded from assignments. If the drift
belongs to the change, the code agent must redeclare scope.

An unsupported runner option is a dispatch failure outside the harness. The main
agent reports it and must not silently inherit XHigh or switch sources.

## TDD Plan

### Task 1: Participant policy

1. Add failing policy tests for participants, inferred threshold, duplicate or
   unknown participants, threshold mismatch, ambiguous legacy source sets, and
   old single-source compatibility.
2. Implement the smallest parser/dataclass/payload changes.
3. Run focused policy tests and refactor only after green.

### Task 2: Explicit Git-range primitives

1. Add failing scope-match tests for HEAD resolution, ancestor checks, and
   explicit `base..head` in-scope file lists/argv ordering.
2. Implement fail-closed Git helpers without changing existing full-diff digest
   semantics.
3. Run core scope tests.

### Task 3: Per-source baseline resolver

1. Add failing tests for complete approval/rejection baselines, partial
   invalidation, old events without heads, non-ancestor heads, plan redeclaration,
   and source isolation.
2. Implement the pure event-history resolver.
3. Run the focused resolver tests.

### Task 4: Review contract compiler

1. Add failing prepare/contract tests for initial full review, follow-up delta,
   mixed per-source targets, one batched docs/fix delta, exact argv, source options,
   canonical prompt constraints, plan-review routing, and fixed current bundle.
2. Implement contract assembly and wire `review prepare` without new CLI options.
3. Preserve existing bundle compatibility fields and JSON/human output.
4. Run focused core and prepare tests.

### Task 5: Verdict metadata and stale rejection

1. Add failing CLI tests proving accepted source verdict events carry the prepared
   target HEAD.
2. Add failing tests proving stale structured code rejects are refused, incomplete
   fresh rejects remain legal/partial, and bare rejects retain compatibility.
3. Implement shared freshness/target metadata validation.
4. Run review and verdict-gate tests.

### Task 6: Generated policy and agent protocol

1. Add failing init tests for participant-based generated policy.
2. Add failing adapter tests for the canonical execution discipline: apply exact
   source options, dispatch all assignments, collect verdicts before recording,
   do not repeat plan review for code-only fixes, and do not expand scoped targets.
3. Update generated AGENTS content and adapter docs.
4. Update concepts/getting-started/CLI reference without duplicating the prompt's
   detailed decision logic.

### Task 7: Verification and self-host lifecycle

1. Run focused tests after each red-green task.
2. Run decision checks at natural checkpoints.
3. Run the full configured verification suite.
4. Commit the in-scope implementation before code review.
5. Prepare one code-review bundle and dispatch `subagent` plus `external` using
   their configured Medium options and exact assignments.
6. If findings are code-only, batch all fixes and perform one scoped follow-up;
   do not repeat plan review. Redeclare only if plan/scope/requirements change.
7. Write and verify attestation, update the ledger and next-session handoff, then
   request user confirmation before merging to `main`.

## Acceptance Criteria

1. A first review without a source baseline compiles a full in-scope assignment.
2. A complete source result at ancestor HEAD compiles one incremental assignment
   containing all later committed in-scope files, including docs.
3. A partial/stale/non-ancestor result cannot establish a baseline and compiles
   full-change for that source.
4. A code-review follow-up reports plan review as not required unless the change
   was explicitly redeclared; undeclared plan/spec drift is rejected.
5. Every participant assignment carries exact agent-specific options and never
   inherits main-session effort through the bundle.
6. `bundle-only`/`incremental` assignments contain exact range, files, and argv;
   their prompt forbids whole-PR expansion and unrelated pre-existing findings.
7. All follow-up commits between baseline and current HEAD are batched into one
   assignment per participant; there is no docs-only/finding-specific mode.
8. The normal CLI path adds no verb or argument and remains compatible with old
   single-source policies and old events.
9. super-harness does not spawn, discover, retry, or select reviewer agents.
