---
change: review-contract-compiler
stage: plan
tier_hint: Large
scope:
  files:
    - .gitignore
    - .harness/attestations/review-contract-compiler.jsonl
    - .harness/policy.yaml
    - .harness/review-governance.yaml
    - AGENTS.md
    - README.md
    - README.zh-CN.md
    - docs/ARCHITECTURE.md
    - docs/adapters/claude-code.md
    - docs/adapters/codex.md
    - docs/adapters/plain.md
    - docs/cli-reference.md
    - docs/concepts.md
    - docs/decisions/d-events-append-only.md
    - docs/decisions/d-fixed-transition-matrix.md
    - docs/getting-started.md
    - docs/limitations.md
    - docs/plans/2026-07-11-review-contract-compiler.md
    - docs/state-machine.md
    - private/CAPABILITY-CONVERGENCE-LEDGER.html
    - private/CAPABILITY-CONVERGENCE-LEDGER.md
    - private/NEXT-SESSION-PROMPT.md
    - private/OPEN-ITEMS.md
    - scripts/gen_cli_reference.py
    - src/super_harness/adapters/agent/claude_code.py
    - src/super_harness/adapters/agent/codex.py
    - src/super_harness/adapters/reviewer/__init__.py
    - src/super_harness/adapters/reviewer/base.py
    - src/super_harness/adapters/reviewer/claude_cli.py
    - src/super_harness/adapters/reviewer/codex_cli.py
    - src/super_harness/adapters/reviewer/registry.py
    - src/super_harness/cli/adapter.py
    - src/super_harness/cli/attest.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/review.py
    - src/super_harness/cli/status.py
    - src/super_harness/core/events.py
    - src/super_harness/core/paths.py
    - src/super_harness/core/review_bundle.py
    - src/super_harness/core/review_verdict.py
    - src/super_harness/core/scope_match.py
    - src/super_harness/core/transitions.py
    - src/super_harness/engineering/attestation.py
    - src/super_harness/engineering/gitignore_injector.py
    - src/super_harness/engineering/review_contract.py
    - src/super_harness/engineering/review_governance.py
    - src/super_harness/engineering/review_profiles.py
    - src/super_harness/engineering/review_runs.py
    - src/super_harness/engineering/reviewer_policy.py
    - tests/e2e/openspec_claude_code/test_full_lifecycle.py
    - tests/integration/cli/test_adapter.py
    - tests/integration/cli/test_init.py
    - tests/integration/cli/test_status.py
    - tests/integration/cli/test_sync.py
    - tests/unit/adapters/reviewer/test_claude_cli.py
    - tests/unit/adapters/reviewer/test_codex_cli.py
    - tests/unit/adapters/reviewer/test_registry.py
    - tests/unit/adapters/test_claude_code.py
    - tests/unit/adapters/test_codex.py
    - tests/unit/cli/test_attest.py
    - tests/unit/cli/test_init.py
    - tests/unit/cli/test_review.py
    - tests/unit/cli/test_review_human.py
    - tests/unit/cli/test_review_prepare.py
    - tests/unit/cli/test_review_runs.py
    - tests/unit/cli/test_review_verdict_gate.py
    - tests/unit/cli/test_status.py
    - tests/unit/core/test_events.py
    - tests/unit/core/test_reducer.py
    - tests/unit/core/test_review_bundle.py
    - tests/unit/core/test_review_verdict.py
    - tests/unit/core/test_scope_match.py
    - tests/unit/core/test_transitions.py
    - tests/unit/engineering/test_attestation.py
    - tests/unit/engineering/test_gitignore_injector.py
    - tests/unit/engineering/test_review_contract.py
    - tests/unit/engineering/test_review_governance.py
    - tests/unit/engineering/test_review_profiles.py
    - tests/unit/engineering/test_review_runs.py
    - tests/unit/engineering/test_reviewer_policy.py
    - tests/unit/scripts/test_gen_cli_reference.py
    - tests/unit/scripts/test_gen_state_machine.py
---

# Agent-Neutral Review Execution Protocol

## Goal

Turn the existing scoped review-target compiler into a usable, agent-neutral
review execution protocol without making super-harness a reviewer executor.

The product must keep review effective first. Cost control prevents accidental
whole-change inspection, inherited main-session settings, silent retries, and
unbounded automated rounds; it must not make a review impossible merely because
an estimate or usage field is unavailable.

