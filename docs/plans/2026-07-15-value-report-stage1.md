# Value Report (Stage 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `super-harness report` command that rolls up the EXISTING event stream into an honest, plain-language "what did this tool actually do for you" summary for a repo/time-window — **zero new event types**.

**Architecture:** A pure rollup module (`engineering/value_report.py`) reads `events.jsonl`, filters events to the window, and computes a frozen `ValueReport` dataclass by reusing existing core primitives (verdict parsing, timestamp parsing, decision loading). A thin CLI command (`cli/report.py`) parses flags and renders human / `--brief` / `--json` output. This mirrors the existing `cli/status.py` → `engineering/*` split, so it respects `d-core-is-base` (engineering may import core; core imports neither).

**Tech Stack:** Python 3.10+, `click` (CLI), `pytest` (tests). Reuses `core.review_verdict`, `core.parse_ts`, `core.decisions`, `core.events`, `cli.output.json_envelope`, `cli.errors.format_error`, `exit_codes`.

---

## Metric taxonomy (the locked contract — READ FIRST)

The report has exactly two measurable bands plus one honest footnote. **Design law: every number must be able to show a negative; the report must be willing to say "nothing prevented that we can prove."** Only two features leave a realized-effect trace in today's events — **review** and **bypass audit** — so those are all Stage 1 measures. Everything else is prevention whose success leaves no trace (that gap is Stage 2).

**Band ① — Caught for you (realized effect)**
- `findings_resolved` — distinct code-review finding ids ever disposed `resolved` in the window. **Headline.**
- `findings_acknowledged` — finding ids raised in the window but neither `resolved` nor `wontfix` ("flagged, acknowledged, not fixed"). Soft number, honestly labeled.
- `undisclosed_bypasses` — `gate_bypassed` events on changes with NO `gate_bypass_disclosed` event. **Negative line** (the gate was defeated).

**Band ② — Cost**
- `review_tokens` / `review_runs_total` / `review_runs_with_usage` — review-side tokens summed from `review_result_imported` receipts; honestly labeled "review side only, self-reported, data for X/Y runs, main coding-agent cost not captured."
- `findings_wontfix` — finding ids disposed `wontfix` (review false alarms / rework).
- `rejected_rounds` — code-review verdicts whose `outcome ∈ {rejected, failed}` (review round returned work).

**Footnote (context, NOT a value claim)**
- `armed_decisions` — ratified decisions carrying an executable `check` (bite-test). One honest line: the lifecycle gate / N locked rules / verification / doc-sync also stand guard, their successful catches leave no trace yet → Stage 2.

**Explicitly EXCLUDED (activity, not effect):** lifecycle runs, plan rounds, decisions armed-as-a-value, verification pass/fail as "catches," scope-drift (`scope_drift_detected` is a defined-but-never-emitted phantom — do NOT reference it).

**Windowing semantics (state honestly in `--help` and docs):** the report counts EVENTS whose timestamp falls in `[since, until]`. A finding resolved in-window counts even if raised earlier. Events with an unparseable timestamp are included only when no window is given; when a window is set they are dropped (cannot be placed). This is an MVP simplification, documented, not hidden.

---

## Self-host lifecycle context (this repo governs itself)

This feature lands as a super-harness change through the repo's OWN lifecycle. Before implementation:

```bash
super-harness plan declare value-report-stage1 --intent "Add `report` command rolling up existing events into a value summary"
# ... implement per tasks below ...
# scope must cover EVERY touched file (src + tests + docs). Extract file list to a plain YAML list:
super-harness plan ready value-report-stage1 --scope @<scope.yaml>
super-harness implementation start value-report-stage1
# ... batch ALL edits before `done` (坑13: reopening after review is a full `plan redeclare` rewind) ...
```

Attestation scope MUST list every file this plan creates/modifies (see auto-memory `project-self-host-pr-attest-scope`). Batch all edits (impl + docs + any nits) before `done`.

---

## Task 1: `ValueReport` dataclass + window skeleton

**Files:**
- Create: `src/super_harness/engineering/value_report.py`
- Test: `tests/unit/engineering/test_value_report.py`

**Step 1: Write the failing test**

