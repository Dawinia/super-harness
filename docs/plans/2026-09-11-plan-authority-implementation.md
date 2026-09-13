---
change: 2026-09-11-plan-authority
stage: plan
status: submitted
proposed_change: 2026-09-11-plan-authority
baseline_commit: d33eeea7c794deb26d129b90fe69ae66adfb7f0d
implementation_authorized: false
review_status: awaiting_plan_review
---

# Implementation plan: plan authority and external review evidence

## Entry condition

Implement the [specification](2026-09-11-plan-authority-spec.md), which traces to
[O1–O7](../product-foundations.md). This file is an implementation-plan draft, not
a review receipt or an instruction to start implementation now.

The present authorization is to submit this plan through the current lifecycle.
It does not approve implementation. Do not modify runtime/configuration/ratified
decisions, run the new reviewer flow, or infer plan approval merely from this
file existing. Keep the original foundations file unchanged and preserve
unrelated untracked files. There is no dedicated migration implementation in
this plan.

## Submission and approval before execution

When the user authorizes formal submission, verify current main, create the new
working branch from it, and register a distinct Change through the current
lifecycle. Do not attach this work to product-baseline or either Windows Change.
Recheck the installed framework/skills and use the existing repository's native
plan submission path. The present draft intentionally has no `change`/`stage`
frontmatter that could cause the framework observer to register it automatically.

Convert the proposed file inventory below into the exact declared scope, register
the spec and plan as reviewable artifacts, and commit the precise review target.
Include the original foundations document as an unchanged dependency of the
reviewable target; do not leave the reviewer dependent on an unavailable
untracked file. Any user requirement to keep it uncommitted must be resolved
before that submission, not worked around.

Enumerate those three documents in the approval-artifact manifest, plus any other
adopted constraint documents identified during submission. Preserve their paths,
roles, contents, and digests together. The reviewer sees that finite set and checks
for omitted constraints; background links are not recursively collected. The
implementation's new subject builder must support this set, not only --plan bytes.

Obtain applicable plan-review approval under the policy effective at submission.
The proposed external-evidence flow is not available to approve its own
implementation. A producer failure is recorded as failure, never replaced by an
in-session receipt, silently substituted model, or reused historical skip. Do not
start the new runtime implementation before approval.

The specific new recognized review process is a separate activation decision.
It may remain unconfigured while implementing/testing the contract with fixtures;
actual cutover cannot claim a usable recognized flow until the user recognizes it.

**Entry completion:** exact artifacts/scope are committed and an applicable
plan-review approval exists. At present this condition is not met.

## Execution order

### P1 — Shared approval and evidence predicates (O1, O3, O4, O6)

Add `src/super_harness/core/approval.py` for pure record validation, subject/
approval applicability, and required lifecycle predicates. Reuse duplicate-key
rejection and existing hashing/Git helpers rather than a schema framework or
general permissions engine. Separate file/Git reading from the pure checks.

First add meaningful contract tests for A01, A02, A06, A11, A13, and A16. Cover wrong
subject/kind, missing original evidence, unrecognized policy version, duplicate
import, conflicting reuse, and explicit supersession. Do not make a test assert
semantic truth merely because an agent supplied `within_approval`.
Exercise an unchanged plan with a changed adopted spec and restoration of approved
constraints without the original Git locator. Preserve implementation-note changes
as assessments rather than treating every manifest change as revoked authority.

**Completion:** first implementation cannot be authorized without matching
approval, and direct consumers share one predicate; no production path calls the
old reviewer runtime to validate an external conclusion.

### P2 — Event/state integration and plan revisions (O1, O2, O3)

Extend event payloads and derived state with effective approval, pending revision,
implementation assessments/coverage, and current evidence references. Declare
revision-specific transitions in the fixed transition module. Keep first-plan
and revision rejection distinct. Do not mutate old event bytes.

Integrate the shared guards into the existing validate-and-append lock. Restrict
the validation-bypass parameter for new-contract authorizing events while
preserving actual non-authorizing sensor/audit callers. Make reducer, state
verify, event validation, and attestation agree about legal authority. Do not
create a CLI-only guard or derive approval from a state name alone.

Update plan ready/redeclare and implementation start/reopen; add plan withdraw and
the implementation record operation. Withdrawal records non-adoption of an exact
unapproved candidate, preserves B1 and all historical outcomes, and requires no
human approval. Reject withdrawal of an approved revision or stale-candidate
disposition. Share the spec's unresolved-candidate completion guard across done,
code review, state eligibility, and attestation. Extend A06 through actual B1
delivery after document correction, including late approval for withdrawn B2.
Route supported framework-generated plan events through the
same validation and subject construction. Carry the added state fields through
snapshot loading without conflating malformed data with an approved Change.
Keep the existing general no-active/failure hook policy unchanged.

