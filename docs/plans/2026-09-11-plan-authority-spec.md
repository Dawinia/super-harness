---
change: 2026-09-11-plan-authority
stage: design
status: code_review
proposed_change: 2026-09-11-plan-authority
baseline_commit: d33eeea7c794deb26d129b90fe69ae66adfb7f0d
implementation_authorized: false
review_status: disclosed_plan_skip
---

# Plan authority and externally supplied review evidence

## Status and authority

This is the proposed specification and technical design for the O1–O7 obligations
in [product foundations](../product-foundations.md). Those product foundations are
confirmed; this specification has not received plan-review approval and is not an
implementation authorization. The [implementation plan](2026-09-11-plan-authority-implementation.md)
defines the execution order and proposed file scope.

The documents are drafts on `main`, inspected at the baseline commit above.
They deliberately omit lifecycle frontmatter: the installed Superpowers adapter
interprets a `change` marker as an intent declaration and a plan-stage artifact
as a plan submission. Merely saving these drafts must not start a Change. The
foundations file remains unchanged, and no existing Change is reopened.

The user subsequently clarified that there is only one current user: perform a
direct local cutover, preserve historical meaning, and handle the actual leftover
Changes. Do not build a migration tool, compatibility layer, resumable migration
engine, or optional reviewer-orchestration product.

## Required behavior

| ID | Requirement | Observable boundary |
|---|---|---|
| O1 | Product implementation within a governed Change requires applicable review approval of its plan. | Plan declaration, execution success, an empty result, or a bare implementation event cannot grant approval. |
| O2 | Approval covers autonomous implementation within the approved commitments and restrictions. | Adding an omitted fixture or changing an implementation technique does not itself require new plan review. Record the agent's reasoning. |
| O3 | Changed commitments or relaxed explicit restrictions require approval of the changed plan content. | An implementation assessment is not approval; cumulative changes remain inspectable against the effective approved commitments. |
| O4 | Review and verification evidence identify their exact subjects separately from plan authority. | A new HEAD alone does not invalidate a plan approval; old code evidence cannot certify a different code subject. |
| O5 | Core accepts external review conclusions without organizing producers, rounds, retries, or budgets. | Valid evidence can be imported without a core run, pending packet, local model profile, or invocation. |
| O6 | The user recognizes review processes; core checks subject, explicit conclusion, and conformance to recognition rules. | An implementer cannot activate a broader process; core does not universally demand independence, particular models, or per-review human confirmation. |
| O7 | New operation switches directly to the new contract while history keeps its original meaning. | Old skips stay skips; archived Changes remain terminal; actual unfinished work receives an explicit applicability assessment. |

Code review, verification, decision conformance, documentation checks, and merge
evidence remain required. Moving the organization of code review outside core
does not delete its substantive obligations or silently relax their acceptance
conditions.

## Scope and exclusions

Change plan authority, implementation assessments, review evidence intake,
subject binding, related lifecycle/attestation rules, configuration, onboarding,
agent instructions, and documentation. Reuse the event log and existing Git/file
facilities. Introduce one shared approval module rather than a general permissions
framework.

Do not add continuous skips, review-cost analysis, reviewer execution plugins,
new scheduling, cross-worktree coordination, or a general migration command.
Do not redesign verification execution or the decision lifecycle. Do not change
the general hook failure policy or treat all editing without an active Change as
newly forbidden. Existing local hook coverage and its failure limits remain
explicit; this work does not guarantee prevention of arbitrary shell writes.

## Existing implementation and required separation

| Evidence | Current behavior | Required adjustment |
|---|---|---|
| [Plan CLI](../../src/super_harness/cli/plan.py) | Late file-scope expansion goes through redeclaration and complete plan review. | Separate implementation coverage updates from commitment revisions. |
| [Review contract](../../src/super_harness/engineering/review_contract.py) | Any plan/spec content drift can require redeclaration. It already compares content across rebases. | Preserve accurate snapshots and content comparison; judge plan authority against commitments rather than all plan prose. |
| [Review execution fold](../../src/super_harness/engineering/review_runs.py) | Plan/code epochs are coupled to producer rounds, receipts, source retention, and budgets. | Keep subject/version identity; remove execution coordination from active operation. |
| [Transitions](../../src/super_harness/core/transitions.py) and [emit validation](../../src/super_harness/core/emit_validation.py) | Universal restart/invalidate transitions can enter approved/implementation states; the reopen CLI applies a narrower guard. | Enforce approval prerequisites in the shared event path, not only in one command. |
| [Attestation](../../src/super_harness/engineering/attestation.py) | Checks lifecycle milestones, receipt references, and file coverage. | Require applicable plan approval and current code/verification subjects, not historical milestone existence alone. |
| [State snapshot](../../src/super_harness/core/state_snapshot.py) | Missing/corrupt state can degrade to no active Change. | Carry new derived approval fields without silently broadening the hook failure policy. |

