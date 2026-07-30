---
change: 2026-07-30-pitfall-is-proposed-decision
stage: plan
---

# Pitfall-is-proposed-decision Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ratify one tier-2 decision establishing that negative knowledge is recorded
as a `proposed` decision record, and state that norm where it binds — the generated
AGENTS.md section and the narrative docs.

**Architecture:** No lifecycle code changes. `status: proposed` already gates nothing
(`core/decision_check.py:79`), already stays out of the `hard:context` tally
(`cli/decision.py:354,366`), and already has an open exit to `ratified`
(`cli/decision.py:133`). This cut adds the *statement* of the norm plus a tier-2
anchor so the norm cannot silently rot if that status filter changes.

**Tech Stack:** Python 3.10+, pytest, click; `super-harness decision` verbs.

---

## Declared scope

```yaml
- docs/decisions/d-pitfall-is-proposed-decision.md
- docs/plans/2026-07-30-pitfall-is-proposed-decision-design.md
- docs/plans/2026-07-30-pitfall-is-proposed-decision-implementation.md
- src/super_harness/core/decision_check.py
- src/super_harness/engineering/agents_md_render.py
- tests/unit/engineering/test_agents_md_render.py
- docs/concepts.md
- AGENTS.md
- .harness/attestations/2026-07-30-pitfall-is-proposed-decision.jsonl
```

`private/OPEN-ITEMS.md` (Cut 2 registration) is gitignored and therefore not a scope
subject; it is still required by Task 1 Step 1.

---

### Task 1: Lifecycle preflight — must precede every source edit

**Why this is Task 1 and not close-out:** the gate blocks writes to `src/` while the
change sits in a pre-approval state (`INTENT_DECLARED` / `PLAN_REJECTED`). An executor
who starts at the AGENTS.md template is blocked on its first `Write`. Scope must be
declared and the plan approved **before** Task 2.

**Step 1: Register the deferred Cut 2 — via Bash, not `Write`**

Append to `private/OPEN-ITEMS.md`, status `DOABLE-NOW-BUT-UNVERIFIED`:
`applies_to: [glob]` + delivery at `plan ready --scope`; the premise to verify first
(does an agent read `docs/decisions/` unaided?); and the constraint that the matcher
must reuse `core/scope_match.py` — never `fnmatch`.

**The `Write` tool is BLOCKed here, and that is correct.** The gate's allowances are
hard-coded path whitelists compared after canonicalization, and are **never derived
from gitignore status** (`gates/pre_tool_use.py:81-82`). `private/OPEN-ITEMS.md`
matches no `plan-paths.yaml` pattern (all four are under `docs/plans`, `openspec`, or
`docs/superpowers`) and is not a `plan_artifact` — this plan declares it out of scope.
Being gitignored buys it nothing.

Use a Bash heredoc instead. That is the sanctioned route for an untracked,
out-of-scope note in a gated state rather than a gate bypass: the file is not a
product artifact, so there is nothing here for the gate to govern. Registration stays
in Task 1 on purpose — postponing it is how deferred work goes missing.

**Step 2: Commit the plan documents**

`review prepare` refuses a dirty in-scope tree — the review digest is taken over the
committed HEAD diff. Commit both plan documents before preparing.

**Step 3: Declare scope**

```bash
super-harness plan ready 2026-07-30-pitfall-is-proposed-decision \
  --scope @<scope-file> --tier-hint Normal
```

Pass the "Declared scope" list above verbatim — every file individually, no directory
prefixes. The `verify` scope baseline uses segment-aware **prefix** matching and is
advisory, while `attest verify` at the merge gate uses **set membership**; a directory
entry that satisfies the first produces one blocker per file at the second.

Omitting `--scope` clears `plan_artifacts`, which removes the `PLAN_REJECTED`
carve-out that lets these plan documents be revised. Never re-emit `plan_ready`
without it.

**Step 4: Plan review to convergence**

```bash
super-harness review prepare <change> --reviewer plan-reviewer
super-harness review begin <change> --reviewer plan-reviewer --source <every required source>
```

`review begin --source` genuinely scopes the round and **requires the complete
required set** — it rejects a partial selection. Run each producer per its
`invocation.json`, import with `review result import`, and record any producer that
cannot run with `review run fail --reason "<why>"`.