## Problem

The current branch already compiles exact per-source Git targets and prevents a
code-only finding fix from automatically reopening plan review. That foundation
worked, but the self-host audit exposed a larger execution-control failure:

- six review rounds caused twelve LLM review calls;
- every reviewer used `gpt-5.6-sol`, including nominal Medium reviewers;
- the reviewer logs totalled approximately 2.52 million tokens;
- external Codex sessions still loaded their normal global skills and memory;
- the final five-file incremental review still consumed 63,156 tokens; and
- the scoped target controlled Git inspection, but not model selection, parent
  session inheritance, retries, invocation count, or review-round count.

The current policy also names `task-subagent` as an executable participant.
That is invalid for a generic CLI: Codex CLI cannot be assumed to expose a
host-native `spawn_agent` operation, and super-harness itself does not own such
an executor.

## Product Boundary

super-harness compiles, freezes, validates, and records review execution
contracts. It never starts Codex, Claude, a host-native task, or any other LLM.

Within that boundary, super-harness can enforce:

- exact committed inspection targets, files, and Git argv;
- explicit local producer protocol, requested model, and agent-specific options;
- a fresh invocation contract that never resumes the authoring conversation;
- complete checklist output and full-target finding collection;
- deterministic round, run, retry, and stale-result rules;
- maximum automatic rounds before explicit human authorization;
- source independence, contract-digest binding, and imported result receipts;
- a dedicated, interactive human-review confirmation path; and
- honest requested-versus-reported execution metadata.

It cannot prove that an external process used the requested model, omitted an
undeclared context source, or reported genuine token usage. A receipt proves
what was imported and what the producer reported, not cryptographic execution
provenance. Explicit contradictions are rejected; absent optional telemetry is
reported as unknown rather than invented.

## Non-Goals

This change does not:

- implement a headless reviewer executor or process supervisor;
- call `spawn_agent`, Claude Task, or any host-native subagent API;
- install Codex or Claude binaries;
- create a clean HOME or suppress normal project instructions, skills, or memory;
- restrict supporting context to a configured file whitelist;
- impose a hard token or input-size gate that can make review unavailable;
- automatically shard a large target;
- retry, widen, change models, or escalate effort silently;
- semantically merge findings with another LLM; or
- provide a general migration command for unreleased legacy reviewer policy.

## Terminology

- **Reviewer role**: lifecycle responsibility, currently `plan-reviewer` or
  `code-reviewer`.
- **Source**: a configured evidence provenance label used for independence. It
  is not a command and not an executor.
- **Producer protocol**: an adapter that validates a locally available producer,
  compiles its invocation contract, and parses its output. It never runs it.
- **Inspection scope**: the committed delta for which findings may be attributed.
- **Supporting context**: unchanged repository material a reviewer may read to
  understand architecture and impact. It does not widen finding attribution.
- **Epoch**: one plan or code review budget window. Plan redeclaration opens a
  new plan epoch; code-only review fixes stay in the same code-review epoch.
- **Round**: one explicit automated attempt against a frozen contract and source
  set. A started round consumes budget even if its producer crashes.
- **Run**: one source invocation within a round, identified by a unique run ID.
- **Result**: normalized verdict output imported for one run.
- **Receipt**: durable event evidence binding the result to its run, source,
  target HEAD, contract digest, requested profile, and reported metadata.

## Configuration

### Tracked governance

Replace `.harness/policy.yaml` with a deliberately named, versioned, tracked
`.harness/review-governance.yaml`. It contains shared governance only:

```yaml
version: 1
review:
  base_branch: main
  sources:
    codex:
      kind: automated
    claude:
      kind: automated
    human:
      kind: human
  roles:
    plan-reviewer:
      participants: [codex, claude]
      min_independent: 2
      max_automatic_rounds_per_epoch: 2
    code-reviewer:
      participants: [codex, claude]
      min_independent: 2
      max_automatic_rounds_per_epoch: 2
  require_distinct_model_families: false
```

Participant order is presentation-only. Semantically it is a distinct set.
Reviewers run without seeing one another's verdicts. The harness waits for the
whole required source set before emitting an approval or rejection milestone.

### User-local profiles