**Completion:** A01, A03, A04, A06, and A13 exercise both CLI and shared event
entrypoints; terminal Changes remain terminal; ordinary coverage updates do not
emit plan-revision requests.

### P3 — External evidence intake and subject binding (O4, O5, O6)

Replace core-owned review runs with direct evidence import. Keep exact subject
preparation and original evidence retention. Import does not require local model
profiles, issued runs, round closure, invocation files, or human nonce commands.
Preserve evidence idempotency and explicit replacement behavior independently
of old run IDs. Failures/missing conclusions never advance approval.

Build code subjects from the actual complete Git change set and approved plan,
including file modes/deletions/renames and stable context identity. Enforce the
same binding at import and merge. Use a narrow validated evidence-carrier rule
to avoid evidence self-reference without exempting arbitrary directory contents.

Bind verification to its tested subject and check configuration. Recheck content
after execution and before completion; old verification events cannot satisfy a
new implementation. Handle dirty in-scope product files explicitly, while
preserving unrelated untracked files rather than deleting/stashing them.

**Completion:** A07–A10 and A14 reject stale/incomplete evidence and accept a
legitimate evidence-only commit. A new subject does not imply lost plan authority.

### P4 — Retire orchestration and update user surfaces (O5, O6)

Remove active producer adapters, model/profile discovery, begin/authorize/run-fail
flows, round budgeting, source retention, aggregation, and TTY nonce execution.
Extract the minimal historical readers before deleting shared old modules so
reports still distinguish prior review, failure, and skip. Do not keep an optional
orchestration package or add a generic extension mechanism.

Update initialization, CLI registration/help, status/report/PR metadata,
agent-instruction generators, and derived documentation. New status shows
effective approval, pending revision, code evidence, and verification—not retry
funding or producer progress. Preserve existing substantive code-review
requirements in the recognized external process; do not make a plain source
label sufficient to claim a valid review.

Add .harness/review-recognition.yaml as the sole new-contract recognition file,
with activation governed by P5. Keep .harness/review-governance.yaml unchanged so
baseline tools can complete this Change, and preserve the ignored local profiles.
After activation those old files are historical/user data that active core does
not read; no dual-format loader or new recognized self-review default is added.
Do not hand-edit the managed AGENTS.md block: change its
generator and regenerate through the repository's supported mechanism.

**Completion:** A10/A11 pass; active call graphs contain no producer execution,
model-selection, round, or retry-budget dependency. Historical report fixtures
still report the same original outcomes.

### P5 — Attestation and one-time direct cutover (O4, O7)

Verify current approval and code/verification subject identities alongside
complete actual file coverage, ordering, and existing required gates. CI reads
committed evidence with Git and the recognized trusted-base policy; it does not
trust runtime local profiles or a policy widened by the candidate PR itself.

Use new-contract evidence for new operation and a minimal discriminator to read
old history. Do not permit a new Change to opt into legacy approval by omitting a
version/receipt. No new skip satisfies review obligations. Keep old skips visible
without converting them to approvals.

Follow the actual three-row leftover-Change table in the spec, rechecking its
evidence. Execute only the necessary one-time lifecycle/configuration operations
when authorized. Keep the archived Windows verification untouched. Preserve the
baseline's actual human approval separately from the historical skip and preserve
its unfinished verification/code review. No migration CLI, preview/apply engine,
rollback product, or batch historical rewrite is required.

Complete the self-hosting cutover in this order:

1. Pin the trusted base commit for the cutover PR. Build and install its baseline
   package non-editably in a separate temporary environment; use its absolute CLI
   path for this Change's real lifecycle/review commands. Keep its old governance
   file unchanged in the candidate checkout. Candidate code is exercised through
   tests, not used to approve its own implementation.
2. Prepare the required CI attestation workflow to build its verifier from trusted
   BASE_SHA in an isolated environment and run it against the candidate checkout
   with the real base/head arguments. Never pip-install the candidate package as
   this gate's verifier. Scrub import-path overrides and avoid editable installs;
   prove the loaded verifier revision with A17 before final code review. Include
   this workflow, its tests, the new recognition file, and the actual owner-
   recognition record in the final reviewed target. Mere inclusion does not
   activate the new contract. Finish code review and verification under the
   still-effective baseline contract; plan approval already precedes implementation.
