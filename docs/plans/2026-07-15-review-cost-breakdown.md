# Review-Cost Breakdown View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a role×source×round breakdown of review-side token cost (with per-round findings-raised density) to `super-harness report`, so the flat "~3.58M review tokens" total becomes attributable — the instrument Step 2 (risk-tiered review intensity) will tune against.

**Architecture:** Pure-read, additive. A new `_cost_breakdown()` reducer in `engineering/value_report.py` folds the SAME event stream (`review_result_imported` for tokens+findings, `review_round_closed` for outcome) into a list of `CostBreakdownRow`. No new event types, no gate/hot-path/governance change. JSON output carries the full per-round list; the human view aggregates to one row per role×source (typically ≤4 = 2 roles × 2 sources; legacy/`unknown` or custom-governance labels add rows — every group is shown, never capped) so it stays compact on a 66-change repo. `--brief` is untouched. Every number can show a negative; `tokens=None` (usage not captured) is rendered `—`, never `0`.

**Tech Stack:** Python 3.10+, Click, dataclasses (`asdict` recurses nested frozen dataclasses → JSON for free), pytest. Mirrors the existing `_review_cost` / `_finding_counts` reducer style and `tests/unit/engineering/test_value_report.py` fixtures.

---

## Design contract (the acceptance oracle)

Row identity = `(change_id, role, round_id, source)`. Fields per row:

| field | source | honesty rule |
|---|---|---|
| `role` | `review_result_imported.payload.reviewer` | missing → `"unknown"` |
| `source` | `.payload.source` | missing → `"unknown"` |
| `change_id` | `Event.change_id` | — |
| `round` | ordinal of `round_id` within `(change_id, role)`, first-seen order | missing `round_id` → `0` |
| `round_id` | `.payload.round_id` | missing → `""` |
| `tokens` | `_usage_tokens(receipt.usage)` | `None` = not captured — MUST stay distinct from `0` |
| `findings_raised` | `len(verdict.findings)` (raised, NOT resolved) | non-list → `0` |
| `outcome` | last `review_round_closed.outcome` for that `round_id` | none → `"open"` |

Non-goals (YAGNI — do NOT build): Step 2 tier tuning / `ReviewerRoleGovernance` edits; new event types; `--by` selector; model-family / cost-class / per-change-total columns; team aggregation; README edits.

Windowing: breakdown is computed over the SAME `windowed` events as the other cost metrics (respects `--since`/`--until`).

Malformed-event guard: reuse the dict-guard style (`payload if isinstance(...,dict) else {}`) — the module NEVER raises.

---

## Task 1: `CostBreakdownRow` + core row extraction (no ordinal yet)

**Files:**
- Modify: `src/super_harness/engineering/value_report.py`
- Test: `tests/unit/engineering/test_value_report.py`

**Step 1: Write the failing test**

Add near the other fixtures a helper that emits a full-fidelity import event, then the test:

```python
def _import_full(eid, change, ts, *, reviewer, source, round_id, usage=None, findings=()):
    payload = {
        "reviewer": reviewer,
        "source": source,
        "round_id": round_id,
        "receipt": {"usage": usage} if usage is not None else {},
        "verdict": {"findings": [{"id": f} for f in findings]},
    }
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": source},
        "framework": "plain", "payload": payload,
    })


def test_cost_breakdown_one_row_per_run(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="r1", usage={"total_tokens": 620000},
                     findings=["F1", "F2"]),
        _import_full("e2", "c1", "2026-07-02T00:01:00Z", reviewer="plan-reviewer",
                     source="claude", round_id="r1", usage={"total_tokens": 580000},
                     findings=["F3"]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    rows = report.cost_breakdown
    assert len(rows) == 2
    codex = next(r for r in rows if r.source == "codex")
    assert codex.role == "plan-reviewer"
    assert codex.change_id == "c1"
    assert codex.round_id == "r1"
    assert codex.tokens == 620000
    assert codex.findings_raised == 2
    assert codex.outcome == "open"          # no review_round_closed seeded


def test_cost_breakdown_missing_usage_is_none_not_zero(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="code-reviewer",
                     source="claude", round_id="r1", usage=None, findings=[]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.cost_breakdown[0].tokens is None      # NOT 0 — usage not captured
    assert report.cost_breakdown[0].findings_raised == 0


def test_cost_breakdown_tolerates_missing_source_and_round(tmp_path):
    # legacy/minimal import event (mirrors _import_with_usage: no source/round_id)
    events_file = _write_events(tmp_path, [
        _import_with_usage("e1", "c1", "2026-07-02T00:00:00Z", {"total_tokens": 100}),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    row = report.cost_breakdown[0]
    assert row.source == "unknown"
    assert row.round_id == ""
    assert row.tokens == 100
```