Create `.harness/review-profiles.local.yaml` only when the user selects local
automated producers. The init-managed gitignore block always ignores it. The
file is user editable, machine local, and must not contain API credentials.

```yaml
version: 1
sources:
  codex:
    protocol: codex-cli
    model: <explicit-user-selection>
    cost_class: standard
    agent_options:
      reasoning_effort: medium
      sandbox: read-only
  claude:
    protocol: claude-cli
    model: <explicit-user-selection>
    cost_class: standard
    agent_options:
      effort: medium
```

Option names remain producer-specific. The harness does not invent a universal
effort vocabulary or hardcode a model name in tracked governance. A profile
marked `cost_class: expensive` requires one-shot human authorization bound to
the exact role, epoch, contract digest, profile digest, and source set before
`review begin` accepts it. Tier never changes the selected model silently.

Missing local profiles produce an actionable error for automated participants;
human-only governance remains fully usable.

The unreleased `.harness/policy.yaml` schema is removed from generated projects
and this repository. If it is found without the new governance file, commands
fail with a direct re-init/manual-update message; no migration CLI is added.

## Agent-Neutral Init

`super-harness init` always initializes generic lifecycle files first. In an
interactive TTY it then detects available Codex and Claude installations and
offers two independent multi-select prompts:

1. coding-agent integrations to configure (`codex`, `claude-code`); and
2. local review producers to configure (`codex-cli`, `claude-cli`).

Users may select zero, one, or many entries; there is no synthetic "both"
choice. With both producer CLIs detected, the wizard recommends both; with one,
it recommends that one; with none, it recommends human-only review. These are
editable recommendations, not forced governance.

The non-TTY equivalent uses repeatable flags:

```text
super-harness init \
  --integration codex \
  --integration claude-code \
  --review-producer codex-cli \
  --review-producer claude-cli
```

With no non-TTY selection flags, init creates an agent-neutral core and
human-only review governance. Selected integrations reuse the existing adapter
installation behavior. Selected producers only write super-harness local
profiles; they do not install third-party binaries. Standalone `adapter install`
remains available for later changes and automation.

## Reviewer Protocol Adapters

Add a separate `ReviewerProtocolAdapter` namespace rather than extending
`AgentAdapter` or introducing a runner abstraction. Each built-in protocol can:

- detect a local executable without making a model call;
- probe non-LLM version/help capabilities when necessary;
- validate required explicit profile fields and supported option names;
- compile argv, stdin/prompt path, output path, and expected result schema;
- parse a completed raw output file into the normalized verdict schema; and
- report requested and producer-reported metadata without fabricating values.

It has no `run()`, `spawn()`, retry, shell, or subprocess-review method.

v0.1 ships `codex-cli` and `claude-cli` protocols. Human review uses the same
normalized verdict and receipt model but a dedicated interactive CLI path, not
an automated producer adapter. Host-native subagent protocols are deferred
until a stable external import contract exists.

## Inspection Contract

Preserve the current scoped target compiler:

- the first result for a source receives the full declared in-scope change;
- a trustworthy complete result at an ancestor HEAD establishes that source's
  incremental baseline;
- partial, stale, non-ancestor, or scope-insufficient evidence cannot establish
  a narrow baseline;
- plan review covers resolved plan/spec artifacts only;
- code review covers the declared implementation scope;
- code-only finding fixes do not trigger plan review; and
- plan, requirements, or scope changes require explicit human-confirmed
  `plan redeclare`, which opens a new epoch and invalidates old evidence.

Every assignment contains exact `base`, `head`, ordered `files`, and an argv
array. It warns when a target is large but does not auto-shard. The reviewer may
read any repository context needed to understand the target, including project
architecture and binding decisions. Findings must still be attributable to the
inspection delta, a dependency made relevant by it, or a prior finding.

The canonical prompt requires the reviewer to continue through the full assigned
target after finding a blocker and return every finding in one result. It forbids
editing, invoking verdict commands, seeing peer verdicts, or widening the target.
If the assigned target is insufficient, the reviewer returns a partial rejection;
the next prepare fails closed to a full target for that source.

## Review Lifecycle

### Prepare

```text
super-harness review prepare <change> --reviewer <role>
```