```python
# tests/unit/engineering/test_value_report.py
from pathlib import Path

from super_harness.engineering.value_report import ValueReport, build_value_report


def _write_events(tmp_path: Path, lines: list[str]) -> Path:
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def test_empty_stream_yields_zeroed_report(tmp_path):
    events_file = tmp_path / "events.jsonl"  # does not exist
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert isinstance(report, ValueReport)
    assert report.changes_touched == 0
    assert report.findings_resolved == 0
    assert report.undisclosed_bypasses == 0
    assert report.review_tokens == 0


def test_changes_touched_counts_distinct_change_ids_in_window(tmp_path):
    events_file = _write_events(tmp_path, [
        '{"event_id":"e1","type":"intent_declared","change_id":"c1","timestamp":"2026-07-01T00:00:00Z","actor":{"type":"human","identifier":"u"},"framework":"plain","payload":{}}',
        '{"event_id":"e2","type":"intent_declared","change_id":"c2","timestamp":"2026-07-10T00:00:00Z","actor":{"type":"human","identifier":"u"},"framework":"plain","payload":{}}',
        '{"event_id":"e3","type":"intent_declared","change_id":"c3","timestamp":"2026-06-01T00:00:00Z","actor":{"type":"human","identifier":"u"},"framework":"plain","payload":{}}',
    ])
    # window excludes c3 (June)
    report = build_value_report(events_file, since="2026-07-01", until=None, workspace_root=tmp_path)
    assert report.changes_touched == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/engineering/test_value_report.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name 'ValueReport'`.

**Step 3: Write minimal implementation**

```python
# src/super_harness/engineering/value_report.py
"""Roll up the existing event stream into an honest value summary (Stage 1).

Zero new event types: this module only READS events.jsonl and reuses core
primitives. Placed in `engineering/` (not `core/`) because it composes review +
decision loaders; `engineering` may import `core`, preserving d-core-is-base.

Metric taxonomy is the locked contract in
docs/plans/2026-07-15-value-report-stage1.md. Only `review` and `bypass audit`
leave a realized-effect trace today; every other guardrail's success is invisible
(that is Stage 2). Design law: every number can show a negative.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from super_harness.core.events import Event, EventSchemaError, parse_event_line
from super_harness.core.parse_ts import parse_ts


@dataclass(frozen=True)
class ValueReport:
    since: str | None
    until: str | None
    changes_touched: int
    # Band ① effect
    findings_resolved: int
    findings_acknowledged: int
    undisclosed_bypasses: int
    # Band ② cost
    review_tokens: int
    review_runs_total: int
    review_runs_with_usage: int
    findings_wontfix: int
    rejected_rounds: int
    # footnote context
    armed_decisions: int


def _read_all_events(events_file: Path) -> list[Event]:
    """Parse every event tolerantly (skip malformed), across all changes."""
    if not events_file.exists():
        return []
    out: list[Event] = []
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(parse_event_line(line))
        except EventSchemaError:
            continue
    return out


def _in_window(ev: Event, lo: datetime | None, hi: datetime | None) -> bool:
    """True if the event's timestamp is within [lo, hi]. With no bounds, always
    true. With a bound set, an unparseable timestamp cannot be placed → excluded.
    """
    if lo is None and hi is None:
        return True
    ts = parse_ts(ev.timestamp)
    if ts is None:
        return False
    if lo is not None and ts < lo:
        return False
    if hi is not None and ts > hi:
        return False
    return True


def build_value_report(
    events_file: Path,
    *,
    since: str | None,
    until: str | None,
    workspace_root: Path,
) -> ValueReport:
    lo = parse_ts(since) if since else None
    hi = parse_ts(until) if until else None
    events = [e for e in _read_all_events(events_file) if _in_window(e, lo, hi)]
    changes_touched = len({e.change_id for e in events})
    return ValueReport(
        since=since,
        until=until,
        changes_touched=changes_touched,
        findings_resolved=0,
        findings_acknowledged=0,
        undisclosed_bypasses=0,
        review_tokens=0,
        review_runs_total=0,
        review_runs_with_usage=0,
        findings_wontfix=0,
        rejected_rounds=0,
        armed_decisions=0,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/engineering/test_value_report.py -v`
Expected: PASS (both tests).

**Step 5: Commit**