## Records and immutable subjects

### Plan subject and approval

A plan subject contains the Change identifier, a finite manifest of approval
artifacts, explicit commitments/restrictions, and a content digest. The manifest
includes the plan and every independently stored spec/foundation document whose
requirements the plan adopts. Each entry preserves its repository-relative path,
role, full submitted content, and digest; the subject digest covers the manifest
and commitments together. It references the prior approval when proposing a
revision. For this Change the artifact set is the implementation plan, this spec,
and docs/product-foundations.md; binding restrictions in other adopted material
must be included before submission, not left behind a mutable link.

Submission explicitly distinguishes adopted constraints from background links.
Do not recursively snapshot ordinary reference links or treat the artifact
manifest as an implementation-file allowlist. Commitments reference an artifact
digest and a stable identifier within that snapshot. Review checks that adopted
constraints were not omitted; core checks declared artifact presence, digests,
and references rather than claiming to discover every semantic dependency.

Use stable identifiers with free-text commitments; do not mandate the previously
unapproved five-category schema. Acceptance conditions and explicitly binding
technical choices belong to the commitments. Implementation notes cannot weaken
them. A reviewer must resolve contradictory statements before approving.

The receipt identifies the exact submitted artifact set and its approved commitments.
Later implementation-note changes create an assessment referencing that approval;
they do not relabel the new full document as already reviewed. Approval must be
recoverable from those stored contents even if the original Git commit disappears.
An unchanged plan file cannot make a revised adopted spec inherit approval.
Compare changes in every adopted artifact with the approved snapshot: ordinary
wording/implementation changes use assessments, whereas changed commitments need
new review. A changed artifact digest is a change to assess, not automatic
revocation of the approval covering the original commitments.

Digest structured records through a single canonical JSON serializer and SHA-256.
Reject duplicate mapping keys and unknown contract versions. Normalize text line
endings for plan text only; do not conflate byte normalization with semantic
equivalence, or normalize product file bytes when binding code evidence.

### Implementation assessment and coverage

Record the effective approval reference, the change being made, affected
commitment identifiers, the agent's conclusion and reasons, and relevant
implementation/file references. Every assessment compares with the effective
approved commitments, not merely with the previous assessment.

Keep the submitted file estimate for history. Maintain a separate actual coverage
manifest updated by implementation records, including additions, deletions, and
renames. File count/path changes alone do not revoke plan approval. Explicit
restrictions such as "do not modify runtime configuration" remain binding even
when a path is added to the manifest.

Agents may batch ordinary assessments at meaningful implementation checkpoints;
do not require a permission request or assessment form before every edit. Before
completion/code review, actual changes must have coverage and traceable reasoning.
Use the complete Git change set to detect omissions rather than filtering the
review target down to the agent's declared paths.

Core checks references and coverage. It does not prove that an agent's
`within_approval` assessment is semantically correct. The recognized review
process inspects the actual changes and accumulated reasoning against approval.

### Review evidence

The external evidence contains an immutable identifier/content digest, `kind`
(`plan` or `code`), subject identifier, explicit `decision` (`approve` or `reject`),
process identifier/version, issuer, original evidence and its provenance, and an
optional reference to a conclusion it supersedes. Invocation success, an absent
verdict, and execution failure are not approval decisions.

Keep original evidence with the recorded conclusion; do not reduce it to a pass
boolean. A core run ID, reviewer model, invocation, retry budget, or round number
is not required. Repeated identical import is idempotent. Reusing an evidence ID
with different content fails. Importing an old pass again cannot supersede a
newer rejection. A conflicting replacement must explicitly reference the current
conclusion for the same subject; unrelated-subject replacements fail.

Evidence for an older subject may be retained for audit but cannot advance the
current subject. Missing original material or an inconsistent subject/provenance
must be reported as insufficient evidence, not silently accepted.

### Code and verification subjects

Bind code evidence to the Change, applicable approval, resolved review base,
complete actual change manifest, and product content identities (including modes,
deletions, renames, and relevant unchanged context through the base/target tree).
HEAD is a locator, not the plan-approval validity predicate.

The local code-review preparation and final merge verification use the same
subject construction. A changed review base requires fresh code-subject
assessment even when the patch appears similar; it does not itself require a new
plan approval. No automatic incremental-review/source-retention engine is added.