Prepare validates lifecycle state, committed in-scope cleanliness, governance,
profiles, baselines, and Git ancestry. It writes a replaceable draft packet under
`.harness/pending-reviews/<change>/<role>/` and returns compact metadata: packet
path, target HEAD, contract digest, source count, and warnings. It starts nothing
and consumes no round.

### Begin

```text
super-harness review begin <change> --reviewer <role> [--source <source>]...
```

Begin freezes the current packet and emits `review_round_started`. It allocates
a round ID and one run ID per selected source, compiles each producer invocation,
and returns compact paths and argv data. The current code agent or user invokes
those commands outside super-harness.

The default selected set is every currently required automated participant.
`--source` is accepted only for retrying the failed subset of the same unchanged
contract; it cannot arbitrarily weaken governance. Once begin succeeds, the round
is consumed even when a producer later crashes.

Begin refuses a stale packet, an unapproved expensive profile, or an automatic
round beyond the role's per-epoch limit. It never starts a process and never
silently retries.

### Import or fail a run

```text
super-harness review result import <change> --reviewer <role> \
  --run-id <run-id> --result-file <path>

super-harness review run fail <change> --reviewer <role> \
  --run-id <run-id> --reason <reason>
```

Import parses the existing output through the frozen run's producer protocol,
validates every checklist item, findings, source, run ID, target HEAD, and
contract digest, then emits `review_result_imported` immediately. Duplicate
imports of the same byte-identical result are idempotent; a conflicting second
import is rejected.

The receipt records the normalized verdict, requested model/options/profile
digest, and any reported actual model, usage, tool trace, and duration. Missing
optional usage does not invalidate ordinary review but cannot support a token
audit. A reported actual model that contradicts the requested model invalidates
the source result. If governance requires distinct model families, missing or
non-distinct reported families cannot satisfy that optional rule.

`run fail` records the current agent's observation that the external producer
did not return a valid result. It does not retry it.

### Close and retry

When all issued runs in a round are terminal, import/fail deterministically emits
`review_round_closed`. The role remains in its awaiting state until all required
sources have valid results for the same contract digest.

- All reviewers finish before implementation edits resume, even if one reports
  a blocker early.
- A complete rejected round preserves every source finding, closes with the
  corresponding plan/code rejection milestone, and the code agent batches fixes
  once.
- A successful source from an execution-failed round may be retained only while
  the target HEAD and complete contract digest remain identical.
- Retrying only failed sources creates a new round and new run IDs and consumes
  another automatic round.
- Any target, prompt, participant, profile, or contract change invalidates the
  retained success and requires the full source set again.
- A code change after rejection causes each source to receive its own compiled
  incremental follow-up target; it does not reset the epoch budget.

The default maximum is two automatic plan-review rounds and two automatic
code-review rounds per epoch. Exhaustion is not a functional dead end: a human
may review directly or explicitly authorize exactly one additional automated
round. `review_round_authorized` is a one-shot, interactive human event bound to
the role, epoch, current contract/profile digests, source set, and reason. There
is no CI or non-TTY `--yes` path.

### Finding identity and aggregation

Finding IDs are namespaced by source and run. Aggregation is deterministic only:
source, severity, file, line, and finding ID ordering. No LLM deduplicates or
rewrites findings. One code fix may resolve several IDs; contradictory findings
remain visible for code-agent or human disposition. A follow-up approval must
dispose every still-open prior finding as `resolved` or justified `wontfix`.

### Milestones and legacy commands

The existing `plan_approved`, `plan_rejected`, `code_review_passed`, and
`code_review_failed` events remain the lifecycle transition milestones. New run
events are append-only informational evidence. Closure emits a milestone only
after governance is satisfied.

Automated sources can no longer create evidence through direct
`review approve|reject --source`. They must use `review result import`; otherwise
the path would bypass the receipt and round contract. Legacy events remain
readable. `review skip` remains a disclosed escape hatch, with the existing merge
gate consequences.

## Human Review

Human review is first-class and does not spend LLM tokens:

```text
super-harness review human inspect <change> --reviewer <role> --pager
super-harness review human draft <change> --reviewer <role> \
  --verdict-file <path>
super-harness review human confirm <change> --reviewer <role> --nonce <nonce>
```

`inspect` renders the packet, checklist, exact diff command, findings, and prior
dispositions through a local pager when a human owns the terminal. Agent-facing
and JSON output stays compact and returns paths, digests, counts, and nonce only;
it never dumps the whole packet into an LLM tool result.