```bash
git add src/super_harness/engineering/value_report.py tests/unit/engineering/test_value_report.py
git commit -m "feat(report): ValueReport dataclass + window skeleton"
```

---

## Task 2: Findings extraction (resolved / wontfix / acknowledged)

**Files:**
- Modify: `src/super_harness/engineering/value_report.py`
- Test: `tests/unit/engineering/test_value_report.py`

Reuse the finding-disposition shape from `core/review_verdict.py`: code-review verdicts live in `review_result_imported` events with `payload.reviewer == "code-reviewer"` (and the `code_review_failed` milestone). Findings raised = `payload.verdict.findings[].id`; dispositions = `payload.verdict.prior_findings[].disposition ∈ {resolved, wontfix}`.

**Step 1: Write the failing test**

```python
def _code_verdict_event(eid, change, ts, *, findings=(), prior=()):
    import json
    payload = {
        "reviewer": "code-reviewer",
        "verdict": {
            "findings": [{"id": fid, "severity": "major", "file": "x", "summary": "s"} for fid in findings],
            "prior_findings": [{"id": pid, "disposition": disp, "note": "n"} for pid, disp in prior],
        },
    }
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "codex"},
        "framework": "plain", "payload": payload,
    })


def test_findings_resolved_wontfix_acknowledged(tmp_path):
    events_file = _write_events(tmp_path, [
        # round 1: raise F1, F2, F3
        _code_verdict_event("e1", "c1", "2026-07-02T00:00:00Z", findings=["F1", "F2", "F3"]),
        # round 2: dispose F1 resolved, F2 wontfix; F3 left open (acknowledged)
        _code_verdict_event("e2", "c1", "2026-07-03T00:00:00Z", prior=[("F1", "resolved"), ("F2", "wontfix")]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.findings_resolved == 1     # F1
    assert report.findings_wontfix == 1       # F2
    assert report.findings_acknowledged == 1  # F3 raised, never disposed
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/engineering/test_value_report.py::test_findings_resolved_wontfix_acknowledged -v`
Expected: FAIL — `findings_resolved == 0 != 1`.

**Step 3: Write minimal implementation**

Add a helper and wire it into `build_value_report` (replace the three finding zeros):

```python
def _is_code_verdict(ev: Event) -> bool:
    payload = ev.payload or {}
    return ev.type == "code_review_failed" or (
        ev.type == "review_result_imported" and payload.get("reviewer") == "code-reviewer"
    )


def _finding_counts(events: list[Event]) -> tuple[int, int, int]:
    """(resolved, wontfix, acknowledged) distinct finding-id counts over the window.

    acknowledged = raised − resolved − wontfix (flagged, never disposed).
    """
    raised: set[str] = set()
    resolved: set[str] = set()
    wontfix: set[str] = set()
    for ev in events:
        if not _is_code_verdict(ev):
            continue
        verdict = (ev.payload or {}).get("verdict") or {}
        for f in verdict.get("findings") or []:
            fid = f.get("id") if isinstance(f, dict) else None
            if isinstance(fid, str):
                raised.add(fid)
        for pf in verdict.get("prior_findings") or []:
            if not isinstance(pf, dict):
                continue
            pid, disp = pf.get("id"), pf.get("disposition")
            if not isinstance(pid, str):
                continue
            if disp == "resolved":
                resolved.add(pid)
            elif disp == "wontfix":
                wontfix.add(pid)
    acknowledged = raised - resolved - wontfix
    return len(resolved), len(wontfix), len(acknowledged)
```

In `build_value_report`, after computing `events`:

```python
    findings_resolved, findings_wontfix, findings_acknowledged = _finding_counts(events)
```

and pass those into the `ValueReport(...)` instead of the zeros.

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/engineering/test_value_report.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(report): count resolved/wontfix/acknowledged review findings"
```

---

## Task 3: Undisclosed-bypass extraction (the negative line)

**Files:** Modify `value_report.py`; Test in same file.

A `gate_bypassed` event (payload `{tool, file}`) records the gate being defeated. `gate_bypass_disclosed` (emitted at attest time) records the operator owning up. **Undisclosed** = `gate_bypassed` events belonging to changes that have NO `gate_bypass_disclosed` event in the window.

**Step 1: Write the failing test**

```python
def _bypass(eid, change, ts, type_="gate_bypassed"):
    import json
    return json.dumps({
        "event_id": eid, "type": type_, "change_id": change, "timestamp": ts,
        "actor": {"type": "sensor", "identifier": "gate"}, "framework": "plain",
        "payload": {"tool": "Write", "file": "x.py"},
    })


