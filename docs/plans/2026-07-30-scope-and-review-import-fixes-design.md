---
change: 2026-07-30-scope-and-review-import-fixes
stage: design
---

# Four mechanically-fixable defects from self-hosting

Each was hit for real while shipping `d-pitfall-is-proposed-decision`. All four are
**bugs, not knowledge** — the taxonomy that cut established says a defect you intend to
fix gets fixed, not recorded. Deliberately kept short: the previous cut's 600-line plan
document was itself the cause of four self-inflicted review rounds.

## 1. `scope.files` means two different things

`sensors/verification_runner.py:463` compares changed files against declared scope with
`core.scope_match.covered_by_scope` — segment-aware **prefix** matching, so a `tests/`
entry covers `tests/unit/x.py`. `engineering/attestation.py:273-274`, the merge gate, uses
**set membership** on canonical paths, so `tests/` matches only a file literally named
`tests/`. Local `verify` therefore reports clean while the merge gate produces one blocker
per file.

**Fix: align `verification_runner` to the merge gate's semantics, not the reverse.** The
loose side is the advisory local check; the strict side is the gate. Loosening the gate so
a `src/` entry covered everything beneath it would be a fail-open widening — the same
class of change rejected when `d-tier2-reconcile-touches-scope` considered exempting
reconcile stamps. `must_pass=False` stays: the drift check is advisory per
sensor-gate §3.1.4, and changing verdict semantics is a separate governance question.

`core/review_bundle.py:84` also calls `covered_by_scope` and **stays on prefix matching**:
it selects which `.md` files enter the review bundle, which is a convenience, not a safety
property. Two callers with genuinely different needs — recorded so the next reader does
not "unify" them into a bug.

## 2. Omitting `--scope` silently revokes `plan_artifacts`

`core/reducer.py` preserves `scope` when a `plan_ready` payload omits it (`if "scope" in p`)
but **always replaces** `plan_artifacts`. The asymmetry is deliberate and commented:
an empty re-submit revokes prior authorization. That property is worth keeping — it is what
stops a stale `plan_artifacts` list from authorizing edits forever.

What is not worth keeping is the silence. Re-emitting `plan_ready` without `--scope` drops
the `PLAN_REJECTED` carve-out that lets a change revise its own plan documents, with no
output saying so.

**Fix: `plan ready` warns when `--scope` is absent and the change's current
`plan_artifacts` is non-empty.** Warn, not refuse — refusing would block a deliberate
revocation, which is a legitimate act. Reducer semantics unchanged.

## 3. `review skip --source` reads as scoping and is only a label

`cli/review.py` `skip` sets `extra["source"]` for the audit trail and then emits the whole
role's PASS verdict (its own docstring: "== approve with reason=manual_skip"). Read as
"skip this one source", it approves a review nobody performed. That happened: during the
previous cut it emitted `plan_approved` before any reviewer had run, and the erroneous
event is permanent in the append-only stream.

**The capability the flag suggests already exists** — `review run fail --run-id … --reason`
retires one producer and leaves the round open. Adding a second way to do that would be
duplication, so the fix is not to make `--source` scope.

**Fix, two parts.** Teeth first:

- **Refuse `skip` when no round has been frozen in the current epoch.** You cannot pass a
  role no reviewer was ever asked to perform. *This is the arm that catches the mistake
  actually made* — `skip` was called before `review begin`.
- **Refuse `skip` while the latest round is open with `pending` runs**, naming them and
  pointing at `review result import` / `review run fail`.

Both are derivable from `engineering/review_runs.ReviewExecutionState`: rounds carry
`status` (`open` / `closed`) and each run carries `pending` / `imported` / `failed`.

Then the cosmetic half: rename the flag to `--stuck-source`. It keeps its disclosure value
(*which* participant was stuck when the role was passed) while no longer reading as a
scope selector. A breaking CLI change, acceptable at v0.1 experimental.

## 4. An `is_error` producer payload with a valid `structured_output` is accepted

Measured before designing, and the defect is narrower than first reported. The real
transient-failure shape — `{"is_error": true, "result": "API Error: 529 …"}` with no
`structured_output` — is **already rejected** by
`adapters/reviewer/claude_cli.parse_result`, which requires an object `structured_output`.
The residual hole is a payload carrying both `is_error: true` and a well-formed
`structured_output`; a producer would have to fail and still emit complete findings, which
does not happen in practice.

**Fix: reject an `is_error` payload outright, before looking at `structured_output`.** One
guard, theoretical hole closed, no behaviour change for any real payload. The
`private/OPEN-ITEMS.md` entry claiming import "accepts an `is_error` payload" is wrong as
written and is corrected by this cut.

## Not fixed, deliberately: dead-reference negative context

`doc refs --gate` flags a backticked snake_case token even where the prose names an
anti-pattern *not* to adopt ("no hand-maintained `ref_count` metadata"). Detecting a
negating clause is prose parsing — precisely what this engine avoids ("string/set work
only"), and an explicit inline opt-out marker would add an authoring surface, and its own
rot, to save one word of rewording. The workaround is a rule an author can follow and the
gate reports immediately.

Recorded as a `proposed` decision rather than fixed, because it is a precondition you must
know *before* you write a decision body — the body is hash-locked at `ratify`, so learning
it afterwards costs a re-ratify. This is the seventh requirement this project has cut.