**Step 2: Run to verify it fails**

Run: `pytest tests/unit/engineering/test_value_report.py -k cost_breakdown -v`
Expected: FAIL — `AttributeError: 'ValueReport' object has no attribute 'cost_breakdown'`.

**Step 3: Write minimal implementation**

In `value_report.py`, add the dataclass after `ValueReport` and the reducer after `_review_cost`:

```python
@dataclass(frozen=True)
class CostBreakdownRow:
    role: str
    source: str
    change_id: str
    round: int
    round_id: str
    tokens: int | None
    findings_raised: int
    outcome: str


def _round_outcomes(events: list[Event]) -> dict[str, str]:
    """round_id -> last-seen review_round_closed outcome."""
    out: dict[str, str] = {}
    for ev in events:
        if ev.type != "review_round_closed":
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        rid, outcome = payload.get("round_id"), payload.get("outcome")
        if isinstance(rid, str) and isinstance(outcome, str):
            out[rid] = outcome
    return out


def _cost_breakdown(events: list[Event]) -> tuple[CostBreakdownRow, ...]:
    """One row per imported review run: where review tokens went + how many
    findings that run RAISED (density). Never raises; missing dims -> 'unknown'."""
    outcomes = _round_outcomes(events)
    rows: list[CostBreakdownRow] = []
    for ev in events:
        if ev.type != "review_result_imported":
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        role = payload.get("reviewer")
        source = payload.get("source")
        round_id = payload.get("round_id")
        raw_receipt = payload.get("receipt")
        receipt = raw_receipt if isinstance(raw_receipt, dict) else {}
        verdict = payload.get("verdict")
        findings = verdict.get("findings") if isinstance(verdict, dict) else None
        rid = round_id if isinstance(round_id, str) and round_id else ""
        rows.append(CostBreakdownRow(
            role=role if isinstance(role, str) and role else "unknown",
            source=source if isinstance(source, str) and source else "unknown",
            change_id=ev.change_id,
            round=0,                                  # ordinal assigned in Task 2
            round_id=rid,
            tokens=_usage_tokens(receipt.get("usage")),
            findings_raised=len(findings) if isinstance(findings, list) else 0,
            outcome=outcomes.get(rid, "open"),
        ))
    return tuple(rows)
```

Wire it into `build_value_report` (add field to the `ValueReport(...)` construction) and add `cost_breakdown: tuple[CostBreakdownRow, ...]` as the LAST field of `ValueReport` (after `armed_decisions`). Compute over `windowed`:

```python
        armed_decisions=_armed_decisions(workspace_root),
        cost_breakdown=_cost_breakdown(windowed),
    )
```

**Step 4: Run to verify it passes**

Run: `pytest tests/unit/engineering/test_value_report.py -k cost_breakdown -v`
Expected: PASS (3 tests). `outcome == "open"`, `tokens is None` distinct from 0, `source == "unknown"` for the minimal event.

**Step 5: Commit**

```bash
git add src/super_harness/engineering/value_report.py tests/unit/engineering/test_value_report.py
git commit -m "feat(report): add CostBreakdownRow + per-run cost extraction"
```

---

## Task 2: round ordinal within (change_id, role)

**Files:**
- Modify: `src/super_harness/engineering/value_report.py` (`_cost_breakdown`)
- Test: `tests/unit/engineering/test_value_report.py`

**Step 1: Write the failing test**