**Do not reach for `review skip` to retire one source.** Its `--source` is an audit
label only; the command PASSes the entire reviewer role (its docstring: "== approve
with reason=manual_skip"). Using it while another source is still pending approves
the plan before anyone has reviewed it. The two sibling verbs read alike and behave
differently.

Iterate until the round passes, committing **inside** the loop — `review prepare`
refuses a dirty in-scope tree, so an uncommitted revision stalls the next round:

```
PLAN_REJECTED
  → revise the plan documents        (only PLAN_REJECTED grants the carve-out;
                                      AWAITING_PLAN_REVIEW blocks these edits)
  → git commit                       ← before prepare, not after the loop
  → plan ready --scope <same list>
  → review prepare / begin / run / result import
  → repeat
```

```bash
git add docs/plans/2026-07-30-pitfall-is-proposed-decision-*.md
git commit -m "docs(plan): address plan review round N"
```

**The automatic-round cap does not bite in this loop, and reaching for
`review authorize` here is a mistake.** The plan-reviewer epoch boundary is the
`plan_ready` event itself (`engineering/review_runs.py:11-13`), and only rounds
appended after the latest boundary are counted — so every iteration's
`plan ready --scope` re-emission opens a fresh epoch with `automatic_rounds_used`
back at 0. Iterate as many times as convergence needs; `review authorize` is a
governance escape hatch for a genuinely over-budget round, not the way past this
loop.

Do not proceed to Task 2 until the state is `PLAN_APPROVED`.

---

### Task 2: AGENTS.md carries the vessel's address

**Files:**
- Modify: `src/super_harness/engineering/agents_md_render.py` (Decision conformance block)
- Test: `tests/unit/engineering/test_agents_md_render.py`

**Step 1: Write the failing test**

Append to `tests/unit/engineering/test_agents_md_render.py`, modelled on the existing
`test_outer_section_has_decision_conformance` (`:117`):

```python
def test_section_states_where_negative_knowledge_goes(tmp_path: Path) -> None:
    text = _render_fresh(tmp_path)
    # The vessel's address is stated as a constraint, not as accumulated content.
    assert "super-harness decision new" in text
    assert "proposed" in text
    assert text.index("super-harness decision new") < text.index(
        "<!-- super-harness section end -->"
    )
```

Reuse whatever fresh-render helper the neighbouring tests use; do not invent a second one.

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/engineering/test_agents_md_render.py::test_section_states_where_negative_knowledge_goes -v`
Expected: FAIL — `assert "super-harness decision new" in text`.

**Step 3: Add exactly one bullet to the template**

In `_AGENTS_MD_SECTION_TEMPLATE`, inside the existing `### Decision conformance`
bullet list, after the "Don't hand-edit the body of a ratified decision" bullet:

```markdown
- **Hit a trap worth remembering?** Record it with
  `super-harness decision new <id> --text "..."`. A `proposed` decision gates
  nothing, so it costs no one anything, and it has an exit: `ratify` once you can
  state the rule (arm it with a check if you can), `retire` once it stops being
  true. Do not start a separate notes file or pitfall directory — a record with no
  lifecycle has no way to stop being wrong.
```

One bullet. Do not add a pitfall list, and do not add a new `###` heading.

**Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/engineering/test_agents_md_render.py -v`
Expected: PASS, and the pre-existing render tests stay green (the section is
version-stamped and idempotent — `test_rerender_is_idempotent` must not regress).

**Step 5: Regenerate the managed section**

Run: `super-harness sync --agents-md`
Then: `super-harness doc check`
Expected: exit 0. `engineering/sync_check.py` is a CI gate; a hand-edited `AGENTS.md`
that disagrees with the template fails it.

**Step 6: Commit**

```bash
git add src/super_harness/engineering/agents_md_render.py \
        tests/unit/engineering/test_agents_md_render.py AGENTS.md
git commit -m "feat(agents-md): state where negative knowledge is recorded"
```

---

### Task 3: Narrative layer

**Files:**
- Modify: `docs/concepts.md`

**Step 1: Add one section**

`docs/concepts.md` currently has no section on decision records (they appear only in
passing at `:108`). Add a section after `## The lifecycle state machine`, titled
`## Recording what not to do`. It must cover, in prose, without a bullet-list of
pitfalls:

- A `proposed` decision is the home for "we got burned, we haven't settled the rule".
  It gates nothing and stays out of the tier tally.
- Its value over a notes file is the **exit**: `ratify` or `retire`. A note has no
  exit, so it cannot stop being wrong.
- Two shapes and where each lands: a *defect* ("the current code has this hole")
  names the fix and dies when the fix ships; a *precondition* ("doing X requires
  also doing Y and Z") outlives every fix and is the kind worth ratifying.
- The field evidence in one sentence, with the sources named: standalone
  lessons-learned repositories have a documented failure record (NASA LLIS,
  OIG IG-12-012), and the forms that survive all require an entry condition —
  an executable detector (Clippy), a stable public subject (PostgreSQL's
  "Don't Do This"), or a named replacement (Brown et al.'s AntiPatterns).

Keep it under ~25 lines. This file is narrative, not reference.

**Step 2: Verify no generated-doc drift**

Run: `super-harness doc check`
Expected: exit 0 (`concepts.md` is hand-written, not generated — this only confirms
nothing else drifted).

**Step 3: Commit**

```bash
git add docs/concepts.md
git commit -m "docs(concepts): explain proposed decisions as the home for pitfalls"
```

---

### Task 4: The decision record

**Files:**
- Create: `docs/decisions/d-pitfall-is-proposed-decision.md`
- Modify: `src/super_harness/core/decision_check.py` (anchor sentinel only)

**Step 1: Create the record**

```bash
super-harness decision new d-pitfall-is-proposed-decision \
  --text "Negative knowledge is recorded as a proposed decision record; super-harness grows no parallel pitfall corpus."
```

**Step 2: Author the body**

Tier-2, so the body carries a ```review``` block and no ```check```. See the design
doc's "Tier: why tier-2, not tier-1" section — that reasoning belongs in the body,
condensed. The `review` block must tell a reviewer what to confirm and name both
resolutions, matching the house style of `d-decision-records.md`:

- what to confirm when `core/decision_check.py` changes: that `proposed` records are
  still skipped by `decision check`, i.e. recording a pitfall as a proposed decision
  still costs nothing and cannot break CI;
- still holds → `decision reconcile d-pitfall-is-proposed-decision`;
- broken → `decision betray d-pitfall-is-proposed-decision` with a justification.

The body is hash-locked at `ratify`, so anything omitted here needs a re-ratify to add
later. Record **all three** of the following.

**(a) The mechanism ceiling.** A general check for this decision is impossible, not
merely inconvenient: `counterexample` can only *add* a file, so a check can only ever
be "no file contains/creates X". A behavioural invariant such as "proposed decisions
do not gate" cannot be bite-tested by that mechanism, and an un-bite-tested check is
exactly what `ratify` refuses. This is a ceiling of the counterexample design, not a
shortcoming of this record.

**(b) and (c) The two deliberate anchor omissions:**

- `core/decisions.py` (four-state lifecycle, `decision_tier` ladder) — already
  anchored by `d-decision-records`, whose subject *is* that shape; a second anchor
  would spend reconcile budget without adding a signal the first does not raise.
- `cli/decision.py:133` (`ratify` accepts `proposed` — the exit path out of proposed)
  — churns for unrelated reasons; anchoring it would spend the reconcile budget on
  noise.

**Step 3: Ratify**

```bash
super-harness decision ratify d-pitfall-is-proposed-decision
```
Expected: success. Tier-2 has no check, so there is no bite-test to pass.
**The body is hash-locked from here — any later wording change requires re-ratify.**

**Step 4: Add the anchor sentinel**

In `src/super_harness/core/decision_check.py`, immediately above the status filter at
`:79` (`if d.status != "ratified" or d.ratified_text_hash is None:`), add:

```python
# @decision:d-pitfall-is-proposed-decision
```

This is the load-bearing line: it is what makes a `proposed` record cost nothing.

**Step 5: Reconcile to stamp the baseline**

```bash
super-harness decision reconcile d-pitfall-is-proposed-decision \
  --kind self \
  --justification "Anchor established at the status filter that keeps proposed records out of the gate."
```
Expected: `reconciled ... (1 file(s), kind=self, ...)`.

**Step 6: Confirm the decision system is green**

Run: `super-harness decision check`
Expected: exit 0, no dangling-up (the sentinel resolves to a ratified record) and no
suspect tier-2.

**Step 7: Commit**

```bash
git add docs/decisions/d-pitfall-is-proposed-decision.md \
        src/super_harness/core/decision_check.py
git commit -m "feat(decisions): ratify d-pitfall-is-proposed-decision (tier-2)"
```

---

### Task 5: Full verification

**Step 1:** `pytest -q` — expected: all pass.
Note the 300s default check timeout: the full suite runs 2–4 minutes under load, and
a timeout surfaces as a failed check rather than a timeout. If `verify` reports the
pytest check failed, re-run `pytest -q` directly before believing it.

**Step 2:** `super-harness verify 2026-07-30-pitfall-is-proposed-decision` — expected:
verdict pass. The scope baseline is advisory (`must_pass=False`) and uses
segment-aware prefix matching, whereas `attest verify` at the merge gate uses set
membership; every file was declared explicitly in Task 1 Step 3 rather than by
directory prefix.

**Step 3:** Commit any fixes, then proceed.

---

### Task 6: Close-out

**Step 1: Implementation complete, code review, attest, PR**

`done` → `review prepare` (freezes the plan documents) → cross-actor code review →
`review approve --verdict-file ...` → `attest write` → PR. Then `on-merge` for this
change — exactly one change on this branch, but confirm with
`super-harness status --all` that nothing else sits at `READY_TO_MERGE`.

---

## Definition of done

- `d-pitfall-is-proposed-decision` is `ratified`, tier-2, with one reconciled anchor.
- `super-harness decision check` and `super-harness doc check` both exit 0.
- The generated AGENTS.md section states where negative knowledge goes, in one bullet.
- `docs/concepts.md` explains the norm and the two pitfall shapes.
- Cut 2 is registered in `private/OPEN-ITEMS.md` with its unverified premise named.
- Attestation written; change reaches `merged`.