def test_undisclosed_bypasses_exclude_disclosed_changes(tmp_path):
    events_file = _write_events(tmp_path, [
        _bypass("e1", "c1", "2026-07-02T00:00:00Z"),                      # c1 undisclosed
        _bypass("e2", "c2", "2026-07-02T00:00:00Z"),                      # c2 bypass...
        _bypass("e3", "c2", "2026-07-02T01:00:00Z", "gate_bypass_disclosed"),  # ...disclosed
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.undisclosed_bypasses == 1  # only c1's bypass counts
```

**Step 2: Run** — Expected FAIL (`0 != 1`).

**Step 3: Implement**

```python
def _undisclosed_bypasses(events: list[Event]) -> int:
    disclosed_changes = {
        e.change_id for e in events if e.type == "gate_bypass_disclosed"
    }
    return sum(
        1 for e in events
        if e.type == "gate_bypassed" and e.change_id not in disclosed_changes
    )
```

Wire `undisclosed_bypasses=_undisclosed_bypasses(events)` into the report.

**Step 4: Run** — Expected PASS.
**Step 5: Commit** — `feat(report): count undisclosed gate bypasses (negative line)`

---

## Task 4: Review cost — tokens, usage coverage, rejected rounds

**Files:** Modify `value_report.py`; Test in same file.

Tokens live in each `review_result_imported` receipt: `payload.receipt.usage` (often `None`). Rejected rounds: a verdict with `payload.verdict.outcome ∈ {rejected, failed}` (mirrors `engineering/review_runs.py:87`).

**Step 1: Write the failing test**

```python
def _import_with_usage(eid, change, ts, usage):
    import json
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "codex"},
        "framework": "plain",
        "payload": {"reviewer": "code-reviewer", "receipt": {"usage": usage}, "verdict": {}},
    })