```python
def test_cost_breakdown_assigns_round_ordinals_per_change_and_role(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="rA", usage={"total_tokens": 1}),
        _import_full("e2", "c1", "2026-07-02T01:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="rB", usage={"total_tokens": 1}),
        # different role, first round -> ordinal restarts at 1
        _import_full("e3", "c1", "2026-07-02T02:00:00Z", reviewer="code-reviewer",
                     source="codex", round_id="rC", usage={"total_tokens": 1}),
        # different change, same round_id-space -> ordinal restarts at 1
        _import_full("e4", "c2", "2026-07-02T03:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="rD", usage={"total_tokens": 1}),
    ])
    rows = build_value_report(
        events_file, since=None, until=None, workspace_root=tmp_path
    ).cost_breakdown
    by_rid = {r.round_id: r.round for r in rows}
    assert by_rid["rA"] == 1
    assert by_rid["rB"] == 2          # 2nd plan round in c1
    assert by_rid["rC"] == 1          # code-reviewer restarts
    assert by_rid["rD"] == 1          # c2 restarts
```

**Step 2: Run to verify it fails**

Run: `pytest tests/unit/engineering/test_value_report.py -k round_ordinal -v`
Expected: FAIL — all ordinals are `0`.

**Step 3: Write minimal implementation**

Assign ordinals before building rows (first-seen order of `round_id` per `(change_id, role)`); skip empty `round_id` (stays 0). Replace the row-append loop body to compute ordinal:

```python
def _cost_breakdown(events: list[Event]) -> tuple[CostBreakdownRow, ...]:
    outcomes = _round_outcomes(events)
    ordinals: dict[tuple[str, str], dict[str, int]] = {}     # (change, role) -> {round_id: n}
    rows: list[CostBreakdownRow] = []
    for ev in events:
        if ev.type != "review_result_imported":
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        role_raw = payload.get("reviewer")
        source_raw = payload.get("source")
        round_raw = payload.get("round_id")
        role = role_raw if isinstance(role_raw, str) and role_raw else "unknown"
        source = source_raw if isinstance(source_raw, str) and source_raw else "unknown"
        rid = round_raw if isinstance(round_raw, str) and round_raw else ""
        ordinal = 0
        if rid:
            seen = ordinals.setdefault((ev.change_id, role), {})
            ordinal = seen.setdefault(rid, len(seen) + 1)
        raw_receipt = payload.get("receipt")
        receipt = raw_receipt if isinstance(raw_receipt, dict) else {}
        verdict = payload.get("verdict")
        findings = verdict.get("findings") if isinstance(verdict, dict) else None
        rows.append(CostBreakdownRow(
            role=role, source=source, change_id=ev.change_id,
            round=ordinal, round_id=rid,
            tokens=_usage_tokens(receipt.get("usage")),
            findings_raised=len(findings) if isinstance(findings, list) else 0,
            outcome=outcomes.get(rid, "open"),
        ))
    return tuple(rows)
```

**Step 4: Run to verify it passes**

Run: `pytest tests/unit/engineering/test_value_report.py -k "cost_breakdown or round_ordinal" -v`
Expected: PASS (all Task 1 + Task 2 tests).

**Step 5: Commit**

```bash
git add src/super_harness/engineering/value_report.py tests/unit/engineering/test_value_report.py
git commit -m "feat(report): assign per-change per-role round ordinals to breakdown"
```

---

## Task 3: outcome wiring + JSON shape (asdict)

**Files:**
- Test: `tests/unit/engineering/test_value_report.py` (reducer outcome)
- Test: `tests/unit/cli/test_report.py` (JSON envelope shape) — check exact path exists first with `ls tests/unit/cli/test_report.py`

**Step 1: Write the failing tests**

Reducer outcome test (value_report):

```python
def test_cost_breakdown_row_carries_round_outcome(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="r1", usage={"total_tokens": 1}, findings=[]),
        _round_closed("e2", "c1", "2026-07-02T00:05:00Z", "rejected"),  # helper needs round_id
    ])
    rows = build_value_report(
        events_file, since=None, until=None, workspace_root=tmp_path
    ).cost_breakdown
    assert rows[0].outcome == "rejected"
```

NOTE: the existing `_round_closed` helper omits `round_id`. Extend it to accept and emit `round_id` (default `"r1"`) so this test can bind the outcome:

```python
def _round_closed(eid, change, ts, outcome, round_id="r1"):
    return json.dumps({
        "event_id": eid, "type": "review_round_closed", "change_id": change,
        "timestamp": ts, "actor": {"type": "sensor", "identifier": "review"},
        "framework": "plain", "payload": {"round_id": round_id, "outcome": outcome},
    })
```