Adding attestation evidence must not invalidate itself through a new HEAD. Exclude
only validated evidence carriers from the product subject, using a narrow,
explicit format/path rule. Do not exempt arbitrary files in an evidence directory.
Keep executable artifacts, configuration, and documentation in the product set.

Verification records identify the implementation subject, check configuration,
executed checks, outcomes, and necessary platform/tool context. Do not capture
secrets from the ambient environment. Check the subject before and after checks;
changing the tested material prevents that result from completing the Change.
Historical verification_passed alone cannot certify a later implementation.

## Recognition and trust

Store the new active recognition contract in .harness/review-recognition.yaml,
with user-recognized process identities, versions, applicable review kinds,
permitted issuers, accepted evidence forms,
and references to the user's recognition. Preserve any user-selected substantive
review requirements in the external process contract. Core does not choose a
replacement model, aggregate reviewers, or universally impose independence.

New recognition rules cannot activate merely because a candidate branch edits a
YAML file. Local operation uses the explicitly enabled recognition version; merge
verification reads the recognized policy from the trusted base plus an applicable
user-authorized policy-change record. Initial enabling is an owner action. The
record must tie recognition to the actual policy digest, not just an author name.

The minimal proposal uses user-recognized process attestations and retained
source material within the existing owner-controlled local trust environment.
It checks consistency with the recognition rules, not cryptographic identity or
whether someone genuinely performed thoughtful review. Do not claim resistance
to a same-account process forging the entire provenance. Independent credentials
or a stronger signature trust root would be a separate trust-strength decision.

The owner has now recognized one concrete process for new-contract operation in
the 2026-09-14 delivery authorization: process
`codex-subagent-review` (`codex-subagent-review/v1`), performed by the
implementation agent outside the super-harness core using an independent Codex
subagent. It accepts `plan` and `code` conclusions issued as `codex-subagent`
JSON evidence. The complete requirements and source record are in
`.harness/review-recognition.yaml`; its `policy_digest` covers the process,
requirements, and recognition record. The process must retain the exact frozen
subject, original result, object identifier, provenance, and explicit
`approve`/`reject` conclusion. Blocking findings require fix and re-review;
execution failure, empty output, and skip are not approval. This recognition
does not retroactively change historical skips and does not expand beyond this
process.

## Commands, lifecycle, and shared enforcement

The following are proposed new-contract command responsibilities, not commands
available in the baseline version:

| Command | Responsibility |
|---|---|
| `plan ready <change> --plan <path>` | Snapshot the submitted plan. First submission enters AWAITING_PLAN_REVIEW. |
| `review import <change> --evidence <path>` | Validate an external conclusion and record its effect without preparing a producer round. |
| `implementation record <change> --assessment <path>` | Record reasoning and actual coverage without issuing approval. |
| `plan redeclare <change> --plan <path> --reason ...` | Submit a commitment revision linked to the effective approval. |
| `plan withdraw <change> --candidate <id> --reason ...` | Record that an unapproved pending/rejected revision will not be adopted; preserve the effective approval and candidate history. |
| `implementation start/reopen` | Require an applicable approval; reopening preserves that approval but invalidates current code completion. |
| `done` | Check coverage and applicability, execute verification, and establish the code subject needing review. |
| `attest verify` | Verify committed approval, code, verification, and coverage evidence against the actual PR contents, offline. |

Keep the main lifecycle states. First review rejection remains PLAN_REJECTED.
For a later commitment revision, retain one effective approval B1 and one pending
candidate B2. B2 submission/rejection does not erase B1 or permit B2 work. A B2
approval explicitly replaces the relevant commitments and triggers reassessment
of code/verification applicability; a previously frozen implementation returns
to implementation before it can become merge-ready under B2.

An unresolved B2 does not block implementation within B1. To finish delivery under
B1, the agent first uses `plan withdraw` to record non-adoption of pending or
rejected B2. This requires no new review or human permission: it abandons an
unapproved proposal, not an approved obligation. The operation names the exact
candidate and effective approval, is idempotent for the same disposition, and
cannot withdraw an approved revision or restore a superseded approval.

Before `done`, current code review, READY_TO_MERGE eligibility, and merge under B1,
the completion predicate requires no unresolved revision candidate. The active
plan/spec files and their references must describe the B1 commitments; compatible
implementation notes may remain with their assessments. Restore any B2-only
commitment text in the delivery documents. Retain B2's submitted snapshot and
rejection/non-adoption in the audit record, without presenting B2 as an effective
plan or deleting its history. A metadata withdrawal alone cannot establish that
the implementation conforms to B1; code review inspects the final actual subject
against B1 and current verification is still required.