def test_review_tokens_and_usage_coverage(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_with_usage("e1", "c1", "2026-07-02T00:00:00Z", {"input_tokens": 100, "output_tokens": 20}),
        _import_with_usage("e2", "c1", "2026-07-02T01:00:00Z", {"total_tokens": 300}),
        _import_with_usage("e3", "c1", "2026-07-02T02:00:00Z", None),  # no usage reported
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_tokens == 420          # 120 + 300
    assert report.review_runs_total == 3
    assert report.review_runs_with_usage == 2
```

**Step 2: Run** — Expected FAIL.

**Step 3: Implement**

```python
def _usage_tokens(usage: object) -> int | None:
    """Best-effort token total from a producer-reported usage dict. None if absent.

    Prefer an explicit total; else input+output; else None (do NOT guess from
    arbitrary keys — an unknown shape must read as 'not captured', never fabricate).
    """
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    if isinstance(inp, int) or isinstance(out, int):
        return (inp if isinstance(inp, int) else 0) + (out if isinstance(out, int) else 0)
    return None


def _review_cost(events: list[Event]) -> tuple[int, int, int, int]:
    """(tokens, runs_total, runs_with_usage, rejected_rounds)."""
    tokens = runs_total = runs_with_usage = rejected = 0
    for ev in events:
        if ev.type != "review_result_imported":
            continue
        runs_total += 1
        usage = (ev.payload or {}).get("receipt", {}).get("usage") if isinstance(ev.payload, dict) else None
        t = _usage_tokens(usage)
        if t is not None:
            runs_with_usage += 1
            tokens += t
        if ((ev.payload or {}).get("verdict") or {}).get("outcome") in {"rejected", "failed"}:
            rejected += 1
    return tokens, runs_total, runs_with_usage, rejected
```

Wire the four values into the report.

**Step 4: Run** — Expected PASS.
**Step 5: Commit** — `feat(report): review token cost + usage coverage + rejected rounds`

---

## Task 5: Armed-decisions count (footnote context)

**Files:** Modify `value_report.py`; Test in same file.

"Armed / locked rule" = a ratified decision carrying an executable `check` (bite-test). Use `core.decisions.load_decisions(workspace_root)`; count `d.status == "ratified" and d.check is not None`. Best-effort: never let a decisions-config problem crash the report.

> **Confirm during implementation:** the exact `DecisionStatus` literal for ratified (grep `class DecisionStatus` / usages in `core/decisions.py`). Adjust the comparison if it differs from `"ratified"`.

**Step 1: Write the failing test**

```python
def test_armed_decisions_counts_ratified_with_check(tmp_path):
    dec_dir = tmp_path / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    # one ratified decision with a check block (armed)
    (dec_dir / "d-example.md").write_text(
        "---\nid: d-example\nstatus: ratified\n---\n\n"
        "Body.\n\n```check\ntrue\n```\n",
        encoding="utf-8",
    )
    events_file = tmp_path / "events.jsonl"
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.armed_decisions == 1
```

**Step 2: Run** — Expected FAIL (`0 != 1`). (If the decision-record frontmatter/format differs, fix the fixture to match a real record from `docs/decisions/` in this repo.)

**Step 3: Implement**

```python
def _armed_decisions(workspace_root: Path) -> int:
    """Ratified decisions carrying an executable check (bite-test). Best-effort:
    any load error → 0 (the footnote must never crash the report)."""
    try:
        from super_harness.core.decisions import load_decisions
        decisions, _errors = load_decisions(workspace_root)
    except Exception:
        return 0
    return sum(1 for d in decisions if d.status == "ratified" and d.check is not None)
```

Wire `armed_decisions=_armed_decisions(workspace_root)` into the report.

**Step 4: Run** — Expected PASS.
**Step 5: Commit** — `feat(report): count armed decisions for honest footnote`

---

## Task 6: CLI command — human rendering + registration

**Files:**
- Create: `src/super_harness/cli/report.py`
- Modify: `src/super_harness/cli/__init__.py` (add `main.add_command(report_cmd)`)
- Test: `tests/unit/cli/test_report.py`

Output is **English** (open-source CLI). Effect-first ordering; the bottom line must be willing to say "nothing prevented that we can prove."

**Step 1: Write the failing test**

```python
# tests/unit/cli/test_report.py
from click.testing import CliRunner

from super_harness.cli import main


def test_report_human_shows_effect_and_bottom_line(tmp_path, monkeypatch):
    # init a harness workspace
    runner = CliRunner()
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "events.jsonl").write_text("", encoding="utf-8")
    result = runner.invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    assert "what it did for you" in result.output
    # empty stream → honest negative bottom line
    assert "nothing" in result.output.lower() or "no measurable" in result.output.lower()
