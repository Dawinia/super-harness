# Per-Severity Round Policy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make a review round's reject/approve decision key on the highest *finding severity* against a per-role, governance-configurable `blocking_severity` threshold (default `major`) — so a round whose worst finding is `minor` passes-with-open-finding instead of rejecting + mandating a full re-review. This directly kills the Step-1-measured tax (one `minor` doc-wording finding forced a whole second dual-source round = 203k tokens / ~28% of that change's review cost).

**Architecture:** One decision-point change plus one frozen-governance field. In `_close_round_if_terminal` (`cli/review.py`) the reject predicate changes from "any checklist item `status==fail`" to "any finding whose severity is at-or-above the round's frozen `blocking_severity`". The threshold is a new optional per-role field on `ReviewerRoleGovernance`, **frozen into the `review_round_started` event** at prepare time (exactly like `min_independent` / `require_distinct_model_families`) and read back onto `ReviewRoundState`, so mid-flight governance edits never retroactively change an open round's verdict. `scope_sufficient is not True` still hard-rejects. No new event types. **The pass-with-open-finding mechanism needs NO new plumbing**: `minor` findings already flow through the per-source `review_result_imported` events into `derive_open_findings` → the report's `findings_open_undisposed` counter, whether the round passes or rejects — the honesty law is satisfied by the existing event stream.

**Tech Stack:** Python 3.10+, Click, frozen dataclasses, pytest. Mirrors the existing frozen-governance discipline in `engineering/review_runs.py` and `engineering/review_governance.py`.

**Out of scope (this cut):** No `tier_hint` → review-governance wiring (deferred: `tier_hint` is an agent-self-declared, unenforced soft estimate — letting it relax review strictness is a governance/gaming question deserving its own brainstorm; see OPEN-ITEMS 2026-07-15). Threshold is uniform across tiers this cut, tuned only per-role in tracked governance.

---

## Design contract (the acceptance oracle)

**Severity ordering** (already exists at `cli/review.py:1243`): `{"blocker": 0, "major": 1, "minor": 2}` — lower ordinal = more severe.