JSON shape test (cli/test_report.py — adapt the file's existing CliRunner + `--json` pattern):

```python
def test_report_json_includes_cost_breakdown_rows(tmp_path, ...):
    # seed a repo with one plan-reviewer import (reuse the file's seeding helper),
    # invoke `--json report`, json.loads stdout, assert:
    data = ...  # parsed envelope["data"]
    assert isinstance(data["cost_breakdown"], list)
    row = data["cost_breakdown"][0]
    assert set(row) >= {"role", "source", "change_id", "round", "round_id",
                        "tokens", "findings_raised", "outcome"}
```

**Step 2: Run to verify they fail**

Run: `pytest tests/unit/engineering/test_value_report.py -k round_outcome tests/unit/cli/test_report.py -k cost_breakdown -v`
Expected: FAIL — outcome `"open"` (helper had no round_id) / KeyError `cost_breakdown`.

**Step 3: Implement**

Reducer already reads `round_id` from `review_round_closed` (Task 1's `_round_outcomes`). The only change is the TEST helper gaining `round_id`. For JSON: `asdict(ValueReport)` already recurses the nested `CostBreakdownRow` tuple into a list of dicts — no CLI code change needed. Confirm by running.

**Step 4: Run to verify they pass**

Run: `pytest tests/unit/engineering/test_value_report.py tests/unit/cli/test_report.py -v`
Expected: PASS (full files, no regressions).

**Step 5: Commit**

```bash
git add tests/unit/engineering/test_value_report.py tests/unit/cli/test_report.py
git commit -m "test(report): lock breakdown round-outcome + JSON envelope shape"
```

---

## Task 4: human aggregate render (role×source table)

**Files:**
- Modify: `src/super_harness/cli/report.py` (`_render_human`)
- Test: `tests/unit/cli/test_report.py`

**Step 1: Write the failing test**

Human view aggregates rows to one line per role×source (typically ≤4, but never capped — legacy/`unknown` groups render too), sums known tokens (unknown excluded, never shown as 0), sums findings, counts distinct rounds, and flags a group that has any 0-finding round. Assert substrings, not whitespace:

```python
def test_report_human_shows_role_source_breakdown_with_flags(...):
    # seed: plan-reviewer/codex two rounds — round 1 raises 4 findings, round 2 raises 0
    out = ...  # human render text
    assert "review cost breakdown" in out
    assert "review-side" in out and "partial" in out          # caveat present
    assert "plan-reviewer" in out and "codex" in out
    assert "0-finding round" in out                            # zero-finding flag fired
```

Also assert an empty stream prints NO breakdown block (guard: only render when rows exist):

```python
def test_report_human_omits_breakdown_when_no_review_runs(...):
    assert "review cost breakdown" not in out_for_empty_repo
```

**Step 2: Run to verify it fails**

Run: `pytest tests/unit/cli/test_report.py -k "breakdown" -v`
Expected: FAIL — `_render_human` has no breakdown block.

**Step 3: Implement**

Add a helper + splice into `_render_human` before `_bottom_line`. Aggregate purely from `r.cost_breakdown`:

```python
def _fmt_tokens_cell(n: int | None) -> str:
    return "—" if n is None else f"{n:,}"


def _breakdown_lines(r: ValueReport) -> list[str]:
    if not r.cost_breakdown:
        return []
    groups: dict[tuple[str, str], list] = {}
    for row in r.cost_breakdown:
        groups.setdefault((row.role, row.source), []).append(row)
    lines = [
        "",
        "Review cost breakdown (review-side only, self-reported, partial)",
        "  role / source        tokens      findings  rounds",
    ]
    for (role, source), rows in sorted(groups.items()):
        known = [row.tokens for row in rows if row.tokens is not None]
        tokens_cell = _fmt_tokens_cell(sum(known) if known else None)
        findings = sum(row.findings_raised for row in rows)
        rounds = len({row.round_id for row in rows if row.round_id})
        flags = []
        if any(row.findings_raised == 0 for row in rows):
            flags.append("has 0-finding round")
        if any(row.outcome == "rejected" for row in rows):
            flags.append("has rejected round")
        flag = f"  ! {', '.join(flags)}" if flags else ""
        lines.append(
            f"  {role}/{source:<12} {tokens_cell:>10}  {findings:>8}  {rounds:>6}{flag}"
        )
    lines.append("  per-round detail: super-harness --json report .cost_breakdown")
    return lines
```

Insert `lines += _breakdown_lines(r)` in `_render_human` just before the final `_bottom_line(r)` block (after the prevention-layer Note). Keep ASCII `—`? NO — `—` is an em dash (non-ASCII) but the codebase writes utf-8 everywhere (F11-encoding). It is fine in output; if a test asserts it, assert the literal `"—"`.

**Step 4: Run to verify it passes**

Run: `pytest tests/unit/cli/test_report.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/report.py tests/unit/cli/test_report.py
git commit -m "feat(report): render role/source cost breakdown in human view"
```

---

## Task 5: docs — CLI reference + "2.52M" honest reconciliation

**Files:**
- Modify: `docs/cli-reference.md` (report section) — only if it enumerates report output/fields; check first
- Modify: `private/CAPABILITY-CONVERGENCE-LEDGER.md` (+ `.html`) — record the 2.52M verdict (gitignored, in change scope for attestation only)
- Modify auto-memory `project-value-report-stage1` — soften "逼近 252万" to the measured/retired verdict

**Step 1: Write the reconciliation verdict (prose, no test)**

In the ledger's `2026-07-15` slice, record verbatim intent:
> The "old flow burned ~2.52M reviewer tokens" figure (PR #79) was never a controlled measurement — it is retired, not confirmed. The breakdown view now attributes the measured review-side total (review-side only · producer-reported · partial: only runs with a usage receipt contribute; main coding-agent cost is structurally uncaptured). Do NOT cite 2.52M going forward; cite the measured, caveated number.

**Step 2: Regenerate CLI reference if it is generated**

If `docs/cli-reference.md` is produced by `scripts/gen_cli_reference.py`, run the sync path (do NOT hand-edit):

Run: `python scripts/gen_cli_reference.py` (or the documented `sync` command) then `git diff docs/cli-reference.md`.
Expected: report help text unchanged (no new flags) → likely no diff. If no diff, skip.

**Step 3: Commit**

```bash
git add docs/cli-reference.md private/CAPABILITY-CONVERGENCE-LEDGER.md private/CAPABILITY-CONVERGENCE-LEDGER.html
git commit -m "docs(report): retire unverified 2.52M baseline; note measured breakdown"
```

---

## Task 6: full verification + risk-tiered review (dogfood)

**Step 1: Full suite + gates green**

Run: `super-harness verify` (or the repo's `verification.yaml` runner) — expect pytest all-green, ruff/mypy/decision/doc/sync clean.

**Step 2: Risk-tiered review (the signed-off intensity)**

This change is pure-read, additive, no gate/hot-path/security surface → apply matched intensity:
- **plan-reviewer: disclosed skip** — `super-harness review skip plan-reviewer --override` with reason: `"low-risk read-only additive reporting view, no design ambiguity — risk-tiered per OPEN-ITEMS 2026-07-15"`.
- **code-reviewer: run, but stop at 1 round if clean** — honor current governance `min_independent=2` (two independent sources, e.g. Codex `--sandbox read-only` + a Claude subagent verdict); do NOT manufacture extra rounds to hit a 2-pass. Dispose any real finding, re-`prepare`/`begin` a delta round ONLY if a finding lands.

**Step 3: Lifecycle close**

`plan ready <slug> --scope @...` covering all touched files (incl. `private/` for attestation coverage) → code review pass → `attest write` → `done` → PR → `on-merge`.

**Step 4: Refresh ledger `.md`/`.html` + NEXT-SESSION-PROMPT handoff + auto-memory.**

---

## Notes for the executor

- `asdict` on a frozen dataclass with a `tuple[CostBreakdownRow, ...]` field yields a `list[dict]` — JSON works with zero CLI changes. Verify, don't assume.
- Do NOT reuse `derive_review_execution` (review_runs.py): it folds a SINGLE reviewer epoch and would only capture the last epoch on a repo-wide multi-change stream. The report needs a flat per-run pass — that is what `_cost_breakdown` does.
- `findings_raised` counts findings a run RAISED (verdict.findings), a different oracle from the headline `findings_resolved` (dispositions). Keep them separate; do not cross-wire.
- Batch ALL edits before `super-harness done`. Grep for existing tests asserting report output before changing render format.
