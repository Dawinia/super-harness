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

`covered_by_scope` **stays on prefix matching**, and its caller census is wider than a
first pass suggests — the census matters, because "leave it loose" is only defensible for
the callers where loose is harmless:

- `core/review_bundle.py:84` picks which `.md` files enter a review bundle. Loose really is
  a convenience there: a wider `.md` selection only gives the reviewer more to read.
- `split_changed_by_scope` and `split_changed_by_scope_between`, in `core/scope_match.py`
  itself, back `review_bundle.assemble_bundle` and `engineering/review_contract.py:265,334`.
  Their `in_scope` half feeds the frozen inspection ranges and the review digest; their
  `out_of_scope` half is what `review prepare` prints as the reviewer's scope-drift warning.

**That second group is not a convenience, and this change does not fix it.** Because the
matcher is loose, a declared `tests/` entry keeps `tests/unit/x.py` out of the
`out_of_scope` list, so `review prepare` still under-reports exactly the drift the merge
gate blocks on — the same under-report removed from the `verify` baseline here. Fixing it
would change the frozen inspection ranges and every stored bundle digest, which is its own
cut. It is recorded as a known residual in the `core/scope_match.py` docstring rather than
asserted away; that docstring is also where the stale claim that the verification baseline
points at this matcher gets corrected.

## 2. Omitting `--scope` silently revokes `plan_artifacts`

`core/reducer.py` preserves `scope` when a `plan_ready` payload omits it (`if "scope" in p`)
but **always replaces** `plan_artifacts`. The asymmetry is deliberate and commented:
an empty re-submit revokes prior authorization. That property is worth keeping — it is what
stops a stale `plan_artifacts` list from authorizing edits forever.

What is not worth keeping is the silence. Re-emitting `plan_ready` without `--scope` drops
the `PLAN_REJECTED` carve-out that lets a change revise its own plan documents, with no
output saying so.

**Fix: `plan ready` warns when the emit will leave `plan_artifacts` empty while the previous
state had them non-empty.** Keyed on the outcome, not on the flag — that covers both paths to
the same revocation: no `--scope` at all, and a `--scope` carrying no frontmatter-marked plan
doc, which is the likelier one (someone re-passes scope but drops the plan document from the
list). Warn, not refuse — refusing would block a deliberate revocation, which is legitimate.
Reducer semantics unchanged, and the message names `plan redeclare` because by the time it
prints, `plan ready` is already illegal from the state the emit produced.

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

- **Refuse `skip` when the role has automated participants, its producers are resolvable,
  and no round has been frozen in the current epoch.** Arm A carries **three** silent
  carve-outs, all of them load-bearing, because each marks a case where "no rounds" says
  nothing about whether a reviewer was asked:

  1. **No governance file at all** — `skip` has always supported an ungoverned workspace.
     Note the deliberate asymmetry: a governance file that is *present but malformed* fails
     CLOSED through `_load_governance_or_exit`. Conflating the two would have made the guard
     removable by corrupting one token of a tracked config, which is how it shipped in the
     first draft and what the code review caught.
  2. **A human-only role** — see below.
  3. **Producers that cannot be resolved to profiles** (`ReviewProfilesError`).
     `.harness/review-profiles.local.yaml` is gitignored while `review-governance.yaml` is
     tracked, so a collaborator, or anyone without the reviewer CLIs installed, has
     automated participants declared and no local profile. There no round can ever be
     frozen, and both steps of Arm A's own hint fail — `prepare` and `begin` each exit 2 on
     the missing profile. Without this carve-out the guard strands the change in
     `AWAITING_CODE_REVIEW`, which is precisely the stuck-reviewer case `skip` exists for.

  What remains after the carve-outs is the case worth refusing: **freezing a round was
  available and you simply never asked.** *That is the mistake actually made* — `skip` was
  called before `review begin`.

  **The refusal binds `--override` too, and that is the point.** The erroneous
  `plan_approved` that motivated this cut was itself emitted with `--override --reason` and
  zero rounds frozen (`.harness/events.jsonl:1354` carries `"skipped": true,
  "override": true`). A guard that any `--override` can bypass would not have stopped it, so
  the guard sits deliberately ahead of the override check. The escape hatch is preserved by
  widening the *silent* cases to every reason freezing is impossible — unresolvable profiles,
  no git repository, a missing base branch, a `BundleError` the author cannot clear — not by
  trusting the flag. A malformed local profiles file is not such a case: like a malformed
  tracked governance file, it fails CLOSED, so corrupting one token cannot remove the guard.

  **Why the condition, stated correctly.** For an automated role, "a reviewer was asked" has
  a mechanical trace: a frozen round. For a human-only role it has none until
  `review human confirm` mints one (`cli/review.py:2319-2320`), so "no rounds" there cannot
  distinguish *nobody was asked* from *the human has not confirmed yet* — and the guard must
  not guess. The blast radius makes this decisive rather than academic: `init`'s skeleton
  governance ships `participants: [human]` for **both** plan-reviewer and code-reviewer
  (`cli/init.py:207-215`; the wizard falls back to `["human"]` at `:309`), so every fresh
  install is human-only on both roles. An unconditional guard would change behaviour for
  every new adopter and strand an agent-driven lifecycle behind a flow that needs an
  interactive TTY (`cli/review.py:2189-2196`) and a prepared packet
  (`_read_packet_or_exit`, `cli/review.py:2057`).

  Two earlier framings of this were wrong and are recorded so they are not re-derived. It is
  **not** true that an unconditional guard would leave a human-only role unpassable — the
  `review prepare` → `review human draft` → `review human confirm` path exists. And an
  unconditional guard would **not** close a self-bypass hole: `skip --override` records a
  *disclosed* no-evidence pass (`skipped: true`, `override: true`, reason in the event stream
  and printed by the merge gate), whereas `confirm`'s TTY guard exists to stop *fabricated
  human evidence*. Those are different acts, and conflating them overstated the benefit.
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