**A finding BLOCKS iff** `severity_order[finding.severity] <= severity_order[threshold]`.
- threshold `major` (default): blocker ✓, major ✓, minor ✗ (does NOT block)
- threshold `blocker`: blocker ✓, major ✗, minor ✗
- threshold `minor`: blocker ✓, major ✓, minor ✓ (= today's "everything blocks" behavior)

**New reject predicate** in `_close_round_if_terminal`:
```
has_rejection = (scope_sufficient is not True) OR any(finding blocks)
```
Checklist `status` no longer drives the round reject decision. (The independent `failing_items` self-consistency check on `review approve` — `core/review_verdict.py:237` — is unrelated and stays.)

**Why max-finding-severity is sound, not a bypass:** `core/review_verdict.py:77-78` enforces `if any_fail and not findings: raise` — a checklist `fail` is *structurally impossible* without at least one finding, and the reviewer assigns each finding's severity. So the worst finding's severity faithfully encodes the reviewer's own judgment of "how bad is the worst thing here." Keying on it respects the reviewer's grading. To hard-block a change a reviewer raises a finding at-or-above the threshold; a checklist fail carrying only `minor` findings now passes-with-open-finding.

**Frozen-governance backward-compat rule:** a round is judged by the governance snapshot **at its open**. NEW rounds freeze `blocking_severity` into the `review_round_started` payload (governance default `major`). For historical `review_round_started` events that predate this field, the reader defaults the *absent* field to **`minor`** — reproducing the pre-feature "everything blocks" behavior those rounds were opened under. Do NOT add `blocking_severity` to the `frozen_governance_complete` gate (that gate governs required_sources/min_independent/distinct completeness for APPROVAL; a pre-feature round lacking the field must still be closable, just with the strict default).

**Honesty law (both directions):**
- blocker/major findings stay fail-closed (reject) at the default threshold — no soundness hole opened.
- a `minor` finding that now passes-with-open-finding MUST still land in `derive_open_findings` / report `findings_open_undisposed`. This is already true via the per-source `review_result_imported` event; a regression test locks it.

**Non-goals (YAGNI — do NOT build):** tier_hint wiring; per-tier thresholds; adding severity to checklist items; new event types; changing `derive_open_findings`; merge-gate changes; changing the harness dead-doc-ref hard reject (its synthetic `major` finding already trips the new predicate naturally).

---

## Task 1: `blocking_severity` on `ReviewerRoleGovernance` + validation

**Files:**
- Modify: `src/super_harness/engineering/review_governance.py:70-77` (dataclass) and `:176-194` (per-role parse)
- Test: `tests/unit/engineering/test_review_governance.py`

**Step 1: Write the failing tests**

```python
def test_role_blocking_severity_defaults_to_major(tmp_path):
    root = _write_governance(tmp_path, roles_yaml="""
      code-reviewer:
        participants: [codex, claude]
    """)
    gov = load_review_governance(root)
    assert gov.roles["code-reviewer"].blocking_severity == "major"

def test_role_blocking_severity_explicit_value_loads(tmp_path):
    root = _write_governance(tmp_path, roles_yaml="""
      code-reviewer:
        participants: [codex, claude]
        blocking_severity: blocker
    """)
    assert load_review_governance(root).roles["code-reviewer"].blocking_severity == "blocker"

def test_role_blocking_severity_rejects_unknown_value(tmp_path):
    root = _write_governance(tmp_path, roles_yaml="""
      code-reviewer:
        participants: [codex, claude]
        blocking_severity: nit
    """)
    with pytest.raises(ReviewGovernanceError, match="blocking_severity"):
        load_review_governance(root)
```
(Use / add a `_write_governance` helper matching the existing fixtures in this file — check what the file already uses to build a valid `.harness/review-governance.yaml`; there IS a valid-doc builder there already.)

**Step 2: Run to verify they fail** — `pytest tests/unit/engineering/test_review_governance.py -k blocking_severity -v` → FAIL (`blocking_severity` attribute / no error raised).

**Step 3: Implement.** In the dataclass (after `max_automatic_rounds_per_epoch`):
```python
    blocking_severity: str = "major"
```
In the per-role loop (`review_governance.py`, after the `max_rounds = _positive_int(...)` block ~line 188, before `roles[reviewer] = ...`):
```python
        blocking_severity = role.get("blocking_severity", "major")
        if blocking_severity not in {"blocker", "major", "minor"}:
            raise ReviewGovernanceError(
                f"review.roles.{reviewer}.blocking_severity must be one of "
                f"'blocker', 'major', 'minor'"
            )
```
Add `blocking_severity=blocking_severity` to the `ReviewerRoleGovernance(...)` constructor call.

**Step 4: Run** — `pytest tests/unit/engineering/test_review_governance.py -v` → PASS.

**Step 5: Commit** — `git commit -m "feat(review): add per-role blocking_severity governance field (default major)"`

---

## Task 2: Freeze `blocking_severity` into the round + read it onto `ReviewRoundState`

**Files:**
- Modify: `src/super_harness/cli/review.py:959-974` (round_started payload freeze) — grep the same function for any sibling governance-echo preview dict (see `review.py:~234`) and mirror the field there too if that dict is the `review prepare` preview.
- Modify: `src/super_harness/engineering/review_runs.py:45` (field) + `:285-290` (reader) + `:301-319` (constructor call)
- Test: `tests/unit/engineering/test_review_runs.py` (RoundState reader) + assert freeze in `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing tests**

Reader default + roundtrip (in `test_review_runs.py`, mirror how existing tests build a `review_round_started` event and call the derive function):
```python
def test_round_state_reads_frozen_blocking_severity():
    events = [_round_started_event(payload_extra={"blocking_severity": "blocker"})]
    state = derive_review_execution(events, "code-reviewer").rounds[0]
    assert state.blocking_severity == "blocker"

def test_round_state_missing_blocking_severity_defaults_to_minor():
    # Pre-feature events lack the field → reproduce strict "everything blocks".
    events = [_round_started_event(payload_extra={})]  # no blocking_severity key
    state = derive_review_execution(events, "code-reviewer").rounds[0]
    assert state.blocking_severity == "minor"
```
Freeze assertion (in `test_review_runs.py` CLI-level, extend an existing `_prepare`/`_begin` flow test): after `review prepare`, read the `review_round_started` event and assert `payload["blocking_severity"] == "major"` (governance default).

**Step 2: Run to verify fail** — `pytest tests/unit/engineering/test_review_runs.py -k blocking_severity -v` → FAIL.

**Step 3: Implement.**

`review_runs.py` dataclass field (after `require_distinct_model_families`, keep default = strict for absent-in-payload):
```python
    blocking_severity: str = "minor"
```
Reader (after the `require_distinct_model_families` block ~line 290):
```python
            raw_blocking_severity = payload.get("blocking_severity")
            blocking_severity = (
                raw_blocking_severity
                if raw_blocking_severity in {"blocker", "major", "minor"}
                else "minor"
            )
```
Add `blocking_severity=blocking_severity,` to the `ReviewRoundState(...)` constructor.

`review.py` round_started payload (in the `payload={...}` dict at ~970, alongside `min_independent`):
```python
            "blocking_severity": role.blocking_severity,
```

**Step 4: Run** — `pytest tests/unit/engineering/test_review_runs.py tests/unit/cli/test_review_runs.py -v` → PASS.

**Step 5: Commit** — `git commit -m "feat(review): freeze blocking_severity into the round + read onto RoundState"`

---

## Task 3: Swap the reject predicate to max-finding-severity

**Files:**
- Modify: `src/super_harness/cli/review.py:1352-1362` (inside `_close_round_if_terminal`)
- Test: `tests/unit/cli/test_review_runs.py`

**Step 1: Write the failing tests** (extend the existing `_prepare`/`_begin`/import flow used by `test_import_records_receipt_closes_round_and_emits_milestone` and `test_blocker_waits_for_every_source_then_rejects...`):

```python
def test_minor_only_round_approves_at_default_threshold(...):
    # Every source: checklist item status=fail + one MINOR finding.
    # Default governance blocking_severity=major → round APPROVES.
    ... import both sources with minor findings + a failing checklist item ...
    assert round_outcome == "approved"

def test_minor_finding_still_surfaces_in_open_undisposed(...):
    # Honesty regression: the minor finding from the APPROVED round is still
    # harvested by derive_open_findings for the change.
    from super_harness.core.review_verdict import derive_open_findings
    assert "MIN-1" in derive_open_findings(_change_events(root, "change"), "change")

def test_major_finding_still_rejects_at_default_threshold(...):
    # A major finding → round REJECTS (fail-closed regression).
    assert round_outcome == "rejected"

def test_blocking_severity_minor_restores_reject_on_minor(...):
    # Governance blocking_severity: minor → minor finding rejects again (old behavior recoverable).
    assert round_outcome == "rejected"

def test_scope_insufficient_rejects_regardless_of_severity(...):
    # scope_sufficient=false with only-minor findings still REJECTS.
    assert round_outcome == "rejected"
```

**Step 2: Run to verify fail** — new approve/threshold tests FAIL (today minor+checklist-fail → rejected).

**Step 3: Implement.** Replace lines 1353-1358:
```python
    aggregate = _aggregate_verdicts(imported)
    aggregate_findings = aggregate["findings"]
    severity_order = {"blocker": 0, "major": 1, "minor": 2}
    threshold_rank = severity_order.get(round_state.blocking_severity, 2)
    blocks = isinstance(aggregate_findings, list) and any(
        isinstance(f, dict)
        and severity_order.get(str(f.get("severity")), 99) <= threshold_rank
        for f in aggregate_findings
    )
    has_rejection = aggregate["scope_sufficient"] is not True or blocks
```
(Drop the `aggregate_checklist` / `has_failure` locals — checklist no longer drives the reject. Keep the rest of the function, including the dead-doc-ref block, unchanged: its synthetic `major` finding now trips `blocks` too, but the explicit `outcome = "rejected"` there is harmless belt-and-suspenders and stays.)

**Step 4: Run** — `pytest tests/unit/cli/test_review_runs.py -v` → PASS.

**Step 5: Commit** — `git commit -m "feat(review): reject rounds by max finding severity vs frozen threshold"`

---

## Task 4: Update the reviewer contract text

**Files:**
- Modify: `src/super_harness/engineering/review_contract.py` (the bundle instruction text near `:157`, "finding fields: id, severity ...")
- Test: `tests/unit/engineering/test_review_contract.py` (assert the new sentence renders)

**Step 1: Write the failing test** — assert the compiled bundle text contains the pass-with-open-finding contract sentence (grep-style substring assertion, mirroring existing bundle-text tests).

**Step 2: Run to verify fail.**

**Step 3: Implement.** Add one sentence to the reviewer-facing instructions, e.g.:
> To block this change, raise a finding at or above the round's blocking severity (default: `major`). A checklist item marked `fail` whose findings are all `minor` passes with the finding left open (recorded and surfaced in `super-harness report`), it does not reject.

**Step 4: Run** — PASS.

**Step 5: Commit** — `git commit -m "docs(review): document pass-with-open-finding contract in reviewer bundle"`

---

## Task 5: Full-suite audit + honesty/soundness verification

**Step 1:** `pytest -q` (full suite). Any pre-existing test whose *premise* was "a `minor`/checklist-fail round rejects" must be re-read: confirm via git-blame/comment that its intent was the OLD blanket-reject (now changed) vs. an unrelated concern. The grep audit already found the close-path reject tests use `blocker`/`major` (still reject) and the one `minor` test (`test_failed_source_retry_reuses_original_round_prior_findings`) exercises retry/`open_finding_ids`, not reject-on-minor — so breakage should be minimal. Fix any that legitimately encode the retired behavior; do NOT weaken a test that guards a real invariant.

**Step 2:** `ruff check . && mypy src` → clean.

**Step 3:** Run the harness quality gates the lifecycle will enforce: decision-check, doc-check, sync-check (per repo CI). Regenerate any derived artifact if a gate flags drift (e.g. `sync --agents-md`). `blocking_severity` is additive governance — confirm no decision-anchor reconcile is triggered; if `core/decisions.py` untouched, none should be.

**Step 4: Commit** any test fixups — `git commit -m "test(review): reconcile round-close tests with severity-graded reject"`

---

## Lifecycle & review intensity (matched to risk)

This change touches review **governance** (higher blast radius than #81's pure-read report), but is a well-converged single-decision change: one predicate + one frozen field, no gate/hot-path change, honesty preserved by the existing event stream. Plan for:
- **plan review:** design has low ambiguity (forks all resolved in brainstorm) — a single-source plan review or a disclosed skip is defensible; do NOT reflexively run 2-source multi-round.
- **code review:** governance-touching → run the governance-required **2 independent sources**, but do not pad rounds. Let a `minor`-only finding on THIS change pass-with-open per the very policy it ships (dogfood).

Scope files for `plan ready --scope`:
`src/super_harness/engineering/review_governance.py`, `src/super_harness/engineering/review_runs.py`, `src/super_harness/cli/review.py`, `src/super_harness/engineering/review_contract.py`, `tests/unit/engineering/test_review_governance.py`, `tests/unit/engineering/test_review_runs.py`, `tests/unit/cli/test_review_runs.py`, `tests/unit/engineering/test_review_contract.py`, `docs/plans/2026-07-16-per-severity-round-policy-plan.md` (+ any derived artifact a gate requires, e.g. `AGENTS.md` via `sync`).