```

> **Confirm during implementation:** how `--workspace` is threaded (see `cli/__init__.py` group + `ctx.obj`), and how `find_harness_root` locates `.harness` (mirror `cli/status.py:124-133`). Adjust the fixture (`.harness/` layout) to whatever `init` actually creates — run `super-harness init` in a tmp dir once and copy the structure if the bare `.harness/events.jsonl` is insufficient.

**Step 2: Run** — Expected FAIL (`report` not a command).

**Step 3: Implement**

```python
# src/super_harness/cli/report.py
"""`report` — roll up the event stream into an honest value summary (Stage 1).

Per docs/plans/2026-07-15-value-report-stage1.md. Reads only existing events;
emits nothing. Mirrors cli/status.py's find-root + json-envelope patterns.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import HarnessNotInitialized, events_path, find_harness_root
from super_harness.engineering.value_report import ValueReport, build_value_report
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK


def _fmt_tokens(n: int) -> str:
    return f"~{n:,}" if n else "0"


def _bottom_line(r: ValueReport) -> str:
    if r.findings_resolved == 0 and r.undisclosed_bypasses == 0:
        return ("Bottom line: no measurable catches this window — nothing prevented "
                "that we can prove. On this evidence alone it is not earning its keep here.")
    parts = []
    if r.findings_resolved:
        parts.append(f"review earned its keep: {r.findings_resolved} real fix(es)")
    if r.undisclosed_bypasses:
        parts.append(f"{r.undisclosed_bypasses} undisclosed bypass(es) to investigate")
    return "Bottom line: " + "; ".join(parts) + "."


def _render_human(r: ValueReport) -> str:
    window = f"{r.since or 'all'} – {r.until or 'now'}"
    lines = [
        "super-harness — what it did for you",
        f"  window: {window} · {r.changes_touched} change(s)",
        "",
        "Caught for you",
        f"  · {r.findings_resolved} problem(s) review found and you fixed",
        f"  · {r.findings_acknowledged} more review flagged that you acknowledged but did not fix",
    ]
    if r.undisclosed_bypasses:
        lines.append(f"  · ⚠ {r.undisclosed_bypasses} gate bypass(es) went undisclosed (the gate was defeated — worth a look)")
    lines += [
        "",
        "Cost",
        f"  · review tokens: {_fmt_tokens(r.review_tokens)} "
        f"(review side only, self-reported; data for {r.review_runs_with_usage}/{r.review_runs_total} runs; "
        f"main coding-agent cost not captured)",
        f"  · review rework: {r.findings_wontfix} false alarm(s) (wontfix), {r.rejected_rounds} rejected round(s)",
        "",
        f"  Note: the lifecycle gate, {r.armed_decisions} locked rule(s), verification and doc-sync also stand",
        "  guard in the prevention layer — their successful catches leave no trace yet (see Stage 2).",
        "",
        _bottom_line(r),
    ]
    return "\n".join(lines)


def _render_brief(r: ValueReport) -> str:
    window = f"{r.since or 'all'}–{r.until or 'now'}"
    bits = [f"caught {r.findings_resolved}", f"{_fmt_tokens(r.review_tokens)} review tokens"]
    if r.undisclosed_bypasses:
        bits.append(f"{r.undisclosed_bypasses} undisclosed bypass(es)")
    return f"{window}: " + ", ".join(bits) + "."


def _report_data(r: ValueReport) -> dict:
    from dataclasses import asdict
    return asdict(r)


@click.command("report")
@click.option("--since", default=None, help="Only count events on/after this date (ISO 8601, e.g. 2026-07-01).")
@click.option("--until", default=None, help="Only count events on/before this date (ISO 8601).")
@click.option("--brief", is_flag=True, help="One-line summary only.")
@click.pass_context
def report_cmd(ctx: click.Context, since: str | None, until: str | None, brief: bool) -> None:
    """Show what the harness measurably did for you over a repo/time-window."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="report", message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)
    r = build_value_report(events_path(root), since=since, until=until, workspace_root=root)
    if ctx.obj.get("json"):
        click.echo(json_envelope(command="report", status="pass", exit_code=EXIT_OK, data=_report_data(r)))
    elif brief:
        click.echo(_render_brief(r))
    else:
        click.echo(_render_human(r))
    sys.exit(EXIT_OK)
```

In `src/super_harness/cli/__init__.py`, add the import and register alongside the other `add_command` calls (near `status_cmd`):

```python
from super_harness.cli.report import report_cmd
# ...
main.add_command(report_cmd)
```

**Step 4: Run** — `pytest tests/unit/cli/test_report.py -v` — Expected PASS.
**Step 5: Commit** — `feat(report): report CLI command + human rendering + registration`

---

## Task 7: `--brief` and `--json` output tests

**Files:** Test-only additions to `tests/unit/cli/test_report.py`.

**Step 1: Write the failing tests**

```python
import json as _json


def _seed(tmp_path, lines):
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "events.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_report_brief_is_one_line(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report", "--brief"])
    assert result.exit_code == 0
    assert result.output.strip().count("\n") == 0


def test_report_json_envelope_shape(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "report"])
    assert result.exit_code == 0
    env = _json.loads(result.output)
    assert env["command"] == "report"
    assert env["status"] == "pass"
    assert set(env.keys()) == {"command", "version", "status", "exit_code", "data", "errors"}
    assert "findings_resolved" in env["data"]
```

> **Confirm during implementation:** whether `--json` is a group-level flag (`main --json report`) or command-level, by checking how `status`/`verify` receive `ctx.obj["json"]` in `cli/__init__.py`. Adjust invocation order to match.

**Step 2: Run** — Expected FAIL if envelope/brief not wired (should pass if Task 6 done; if the `--json` flag position differs, fix invocation).
**Step 3:** No new impl expected (Task 6 covers it); if a test exposes a gap, fix minimally.
**Step 4: Run** — Expected PASS.
**Step 5: Commit** — `test(report): brief + json envelope coverage`

---

## Task 8: Error handling — no harness, unparseable window

**Files:** Test-only; minimal impl if needed.

**Step 1: Write the failing tests**

```python
def test_report_without_harness_exits_no_config(tmp_path):
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 3  # EXIT_NO_CONFIG
    assert "Error" in result.output or "Error" in (result.stderr or "")