If withdrawal/document corrections alter the frozen product subject, use the
existing reopen path and refresh code/verification evidence. If they do not,
retain only evidence that still matches; withdrawal itself emits no approval.
A late verdict for withdrawn B2 remains historical and cannot activate it. To
pursue it again, explicitly submit a new candidate. These completion conditions
are shared by CLI, state eligibility, and attestation; a stale READY_TO_MERGE
label cannot bypass them.

Record separate revision events so a first-plan rejection and a later candidate
rejection cannot accidentally have the same state effect. Declare all transitions
in the fixed transition module; do not assign state directly in CLI code. Code
rejection/reopen paths retain plan approval but cannot reuse stale completion.
Terminal Changes cannot be reopened by these paths.

Put approval/reference prerequisites in the shared event validation used by
writers and attestation. Normal writes must not create authorizing events through
`skip_validation=True`; existing non-authorizing audit callers can retain their
purpose. Replay legacy data through its historical interpretation, never through
a writable legacy escape route. Unknown/new-contract malformed records cannot
produce approval by tolerant parsing or state-name inference.

Validate evidence outside the writer lock where possible, then check the current
approval/candidate/evidence relationships again inside the existing validate-and-
append critical section. A concurrent change prevents stale approval from being
applied to a replacement candidate. State.yaml remains a derived cache, not an
independent authority. Reuse the shared validity predicate in gate policy rather
than constructing a second permission model there.

## Retiring core orchestration

Remove active begin/authorize/run-fail/round-budget/producer-dispatch/result-
aggregation and human-nonce execution flows. Keep subject preparation where it
belongs in plan submission and implementation completion. Remove model discovery
from review onboarding and instructions. Local reviewer profiles remain preserved
as user data, but the new core no longer reads them.

Retain only the legacy readers required to explain archived evidence and reports.
Do not retain a complete old execution loop to keep old commands working. Retired
commands either disappear or return a clear removal error; they cannot create a
round, ask for retry funding, or produce an automatic skip. Preserve historical
cost/failure/skip fields without adding a new cost-analysis feature.

## Direct cutover and actual leftover Changes

Perform one owner-local configuration/workflow update and one evidence-based
handling pass. New Changes use the new contract. Preserve old event bytes and
their old meaning; retain a small historical read discriminator where needed.
Do not infer a contract version from the absence of receipts, allowing an actor
to downgrade new evidence into the legacy path.

The first cutover PR is delivered under the existing contract. Its required CI
attestation job runs an isolated, non-editable verifier built from the trusted
PR BASE_SHA, against the candidate checkout and the actual base/head arguments.
It does not install the candidate package as the verifier for that PR. Candidate
tests separately exercise new-contract evidence and historical reads; they do
not authorize the cutover Change. Build/install the baseline outside the candidate
tree, and prevent PYTHONPATH/editable-import leakage back into candidate code.

Keep .harness/review-governance.yaml unchanged for this Change's baseline review
commands. Include .harness/review-recognition.yaml and its actual owner-recognition
record in the reviewed cutover target, but activate the new runtime/defaults only
after the old-contract review, verification, CI, and merge have completed. On
subsequent PRs the trusted base contains the new verifier and recognized policy,
so the same trusted-base CI installation selects the new contract. No candidate
flag or missing version can select the old verifier. After activation the new
core reads only review-recognition.yaml; the retained old governance/profile files
are inert historical data, not a second supported workflow. This is one local
cutover using an isolated baseline tool, not a shipped legacy execution mode.

| Change | Inspected evidence | Required handling before claiming cutover complete |
|---|---|---|
| 2026-09-04-windows-verification | ARCHIVED in the local event log. | Leave terminal and unchanged. Use only as a historical preservation fixture. |
| 2026-08-20-windows-file-locks | Local log says READY_TO_MERGE; plan/code approvals are skips. Merge commit da49e91db0fdcc05dae756bf36392e67d09ab7e7 (PR 121) is an ancestor of the inspected main. | Verify the merge-to-Change association and append the missing archive fact through the permitted lifecycle path. Do not convert skips into reviews or rerun completed work. |
| 2026-09-04-product-baseline | Branch codex/2026-09-04-product-baseline at 49f4cf0; plan unchanged from 734e753. Original user message on 2026-09-03 at 17:34:02.832Z explicitly passed human review; the log separately records a disclosed skip. Verification was invalidated; code review is unfinished. | Preserve applicable human plan approval with its actual original source and exact plan, independently of the unchanged skip record. Retain unfinished verification/code review. This approval does not cover the present evolution. |