3. Run the already-reviewed CI workflow against that exact delivery without
   changing its artifacts. Candidate test jobs separately run the new code and
   its new-contract acceptance scenarios. Any required artifact correction follows
   the baseline reopen/review/verification rules before retrying delivery.
4. Pass the old-contract gate for this exact delivery and merge. Only then switch
   local installed/default operation to the merged new runtime/recognition file.
   Subsequent PRs use the same trusted-base verifier installation; their base now
   contains the new contract, which rejects new legacy-format approvals. If main
   moves before the cutover PR merges, update the pinned target and evidence as
   required by the still-effective contract rather than choosing a convenient
   older verifier.

Add A17 as a CI-equivalent integration check with a baseline checkout, candidate
checkout, and distinct installed verifier. A complete old-contract cutover passes;
missing required old evidence fails; candidate import-path contamination is
prevented; a subsequent new-contract PR cannot downgrade itself to the old rules.
Historical readers retain this Change's old evidence after merge, but the shipped
product has no legacy review execution mode or generic cutover exception switch.

**Completion:** A12–A15 and A17 pass and the actual local leftover disposition is recorded
without changing historical meaning. Only one runtime review contract is active.

## Scope rationale by area

The table below explains the bounded areas. The exact declared path set for this
submission is listed immediately after it; there are no wildcard paths. Files
outside that set require a new scope decision under the contract then in force.
Until the new contract is actually effective, current exact-scope/redeclaration
rules still apply. Do not use this plan to claim an exemption from today's gate.

| Area | Proposed files or bounded inventory | Change |
|---|---|---|
| Product/spec/plan | docs/product-foundations.md; this spec and implementation plan | Preserve foundations verbatim; maintain review artifacts. |
| Shared authority | src/super_harness/core/approval.py (new); core/events.py, state.py, reducer.py, transitions.py, emit_validation.py, writer.py | Predicates, references, transitions, guarded writes. |
| State and gate plumbing | core/state_snapshot.py, state_yaml.py, post_emit.py; gates/decisions.py, pre_tool_use.py; cli/state.py | Derived fields and shared validation without changing general hook-failure policy. |
| Plan and implementation | cli/plan.py, implementation.py, done.py; core/review_bundle.py, review_verdict.py, review_checklist.py, scope_match.py, paths.py | Subjects, assessments, current completion requirements. |
| Evidence/governance | cli/review.py; engineering/review_contract.py, review_governance.py | Direct intake and recognition policy; retain only needed subject logic. |
| Merge/verification | cli/attest.py; engineering/attestation.py, pr_metadata.py; sensors/verification_runner.py | Exact subject applicability and coverage. |
| Retire old execution | engineering/review_runs.py, review_budget.py, review_profiles.py; all current files in adapters/reviewer/ | Delete execution responsibilities; retain/extract only necessary historical readers. |
| Reporting | cli/status.py, report.py; engineering/value_report.py | New approval/evidence display; old outcomes retain meaning. |
| Onboarding | cli/__init__.py, init.py, init_plan.py, init_models.py, init_ui.py, init_executor.py; engineering/gitignore_injector.py | Remove core producer/model setup and adjust command/config surfaces. |
| Framework/agent guidance | adapters/framework/superpowers.py, openspec.py; adapters/agent/claude_code.py, codex.py; engineering/agents_md_render.py; AGENTS.md | Shared submission validation and regenerated instructions. |
| Configuration and delivery | .harness/review-recognition.yaml (new); .github/workflows/merge-gate.yml; src/super_harness/templates/super_harness_workflow.yml | New active recognition contract and trusted-base verifier installation; keep the old governance file unchanged/inert after cutover. |
| Documentation | docs/architecture.md, getting-started.md, cli-reference.md, state-machine.md; scripts/gen_cli_reference.py, gen_state_machine.py | Changed responsibilities and regenerated contract surfaces. |
| Decision reconciliation | docs/decisions/d-events-append-only.md, d-state-pure-fold.md, d-fixed-transition-matrix.md, d-single-gate-policy.md, d-gate-governs-git-product.md; other changed anchored decisions discovered before freeze | Only fresh, justified lifecycle reconciliation; never edit a ratified body to suppress a failure. |
| New behavioral tests | tests/unit/core/test_approval.py; tests/integration/lifecycle/test_plan_authority.py | Contract/semantic-scenario harness at the shared interface and lifecycle seam. |
| Existing regression tests | The explicit regression list below, including the retained historical corpus and lifecycle seams | Replace obsolete execution expectations while preserving history, rejection, coverage, and actual verification proofs. |