def test_report_bad_since_is_ignored_not_crash(tmp_path):
    # parse_ts never raises; a bad --since parses to None → treated as no lower bound.
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report", "--since", "not-a-date"])
    assert result.exit_code == 0
```

> **Decision:** `parse_ts` returns `None` for garbage (never raises), so a bad `--since` silently becomes "no lower bound". That is the least-surprising fail-open behavior and keeps the report from ever crashing on input. Documented in `--help`. (If you prefer strict rejection with EXIT_VALIDATION, that is a deliberate scope change — do NOT add it silently.)

**Step 2: Run** — Expected PASS if Task 6 handled `HarnessNotInitialized`; adjust if error routing differs.
**Step 5: Commit** — `test(report): no-harness + tolerant window parsing`

---

## Task 9: Docs + AGENTS sync + lifecycle close

**Files:**
- Modify: `docs/reference/` command reference (find where `status`/`verify` are documented — grep `super-harness status`), add a `report` entry.
- Modify: `docs/` narrative if there's a commands overview (grep for the command list).
- Regenerate `AGENTS.md` via `super-harness sync --agents-md` (NOT `doc check --fix` — see auto-memory `reference-agents-md-regen-via-sync`).
- Update `private/OPEN-ITEMS.md` + `private/CAPABILITY-CONVERGENCE-LEDGER.md` (+ `.html`) per the repo's close ritual.

**Step 1:** Add the `report` command doc entry mirroring the `status` entry's format. State: purpose, flags (`--since/--until/--brief/--json`), windowing semantics, and the honesty framing (measures review + bypasses only; prevention value is invisible → Stage 2).

**Step 2:** Run `super-harness sync --agents-md` then `super-harness sync --check` to confirm AGENTS.md is in sync.

**Step 3:** Run the full gate suite locally:

```bash
pytest -q
ruff check .
mypy src/super_harness
super-harness decision check
super-harness doc check
super-harness sync --check
```

Expected: all green. Fix any failures before proceeding.

**Step 4:** Batch-complete the lifecycle (see 坑13 — do NOT `done` until every edit, incl. docs + ledger, is in):

```bash
super-harness verify value-report-stage1
super-harness done value-report-stage1
# then plan ready --scope covering all files, review (Codex + Claude cross-review), merge
```

**Step 5: Commit** — `docs(report): command reference + AGENTS sync for report`

---

## Task 10: Independent adversarial code review before landing

Per auto-memory `feedback-codex-cross-review` + `feedback-best-change-not-minimal`: before merge, run **two independent reviewers** (Codex `codex exec --sandbox read-only` + a Claude subagent code-reviewer). Give each a self-contained brief with the taxonomy contract (this file's top section) as the acceptance oracle. Reviewers must check specifically:

- **No fabricated metrics** — every number traces to a real event field; `_usage_tokens` returns `None` (not a guess) on unknown shapes.
- **Honesty law holds** — the empty/near-zero window produces a negative bottom line, not silence or spin.
- **No phantom signals** — `scope_drift_detected` is never referenced.
- **Windowing is honest** — documented event-timestamp semantics; unparseable-ts handling matches the doc.
- **Architecture** — `value_report.py` imports only `core` (no `cli`/`gates`), preserving `d-core-is-base`; run `super-harness decision check`.

Reject → fix → re-approve until both APPROVE. Then land per Task 9.

---

## Out of scope (Stage 2 — do NOT build now)

- Recording gate BLOCKs as durable signals (the hot-path design problem). This report leaves the honest "prevention catches leave no trace" footnote pointing here.
- Team/shared aggregation across repos (events are gitignored/local; per-repo only).
- Running bite-tests live at report time (`report` counts armed decisions from config only; `decision check` owns liveness).
- Distinguishing in-anger gate blocks from self-corrected noise (Stage 2 open question).