These are inspected applicability findings, not completed lifecycle operations.
Recheck the actual records before applying the one-time handling. The original
human source is task 01a06354-2c16-7b91-b5a0-979ac6deb374; the plan-override event
is ev_01M1M5D2HFNZ7NKG8YPWT7XE5R. Preserve enough provenance to distinguish the
human statement from the agent-authored event summary; do not publish private
conversation content without an appropriate disclosure decision.

## Acceptance scenarios and traceability

No scenarios below have been run against a new implementation. D means a
deterministic contract test; S means a semantic review scenario, not a claim that
the core can infer meaning from arbitrary prose.

| Scenario | Type | Expected result | Obligations |
|---|---|---|---|
| A01: First plan absent/unapproved; attempt start, restart, invalidate, or raw milestone write. | D | All authorizing paths reject; no new approval event is appended. | O1 |
| A02: Empty conclusion, failed execution, wrong Change/kind/subject, or unrecognized process. | D | No implementation/merge permission. | O1, O6 |
| A03: Add an omitted fixture within the approved goal, approach, and acceptance conditions. | D + S | Coverage and reasoning update; effective plan approval is unchanged and no human permission is requested. | O2 |
| A04: Lower an acceptance condition to make a failure disappear. | D + S | Revised commitment needs its own approval; the old receipt cannot approve the new object. Review must reject falsely calling it a detail. | O2, O3 |
| A05: Multiple individually small changes cumulatively replace the goal. | S | The full change and assessment chain remain compared with effective approved commitments; review treats the changed goal as a revision. | O3 |
| A06: Submit/reject B2, continue B1 work, withdraw B2, restore B1 delivery documents, and run done/code review/merge; also receive late B2 approval. | D + S | Unresolved B2 blocks completion, not B1 implementation. Withdrawal needs no new approval, does not approve B2, and cannot undo an approved revision. Only a current verified/reviewed B1 subject can merge; late B2 evidence stays historical. | O2, O3, O4 |
| A07: Rebase preserves commitments but changes HEAD/base. | D + S | Plan approval is retained if its applicability remains; code evidence is checked against the newly computed subject. | O4 |
| A08: Modify product content after code approval or during verification. | D | Old evidence cannot establish current merge readiness. | O4 |
| A09: Commit only validated evidence after review. | D | No self-invalidating HEAD loop; arbitrary neighboring files still require coverage. | O4 |
| A10: Import a valid external conclusion with no core run/profile/budget records. | D | Import succeeds according to recognized policy. No reviewer invocation occurs. | O5 |
| A11: Recognized same-agent review versus an agent enabling an unrecognized self-review process. | D + S | First is allowed when explicitly recognized; second cannot activate policy or approve work. | O6 |
| A12: Replay old failures, skips, and archived Changes after direct cutover. | D | Historical meaning is unchanged; new writes cannot select old approval rules. | O7 |
| A13: Concurrent candidate replacement/import; duplicate or conflicting receipt. | D | Stale application is rejected; identical import is idempotent; conflicting reuse fails. | O1, O4, O6 |
| A14: Complete a PR with an omitted/new/renamed file or stale evidence from another Change. | D | Coverage/subject failure; no scope-only or historical-milestone pass. | O2, O4 |
| A15: Full first approval → fixture addition → approved commitment revision → code correction → verify/code review → attestation. | D + S | Complete sequence on native Windows and POSIX without core review orchestration or dropped checks. | O1–O7 |
| A16: Keep the plan file unchanged while an adopted independent spec lowers acceptance conditions; remove the original Git locator. | D + S | The stored artifact set still recovers the original constraints; new spec contents have a different subject and do not inherit approval. Compatible wording changes remain assessable without automatically revoking original authority. | O1, O3, O4 |
| A17: First cutover PR contains candidate new code/policy but complete old-contract evidence; next PR has the merged new contract in its trusted base. | D | First PR is verified by the isolated baseline tool; incomplete old evidence fails. Candidate tests validate new evidence separately. Next PR uses the new verifier and rejects a legacy-format downgrade. Neither a candidate flag nor altered import paths can pick the verifier. | O1, O5, O6, O7 |

## Remaining decisions and review readiness

The concrete recognized external process and its source-declaration rules still
need the user's recognition before activation. Supporting a process does not
authorize enabling it. This is distinct from reviewing this specification/plan
under the currently effective governance.

The documented local trust ceiling and unchanged hook failure policy must remain
visible in review; do not silently promise cryptographic origin or universal
prevention. Exact implementation identifiers and serializer details may be refined
within the above commitments, with scope handled under the rules then in force.

Saving these documents does not approve this plan, start the implementation,
execute cutover, complete historical Changes, or replace ratified decisions.