All paths beginning with core/, cli/, engineering/, gates/, or adapters/ in the
table are relative to src/super_harness/. The following is the exact path set
submitted to plan review. New files are explicitly marked by their role in the
table; no unrelated module enters this Change solely because the full suite finds
an issue.

## Exact declared scope

The plan-review target declares this finite path set. The foundations document is
included byte-for-byte as an adopted constraint; it is intentionally not given a
`change` marker.

```text
docs/product-foundations.md
docs/plans/2026-09-11-plan-authority-spec.md
docs/plans/2026-09-11-plan-authority-implementation.md
.harness/review-recognition.yaml
.github/workflows/merge-gate.yml
AGENTS.md
src/super_harness/core/approval.py
src/super_harness/core/events.py
src/super_harness/core/state.py
src/super_harness/core/reducer.py
src/super_harness/core/transitions.py
src/super_harness/core/emit_validation.py
src/super_harness/core/writer.py
src/super_harness/core/state_snapshot.py
src/super_harness/core/state_yaml.py
src/super_harness/core/post_emit.py
src/super_harness/core/review_bundle.py
src/super_harness/core/review_verdict.py
src/super_harness/core/review_checklist.py
src/super_harness/core/scope_match.py
src/super_harness/core/paths.py
src/super_harness/gates/decisions.py
src/super_harness/gates/pre_tool_use.py
src/super_harness/cli/__init__.py
src/super_harness/cli/state.py
src/super_harness/cli/plan.py
src/super_harness/cli/implementation.py
src/super_harness/cli/done.py
src/super_harness/cli/review.py
src/super_harness/cli/attest.py
src/super_harness/cli/status.py
src/super_harness/cli/report.py
src/super_harness/cli/init.py
src/super_harness/cli/init_plan.py
src/super_harness/cli/init_models.py
src/super_harness/cli/init_ui.py
src/super_harness/cli/init_executor.py
src/super_harness/engineering/attestation.py
src/super_harness/engineering/pr_metadata.py
src/super_harness/engineering/review_contract.py
src/super_harness/engineering/review_governance.py
src/super_harness/engineering/review_profiles.py
src/super_harness/engineering/review_runs.py
src/super_harness/engineering/review_budget.py
src/super_harness/engineering/value_report.py
src/super_harness/engineering/agents_md_render.py
src/super_harness/engineering/gitignore_injector.py
src/super_harness/sensors/verification_runner.py
src/super_harness/adapters/framework/superpowers.py
src/super_harness/adapters/framework/openspec.py
src/super_harness/adapters/agent/claude_code.py
src/super_harness/adapters/agent/codex.py
src/super_harness/adapters/reviewer/__init__.py
src/super_harness/adapters/reviewer/base.py
src/super_harness/adapters/reviewer/claude_cli.py
src/super_harness/adapters/reviewer/codex_cli.py
src/super_harness/adapters/reviewer/registry.py
src/super_harness/templates/super_harness_workflow.yml
docs/ARCHITECTURE.md
docs/getting-started.md
docs/cli-reference.md
docs/state-machine.md
scripts/gen_cli_reference.py
scripts/gen_state_machine.py
docs/decisions/d-events-append-only.md
docs/decisions/d-state-pure-fold.md
docs/decisions/d-fixed-transition-matrix.md
docs/decisions/d-single-gate-policy.md
docs/decisions/d-gate-governs-git-product.md
tests/unit/core/test_approval.py
tests/integration/lifecycle/test_plan_authority.py
tests/unit/core/test_events.py
tests/unit/core/test_state.py
tests/unit/core/test_reducer.py
tests/unit/core/test_transitions.py
tests/unit/core/test_emit_validation.py
tests/unit/core/test_writer.py
tests/unit/core/test_state_snapshot.py
tests/unit/core/test_state_yaml.py
tests/unit/core/test_post_emit.py
tests/unit/core/test_reducer_plan_artifacts.py
tests/unit/core/test_review_bundle.py
tests/unit/core/test_review_verdict.py
tests/unit/core/test_review_checklist.py
tests/unit/core/test_scope_match.py
tests/unit/core/test_paths.py
tests/unit/gates/test_decisions.py
tests/unit/gates/test_pre_tool_use.py
tests/unit/cli/test_plan.py
tests/unit/cli/test_implementation.py
tests/unit/cli/test_done.py
tests/unit/cli/test_review.py
tests/unit/cli/test_review_prepare.py
tests/unit/cli/test_review_human.py
tests/unit/cli/test_review_runs.py
tests/unit/cli/test_review_verdict_gate.py
tests/unit/cli/test_attest.py
tests/unit/cli/test_report.py
tests/unit/cli/test_entrypoint.py
tests/unit/cli/test_init_plan.py
tests/unit/cli/test_init_ui.py
tests/unit/engineering/test_attestation.py
tests/unit/engineering/test_pr_metadata.py
tests/unit/engineering/test_review_contract.py
tests/unit/engineering/test_review_governance.py
tests/unit/engineering/test_review_profiles.py
tests/unit/engineering/test_review_runs.py
tests/unit/engineering/test_review_budget.py
tests/unit/engineering/test_value_report.py
tests/unit/engineering/test_agents_md_render.py
tests/unit/sensors/test_verification_runner.py
tests/unit/adapters/framework/test_openspec.py
tests/unit/adapters/framework/test_superpowers.py
tests/unit/adapters/test_claude_code.py
tests/unit/adapters/test_codex.py
tests/unit/adapters/reviewer/test_claude_cli.py
tests/unit/adapters/reviewer/test_codex_cli.py
tests/unit/adapters/reviewer/test_registry.py
tests/unit/templates/test_super_harness_workflow.py
tests/unit/scripts/test_gen_cli_reference.py
tests/unit/scripts/test_gen_state_machine.py
tests/integration/cli/test_change.py
tests/integration/cli/test_init.py
tests/integration/cli/test_pr_validate.py
tests/integration/cli/test_status.py
tests/integration/cli/test_on_merge.py
tests/integration/cli/test_verification.py
tests/integration/cli/test_windows_lifecycle_entrypoint.py
tests/integration/adapter/test_openspec.py
tests/integration/adapter/test_superpowers_registered.py
tests/integration/daemon/test_framework_observer.py
tests/integration/daemon/test_hook_entry.py
tests/integration/daemon/test_hook_entry_plan_paths.py
tests/e2e/test_plan_authoring_reject_loop.py
tests/e2e/test_pre_tool_use_claude_code.py
tests/e2e/openspec_claude_code/test_full_lifecycle.py
tests/e2e/conftest.py
tests/fixtures/review-corpus/corpus.jsonl
tests/fixtures/review-corpus/README.md
```