`draft` validates the human verdict and creates a nonce bound to the change,
reviewer, target HEAD, verdict digest, and expiry. `confirm` requires an
interactive TTY and explicit confirmation; it imports the human receipt and can
satisfy governance. There is no non-interactive flag that impersonates a human.
The protocol still cannot cryptographically prove who typed at a terminal, so
agent guidance explicitly forbids self-confirmation without human instruction.

## Status and Recovery

`super-harness status` reports, without running a producer:

- current reviewer role and epoch;
- automatic rounds used, remaining, and authorized extras;
- required, imported, failed, retained, and stale sources;
- current packet/contract digest and target HEAD;
- requested profiles plus reported metadata availability; and
- the exact next legal command.

A crash after begin remains visible as an open run. The user/code agent records
it with `review run fail`; the harness never guesses that an external process is
dead. Re-running prepare or import is deterministic and does not silently consume
another round.

## Cost Semantics

There is no fixed token pass/fail threshold. Completion means the product can
show and enforce the decisions that prevent accidental cost multiplication:

- explicit producer, model, cost class, and producer-specific effort/options;
- fresh external invocation rather than authoring-session resume;
- exact inspection range/files/argv without silent whole-PR widening;
- complete findings before batched fixes;
- bounded automatic rounds and invocations;
- no silent retry, model escalation, or repeated plan review; and
- honest optional actual model/usage/duration reporting.

Project architecture, AGENTS guidance, skills, and other supporting repository
context may still be needed for review quality. The protocol does not suppress
them. Cost reduction must be evaluated by dogfood evidence against the historical
2.52-million-token audit, with missing telemetry called out explicitly rather
than converted into a false saving claim.

## TDD Plan

### Task 1: Split governance from local profiles

1. Add failing tests for the tracked governance schema, participant-set
   validation, round defaults, optional model-family rule, legacy-file error,
   and user-local producer profiles.
2. Implement `review_governance` and `review_profiles` parsers.
3. Replace this repository's policy with tracked governance and add the local
   profile path to the managed gitignore block.
4. Run focused parser and gitignore tests.

### Task 2: Add agent-neutral init selection

1. Add failing TTY and non-TTY init tests for zero/one/many integration and
   producer selections, recommendations, repeatable flags, missing binaries,
   human-only fallback, and idempotent force behavior.
2. Refactor adapter installation into a reusable public helper.
3. Implement the dependency-free multi-select wizard and local profile writer.
4. Prove init never installs a third-party binary and never creates an impossible
   `task-subagent` default.

### Task 3: Define reviewer protocol adapters

1. Add failing contract tests for a no-execution `ReviewerProtocolAdapter`.
2. Add Codex and Claude protocol tests for detection/probe, explicit model and
   option validation, exact argv/stdin/output compilation, output parsing, and
   unsupported capabilities.
3. Implement the separate built-in protocol registry.
4. Assert no protocol adapter exposes or calls a review execution method.

### Task 4: Model epochs, rounds, runs, and receipts

1. Add failing pure-fold tests for epoch boundaries, consumed crash rounds,
   source runs, idempotent imports, conflicting imports, retained success,
   invalidation, exhaustion, and one-shot authorization.
2. Add `review_round_started`, `review_result_imported`,
   `review_round_closed`, and `review_round_authorized` as informational events.
3. Implement deterministic run-state derivation from append-only events.
4. Reconcile the append-only-event and fixed-transition-matrix decisions after
   proving their invariants still hold.

### Task 5: Freeze inspection and invocation contracts

1. Preserve all current scoped-target compiler tests.
2. Add failing tests for profile/participant/prompt digests, exact frozen packet
   paths, large-target warnings, supporting-context wording, no widening, and
   no automatic sharding.
3. Implement prepare/begin compilation without executing producer commands.
4. Add stale packet and expensive-profile authorization failures.

### Task 6: Import results and close rounds

1. Add failing CLI tests for import, execution failure, complete participant-set
   closure, early blocker continuation, partial scope rejection, source retry,
   model mismatch, optional telemetry, finding namespaces, deterministic
   aggregation, and prior-finding dispositions.
2. Implement result receipts and immediate append-only imports.
3. Emit lifecycle milestones only after deterministic closure satisfies or
   rejects the complete governance set.