## Verification and decision conformance

Use tests through the shared predicate and actual CLI/lifecycle seams. Tests
must fail on the prohibited behavior before claiming closure; do not mirror field
assignments or replace semantic review with structure checks.

1. Focused contract tests: P1/P2 and spec A01–A06, A11, A13, A16.
2. Git/evidence integration tests: A07–A10, A12, A14, A17, including evidence-only
   commits, dirty product paths, rename/deletion, conflicting imports, and legacy
   reads versus new writes.
3. A15 on native Windows without WSL/Docker and on POSIX. Check actual nonzero
   propagation and executed-test evidence, not only a green wrapper exit.
4. Run the full existing test suite, Ruff, and mypy after the focused tests pass.
5. At checkpoints and before commits run decision check --changed. Run full
   decision check and doc check before completion, plus relevant doc-ref checks.
6. Complete the effective lifecycle's done/code-review/attestation obligations.
   Record exact tested subject and final review evidence; no implementation
   completion statement substitutes for verify/done evidence.

Example local tool entrypoints on this Windows checkout are
`.venv/Scripts/python.exe -m pytest`, `.venv/Scripts/python.exe -m ruff`,
`.venv/Scripts/python.exe -m mypy`, and `.venv/Scripts/super-harness.exe`.
Use the project's direct-argv verification configuration for the actual run;
do not introduce POSIX environment-assignment prefixes into Windows commands.

The append-only log, pure state fold, fixed transition declarations, single gate
policy, and offline Git merge verification should still hold. Reconcile anchored
changes only with fresh evidence. If a ratified decision would actually be
violated, use its formal decision lifecycle and make the conflict explicit;
do not relabel a violation as successful reconciliation. Do not batch-reconcile
unrelated existing REVIEW-NEEDED notices.

## Draft completion and implementation completion

Draft completion means the spec and this plan are saved, linked, checked for
internal consistency, and distinguish confirmed foundations from proposals and
unanswered recognition choices. It does not mean the plan is approved.

Implementation completion requires all O1–O7 scenarios with the right kind of
evidence, an explicitly recognized usable external process, removal of active
core orchestration, one-time leftover handling, required code review and actual
verification, decision/doc conformance, and accurate final attestation. Report
remaining limitations and actual lifecycle status. Do not reopen the archived
2026-09-04-windows-verification to achieve this result.