4. Reject automated direct approve/reject while retaining historical events and
   disclosed skip behavior.

### Task 7: Add human review and status recovery

1. Add failing tests for pager inspection, compact agent output, draft nonce
   binding/expiry, TTY-only confirmation, human-only governance, exhaustion
   recovery, and no non-interactive impersonation.
2. Implement the human draft/confirm receipt path.
3. Extend status with epoch, round, source, receipt, and next-command summaries.
4. Keep the trust ceiling explicit in output and documentation.

### Task 8: Update gates, attestations, and docs

1. Add failing attestation tests proving valid imported receipts satisfy
   independent review and receipt-less automated approvals do not.
2. Update generated agent instructions for external invocation/import discipline,
   full-target review, batched fixes, and human boundaries.
3. Update public concepts, architecture, getting started, limitations, adapter
   guides, READMEs, and CLI reference; regenerate derived docs.
4. Update the private cost audit/ledger and next-session handoff with measured
   dogfood evidence.

### Task 9: Verification and self-host dogfood

1. Run focused tests after each red-green task and decision checks at natural
   checkpoints.
2. Run the full configured verification suite and documentation checks.
3. Commit all in-scope implementation before code review.
4. Dogfood the new external `codex-cli` and `claude-cli` producer protocols as
   independent sources. Do not use a subagent or implementation-review override.
5. Complete all source results before editing; batch findings and run at most one
   scoped follow-up unless the human explicitly authorizes another round.
6. Record requested/reported model, call, round, duration, and available usage
   evidence; compare honestly with the 2.52-million-token baseline.
7. Update and verify the attestation, then request explicit user confirmation
   before any PR #79 merge.

## Acceptance Criteria

1. super-harness never spawns or executes a reviewer; protocol adapters have no
   execution method and begin returns invocation contracts only.
2. Generated configuration contains no `task-subagent` default and separates
   tracked governance from gitignored, user-editable producer profiles.
3. Interactive init independently multi-selects zero/one/many coding-agent
   integrations and review producers; repeatable non-TTY flags provide the same
   semantics without installing third-party binaries.
4. Every automated run freezes an explicit producer, model, cost class,
   producer-specific options, source, target HEAD, exact files/argv, prompt, and
   contract digest before external invocation.
5. Reviewers start from a fresh external invocation contract, never resume the
   authoring conversation, and may read supporting repository context without
   widening inspection findings.
6. The current full/incremental target compiler and code-only follow-up routing
   remain green, including exact per-source ranges and plan-artifact-only review.
7. Every required source completes the full assigned target before the round
   closes; findings are namespaced and deterministically aggregated for one
   batched fix pass.
8. Started crashes consume a round, successful peer evidence is retained only
   for an identical contract, retries use new IDs, and changed contracts
   invalidate retained evidence.
9. Automatic review defaults to at most two plan rounds and two code rounds per
   epoch; exhaustion allows human review or one explicitly authorized additional
   round rather than making review unavailable.
10. Automated evidence is accepted only through complete, fresh imported
    receipts. Explicit actual/requested model mismatch invalidates the result;
    absent optional usage remains unknown and does not block ordinary review.
11. Human review provides pager inspection plus nonce-bound TTY confirmation and
    no non-interactive self-approval shortcut.
12. Direct automated `review approve|reject --source` cannot bypass receipts;
    legacy events remain readable and deliberate skips remain disclosed.
13. Status provides deterministic crash/retry/exhaustion recovery without
    starting a producer or requiring the whole packet in agent context.
14. Self-host code review uses the new Codex CLI and Claude CLI external
    protocols, records all available cost evidence, and does not claim completion
    merely because CI is green.
15. PR #79 is not merged until the user explicitly confirms the merge after
    implementation review and final lifecycle verification.

## Bootstrap Exception for This Redeclaration

The currently installed plan-review policy requires the invalid
`task-subagent` path that this change removes. Therefore this redeclared Large
plan cannot truthfully dogfood the new protocol before implementation.

After `plan ready`, the user must inspect the generated plan-review packet and
explicitly confirm this plan. Only then may the existing disclosed human
bootstrap override advance the lifecycle. That exception applies to this plan
review only. Implementation code review must dogfood the new Codex CLI and
Claude CLI producer protocols with no review override.
