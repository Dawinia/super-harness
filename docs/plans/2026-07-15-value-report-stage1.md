# Value Report (Stage 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `super-harness report` command that rolls up the EXISTING event stream into an honest, plain-language "what did this tool actually do for you" summary for a repo/time-window — **zero new event types**.

**Architecture:** A pure rollup module (`engineering/value_report.py`) reads `events.jsonl` once, keeps the full parsed stream (for append-order causality) AND a window-filtered view, and computes a frozen `ValueReport` dataclass by reusing existing core primitives (verdict parsing, timestamp parsing, decision loading). A thin CLI command (`cli/report.py`) parses flags and renders human / `--brief` / `--json` output. This mirrors the existing `cli/status.py` → `engineering/*` split, so it respects `d-core-is-base` (engineering may import core; core imports neither).

**Tech Stack:** Python 3.10+, `click` (CLI), `pytest` (tests). Reuses `core.review_verdict`, `core.parse_ts`, `core.decisions`, `core.events`, `cli.output.json_envelope`, `cli.errors.format_error`, `exit_codes`.

---

## Metric taxonomy (the locked contract — READ FIRST)

The report has exactly two measurable bands plus one honest footnote. **Design law: every number must be able to show a negative; the report must be willing to say "nothing prevented that we can prove."** Only two features leave a realized-effect trace in today's events — **review** and **bypass audit** — so those are all Stage 1 measures. Everything else is prevention whose success leaves no trace (that gap is Stage 2).

**Band ① — Caught for you (realized effect)**
- `findings_resolved` — distinct code-review finding ids disposed `resolved` (dispositions recorded in the window). **Headline.**
- `findings_open_undisposed` — finding ids raised in the window with NO recorded disposition ANYWHERE in history (neither `resolved` nor `wontfix`). The report label states ONLY the fact ("review raised, no fix or waiver recorded") — it MUST NOT say the user "acknowledged" anything: an open finding may be in-flight or abandoned, so claiming a user action fabricates evidence and breaks the honesty law (CODX-003). Disposition is checked over the FULL stream so a finding resolved just outside the window is not mis-counted as open.
- `undisclosed_bypasses` — `gate_bypassed` events (in the window) NOT covered by a later `gate_bypass_disclosed` on the same change. Disclosure is append-order causal (a disclosure only covers bypasses BEFORE it), so this is computed order-aware over the full stream (CODX-002). **Negative line** (the gate was defeated).

**Band ② — Cost**
- `review_tokens` / `review_runs_total` / `review_runs_with_usage` — review-side tokens summed from `review_result_imported` receipts; honestly labeled "review side only, self-reported, data for X/Y runs, main coding-agent cost not captured."
- `findings_wontfix` — finding ids disposed `wontfix` (review false alarms / rework).
- `nonpassing_rounds` — `review_round_closed` events whose `payload.outcome ∈ {rejected, execution_failed}`. The ONLY closed-round outcomes are `approved`/`rejected`/`execution_failed` (there is NO `failed`); an `execution_failed` round is wasted review effort, so it counts as rework. The imported verdict schema has NO `outcome` field — rejection lives on the round-closed milestone, not the verdict (CODX-001, CODX-005).

**Footnote (context, NOT a value claim)**
- `armed_decisions` — ratified decisions carrying an executable `check` (bite-test). One honest line: the lifecycle gate / N locked rules / verification / doc-sync also stand guard, their successful catches leave no trace yet → Stage 2.

**Explicitly EXCLUDED (activity, not effect):** lifecycle runs, plan rounds, decisions armed-as-a-value, verification pass/fail as "catches," scope-drift (`scope_drift_detected` is a defined-but-never-emitted phantom — do NOT reference it).

**Windowing semantics (state honestly in `--help` and docs):** counts are over EVENTS whose timestamp falls in `[since, until]`. Dispositions/tokens/round-closures/bypasses are counted when their OWN event is in-window; a finding resolved in-window counts even if raised earlier. Disposition-lookups and bypass-disclosure causality use the FULL stream (not just the window) so window boundaries never fabricate an "open" finding or an "undisclosed" bypass. Events with an unparseable timestamp are included only when no window is given; when a window is set they are dropped (cannot be placed). MVP simplification, documented, not hidden.

---

## Self-host lifecycle context (this repo governs itself)

This feature lands as a super-harness change through the repo's OWN lifecycle: `change start` → `plan ready --scope @scope.yaml` → plan review (2 independent sources) → `implementation start` → TDD → `verify` → `done` → code review → merge. Attestation scope MUST list every touched file (see auto-memory `project-self-host-pr-attest-scope`). While in a gated state (INTENT_DECLARED / PLAN_REJECTED / AWAITING_CODE_REVIEW / READY_TO_MERGE) the gate blocks Write/Edit **regardless of path** — plan/scope edits go through Bash (ungated plumbing). Batch ALL edits (impl + docs + ledger) before `done` (坑13 — reopening after code review is a full `plan redeclare` rewind).

**Scope (10 → 11 files):** the source (`engineering/value_report.py`, `cli/report.py`), the registration (`cli/__init__.py`), the tests (`tests/unit/engineering/test_value_report.py`, `tests/unit/cli/test_report.py`), the generated command doc (`docs/cli-reference.md`) AND its generator's exit-code map (`scripts/gen_cli_reference.py`), this plan (`docs/plans/2026-07-15-value-report-stage1.md`), and the three gitignored project-management files (`private/OPEN-ITEMS.md`, `private/CAPABILITY-CONVERGENCE-LEDGER.md`, `private/CAPABILITY-CONVERGENCE-LEDGER.html`).

---

## Task 1: `ValueReport` dataclass + window skeleton (keep full + windowed streams)

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
    f.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
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
    report = build_value_report(events_file, since="2026-07-01", until=None, workspace_root=tmp_path)
    assert report.changes_touched == 2  # c3 (June) excluded
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/engineering/test_value_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'ValueReport'`.

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
    findings_open_undisposed: int
    undisclosed_bypasses: int
    # Band ② cost
    review_tokens: int
    review_runs_total: int
    review_runs_with_usage: int
    findings_wontfix: int
    nonpassing_rounds: int
    # footnote context
    armed_decisions: int


def _read_all_events(events_file: Path) -> list[Event]:
    """Parse every event tolerantly (skip malformed), across all changes, in
    append (file) order — order is load-bearing for bypass-disclosure causality."""
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
    all_events = _read_all_events(events_file)          # full stream (causality)
    windowed = [e for e in all_events if _in_window(e, lo, hi)]
    changes_touched = len({e.change_id for e in windowed})
    return ValueReport(
        since=since,
        until=until,
        changes_touched=changes_touched,
        findings_resolved=0,
        findings_open_undisposed=0,
        undisclosed_bypasses=0,
        review_tokens=0,
        review_runs_total=0,
        review_runs_with_usage=0,
        findings_wontfix=0,
        nonpassing_rounds=0,
        armed_decisions=0,
    )
```

**Step 4: Run test to verify it passes** — `pytest tests/unit/engineering/test_value_report.py -v` → PASS.

**Step 5: Commit**

```bash
git add src/super_harness/engineering/value_report.py tests/unit/engineering/test_value_report.py
git commit -m "feat(report): ValueReport dataclass + full/windowed stream skeleton"
```

---

## Task 2: Findings — resolved / wontfix (windowed) + open-undisposed (full-stream disposition)

**Files:** Modify `value_report.py`; Test in same file.

Code-review verdicts live in `review_result_imported` events with `payload.reviewer == "code-reviewer"` (and the `code_review_failed` milestone). Findings raised = `payload.verdict.findings[].id`; dispositions = `payload.verdict.prior_findings[].disposition ∈ {resolved, wontfix}`.

- `resolved` / `wontfix`: count distinct ids **disposed in the window**.
- `open_undisposed`: ids **raised in the window** whose id is NEVER disposed (`resolved`/`wontfix`) anywhere in the FULL stream (CODX-003 — no user-action claim, no window-boundary fabrication).

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


def test_findings_resolved_wontfix_open(tmp_path):
    events_file = _write_events(tmp_path, [
        _code_verdict_event("e1", "c1", "2026-07-02T00:00:00Z", findings=["F1", "F2", "F3"]),
        _code_verdict_event("e2", "c1", "2026-07-03T00:00:00Z", prior=[("F1", "resolved"), ("F2", "wontfix")]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.findings_resolved == 1          # F1
    assert report.findings_wontfix == 1           # F2
    assert report.findings_open_undisposed == 1   # F3 raised, never disposed


def test_open_finding_not_counted_when_resolved_outside_window(tmp_path):
    # F1 raised inside window; resolved AFTER the window → still not "open"
    events_file = _write_events(tmp_path, [
        _code_verdict_event("e1", "c1", "2026-07-02T00:00:00Z", findings=["F1"]),
        _code_verdict_event("e2", "c1", "2026-07-20T00:00:00Z", prior=[("F1", "resolved")]),
    ])
    report = build_value_report(events_file, since="2026-07-01", until="2026-07-10", workspace_root=tmp_path)
    assert report.findings_open_undisposed == 0   # disposition seen in full stream
    assert report.findings_resolved == 0          # disposition event is out of window
```

**Step 2: Run** → FAIL.

**Step 3: Implement**

```python
def _is_code_verdict(ev: Event) -> bool:
    payload = ev.payload or {}
    return ev.type == "code_review_failed" or (
        ev.type == "review_result_imported" and payload.get("reviewer") == "code-reviewer"
    )


def _dispositions(events: list[Event]) -> tuple[set[str], set[str]]:
    """(resolved_ids, wontfix_ids) disposed by the given events."""
    resolved: set[str] = set()
    wontfix: set[str] = set()
    for ev in events:
        if not _is_code_verdict(ev):
            continue
        verdict = (ev.payload or {}).get("verdict") or {}
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
    return resolved, wontfix


def _raised_ids(events: list[Event]) -> set[str]:
    ids: set[str] = set()
    for ev in events:
        if not _is_code_verdict(ev):
            continue
        for f in ((ev.payload or {}).get("verdict") or {}).get("findings") or []:
            fid = f.get("id") if isinstance(f, dict) else None
            if isinstance(fid, str):
                ids.add(fid)
    return ids


def _finding_counts(windowed: list[Event], all_events: list[Event]) -> tuple[int, int, int]:
    """(resolved, wontfix, open_undisposed).

    resolved/wontfix: disposed in the WINDOW. open_undisposed: raised in the
    window, never disposed anywhere in the FULL stream.
    """
    resolved_w, wontfix_w = _dispositions(windowed)
    disposed_all = set().union(*_dispositions(all_events)) if all_events else set()
    raised_w = _raised_ids(windowed)
    open_undisposed = raised_w - disposed_all
    return len(resolved_w), len(wontfix_w), len(open_undisposed)
```

In `build_value_report`, after computing `windowed`:

```python
    findings_resolved, findings_wontfix, findings_open_undisposed = _finding_counts(windowed, all_events)
```

and pass those into `ValueReport(...)` instead of the zeros.

**Step 4: Run** → PASS.
**Step 5: Commit** — `feat(report): resolved/wontfix (windowed) + open-undisposed (full-stream) findings`

---

## Task 3: Undisclosed bypasses — order-aware over the full stream (the negative line)

**Files:** Modify `value_report.py`; Test in same file.

`gate_bypassed` (payload `{tool, file}`) records the gate defeated; `gate_bypass_disclosed` (payload `{reason}`, emitted at attest time, change-level) discloses. A disclosure only covers bypasses BEFORE it (append order). **Undisclosed** = a `gate_bypassed` in the window with NO `gate_bypass_disclosed` LATER in the full stream on the same change (CODX-002).

**Step 1: Write the failing test**

```python
def _bypass(eid, change, ts, type_="gate_bypassed"):
    import json
    return json.dumps({
        "event_id": eid, "type": type_, "change_id": change, "timestamp": ts,
        "actor": {"type": "sensor", "identifier": "gate"}, "framework": "plain",
        "payload": {"tool": "Write", "file": "x.py"},
    })


def test_undisclosed_bypass_is_order_aware(tmp_path):
    events_file = _write_events(tmp_path, [
        _bypass("e1", "c1", "2026-07-02T00:00:00Z"),                          # c1: undisclosed
        _bypass("e2", "c2", "2026-07-02T00:00:00Z"),                          # c2: bypass...
        _bypass("e3", "c2", "2026-07-02T01:00:00Z", "gate_bypass_disclosed"), # ...disclosed after → covered
        _bypass("e4", "c3", "2026-07-02T00:00:00Z", "gate_bypass_disclosed"), # c3: disclosure first...
        _bypass("e5", "c3", "2026-07-02T02:00:00Z"),                          # ...then a LATER bypass → undisclosed
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.undisclosed_bypasses == 2  # c1 + c3's post-disclosure bypass
```

**Step 2: Run** → FAIL (a set-based version returns 1).

**Step 3: Implement** (iterate the FULL stream in append order; count a windowed bypass as undisclosed when no disclosure follows it on the same change)

```python
def _undisclosed_bypasses(windowed: list[Event], all_events: list[Event]) -> int:
    windowed_ids = {e.event_id for e in windowed}
    # Last append-position of a disclosure, per change (over the full stream).
    last_disclosure: dict[str, int] = {}
    for i, ev in enumerate(all_events):
        if ev.type == "gate_bypass_disclosed":
            last_disclosure[ev.change_id] = i
    count = 0
    for i, ev in enumerate(all_events):
        if ev.type != "gate_bypassed" or ev.event_id not in windowed_ids:
            continue
        if i > last_disclosure.get(ev.change_id, -1):  # no disclosure after this bypass
            count += 1
    return count
```

Wire `undisclosed_bypasses=_undisclosed_bypasses(windowed, all_events)` into the report.

**Step 4: Run** → PASS.
**Step 5: Commit** — `feat(report): order-aware undisclosed-bypass count (negative line)`

---

## Task 4: Review cost — tokens, usage coverage, rejected rounds (from round-closed)

**Files:** Modify `value_report.py`; Test in same file.

Tokens live in each `review_result_imported` receipt: `payload.receipt.usage` (often `None`). **Non-passing rounds live on `review_round_closed.payload.outcome`** — outcomes are `approved`/`rejected`/`execution_failed` (no `failed`); count `rejected` + `execution_failed` since a crashed/stale round is wasted review effort (the imported verdict has no `outcome` field — CODX-001, CODX-005).

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


def _round_closed(eid, change, ts, outcome):
    import json
    return json.dumps({
        "event_id": eid, "type": "review_round_closed", "change_id": change,
        "timestamp": ts, "actor": {"type": "sensor", "identifier": "review"},
        "framework": "plain", "payload": {"outcome": outcome},
    })


def test_review_tokens_usage_and_nonpassing_rounds(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_with_usage("e1", "c1", "2026-07-02T00:00:00Z", {"input_tokens": 100, "output_tokens": 20}),
        _import_with_usage("e2", "c1", "2026-07-02T01:00:00Z", {"total_tokens": 300}),
        _import_with_usage("e3", "c1", "2026-07-02T02:00:00Z", None),   # no usage reported
        _round_closed("e4", "c1", "2026-07-02T03:00:00Z", "rejected"),  # counts
        _round_closed("e5", "c1", "2026-07-02T04:00:00Z", "approved"),  # does NOT
        _round_closed("e6", "c1", "2026-07-02T05:00:00Z", "execution_failed"),  # counts (wasted effort)
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_tokens == 420          # 120 + 300
    assert report.review_runs_total == 3
    assert report.review_runs_with_usage == 2
    assert report.nonpassing_rounds == 2
```

**Step 2: Run** → FAIL.

**Step 3: Implement**

```python
def _usage_tokens(usage: object) -> int | None:
    """Best-effort token total from a producer-reported usage dict. None if absent.

    Prefer an explicit total; else input+output; else None. NEVER guess from
    arbitrary keys — an unknown shape must read as 'not captured', never fabricate.
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


def _review_cost(events: list[Event]) -> tuple[int, int, int]:
    """(tokens, runs_total, runs_with_usage) over review_result_imported events."""
    tokens = runs_total = runs_with_usage = 0
    for ev in events:
        if ev.type != "review_result_imported":
            continue
        runs_total += 1
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
        t = _usage_tokens(receipt.get("usage"))
        if t is not None:
            runs_with_usage += 1
            tokens += t
    return tokens, runs_total, runs_with_usage


def _nonpassing_rounds(events: list[Event]) -> int:
    return sum(
        1 for ev in events
        if ev.type == "review_round_closed"
        and (ev.payload or {}).get("outcome") in {"rejected", "execution_failed"}
    )
```

Wire into the report (all over `windowed`):

```python
    review_tokens, review_runs_total, review_runs_with_usage = _review_cost(windowed)
    nonpassing_rounds = _nonpassing_rounds(windowed)
```

**Step 4: Run** → PASS.
**Step 5: Commit** — `feat(report): review token cost + usage coverage + rejected rounds (round-closed)`

---

## Task 5: Armed-decisions count (footnote context)

**Files:** Modify `value_report.py`; Test in same file.

"Armed / locked rule" = a ratified decision carrying an executable `check` (bite-test). Use `core.decisions.load_decisions(workspace_root)`; count `d.status == "ratified" and d.check is not None`. Best-effort: never let a decisions-config problem crash the report.

> **Confirm during implementation:** the exact `DecisionStatus` literal for ratified (grep `DecisionStatus` in `core/decisions.py`). Adjust the comparison if it differs from `"ratified"`. Match the fixture frontmatter to a real record under `docs/decisions/`.

**Step 1: Write the failing test**

```python
def test_armed_decisions_counts_ratified_with_check(tmp_path):
    dec_dir = tmp_path / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    (dec_dir / "d-example.md").write_text(
        "---\nid: d-example\nstatus: ratified\n---\n\nBody.\n\n```check\ntrue\n```\n",
        encoding="utf-8",
    )
    events_file = tmp_path / "events.jsonl"
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.armed_decisions == 1
```

**Step 2: Run** → FAIL. (If the record format differs, copy a real `docs/decisions/*.md` shape.)

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

**Step 4: Run** → PASS.
**Step 5: Commit** — `feat(report): count armed decisions for honest footnote`

---

## Task 6: CLI command — human rendering + registration

**Files:**
- Create: `src/super_harness/cli/report.py`
- Modify: `src/super_harness/cli/__init__.py` (add `main.add_command(report_cmd)`)
- Test: `tests/unit/cli/test_report.py`

Output is **English** (open-source CLI). Effect-first ordering; the bottom line must be willing to say "nothing prevented that we can prove." The open-findings line states only the fact — no "acknowledged" (CODX-003).

**Step 1: Write the failing test**

```python
# tests/unit/cli/test_report.py
from click.testing import CliRunner

from super_harness.cli import main


def test_report_human_shows_effect_and_bottom_line(tmp_path):
    runner = CliRunner()
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "events.jsonl").write_text("", encoding="utf-8")
    result = runner.invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    assert "what it did for you" in result.output
    assert "nothing" in result.output.lower() or "no measurable" in result.output.lower()
    assert "acknowledged" not in result.output.lower()  # CODX-003: no fabricated user action
```

> **Confirm during implementation:** how `--workspace` / `--json` thread through `ctx.obj` (mirror `cli/status.py:124-133`), and what `.harness` layout `find_harness_root` needs (run `super-harness init` in a tmp dir if the bare file is insufficient).

**Step 2: Run** → FAIL (`report` not a command).

**Step 3: Implement**

```python
# src/super_harness/cli/report.py
"""`report` — roll up the event stream into an honest value summary (Stage 1).

Per docs/plans/2026-07-15-value-report-stage1.md. Reads only existing events;
emits nothing. Mirrors cli/status.py's find-root + json-envelope patterns.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
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
    window = f"{r.since or 'all'} - {r.until or 'now'}"
    lines = [
        "super-harness - what it did for you",
        f"  window: {window} - {r.changes_touched} change(s)",
        "",
        "Caught for you",
        f"  - {r.findings_resolved} problem(s) review found and you fixed",
        f"  - {r.findings_open_undisposed} more review raised that are still open "
        "(no fix or waiver recorded)",
    ]
    if r.undisclosed_bypasses:
        lines.append(f"  - WARNING {r.undisclosed_bypasses} gate bypass(es) went undisclosed "
                     "(the gate was defeated - worth a look)")
    lines += [
        "",
        "Cost",
        f"  - review tokens: {_fmt_tokens(r.review_tokens)} "
        f"(review side only, self-reported; data for {r.review_runs_with_usage}/{r.review_runs_total} runs; "
        f"main coding-agent cost not captured)",
        f"  - review rework: {r.findings_wontfix} false alarm(s) (wontfix), {r.nonpassing_rounds} non-passing round(s) (rejected or failed to execute)",
        "",
        f"  Note: the lifecycle gate, {r.armed_decisions} locked rule(s), verification and doc-sync also",
        "  stand guard in the prevention layer - their successful catches leave no trace yet (see Stage 2).",
        "",
        _bottom_line(r),
    ]
    return "\n".join(lines)


def _render_brief(r: ValueReport) -> str:
    window = f"{r.since or 'all'}-{r.until or 'now'}"
    bits = [f"caught {r.findings_resolved}", f"{_fmt_tokens(r.review_tokens)} review tokens"]
    if r.undisclosed_bypasses:
        bits.append(f"{r.undisclosed_bypasses} undisclosed bypass(es)")
    return f"{window}: " + ", ".join(bits) + "."


@click.command("report")
@click.option("--since", default=None, help="Only count events on/after this ISO date (e.g. 2026-07-01). Unparseable = no lower bound (never errors).")
@click.option("--until", default=None, help="Only count events on/before this ISO date. Unparseable = no upper bound.")
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
        click.echo(json_envelope(command="report", status="pass", exit_code=EXIT_OK, data=asdict(r)))
    elif brief:
        click.echo(_render_brief(r))
    else:
        click.echo(_render_human(r))
    sys.exit(EXIT_OK)
```

In `src/super_harness/cli/__init__.py`, register next to `status_cmd`:

```python
from super_harness.cli.report import report_cmd
# ...
main.add_command(report_cmd)
```

**Step 4: Run** → `pytest tests/unit/cli/test_report.py -v` → PASS.
**Step 5: Commit** — `feat(report): report CLI command + human rendering + registration`

---

## Task 7: `--brief` and `--json` output tests

**Files:** Test-only additions to `tests/unit/cli/test_report.py`.

```python
import json as _json


def _seed(tmp_path, lines):
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "events.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


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

> **Confirm during implementation:** whether `--json` is group-level (`main --json report`) by checking how `status`/`verify` read `ctx.obj["json"]`. Fix invocation order to match.

**Step 2-4:** Should pass if Task 6 wired both; fix minimally if a gap surfaces.
**Step 5: Commit** — `test(report): brief + json envelope coverage`

---

## Task 8: Error handling — no harness, unparseable window

**Files:** Test-only; minimal impl if needed.

```python
def test_report_without_harness_exits_no_config(tmp_path):
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 3  # EXIT_NO_CONFIG


def test_report_bad_since_is_ignored_not_crash(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report", "--since", "not-a-date"])
    assert result.exit_code == 0
```

> **Decision:** `parse_ts` returns `None` for garbage (never raises), so a bad `--since` silently becomes "no lower bound" — least-surprising fail-open, documented in `--help`. Strict rejection would be a deliberate scope change; do NOT add it silently.

**Step 5: Commit** — `test(report): no-harness + tolerant window parsing`

---

## Task 9: Docs (generated) + generator exit-code map + lifecycle close

**Files:**
- Modify: `scripts/gen_cli_reference.py` — add a `"report"` entry to the hand-maintained `_EXIT_CODES` map (near the `"status"` entry, ~line 115) so the generated doc shows the command's real exits, not the generic `0/1` fallback (CODX-004):

  ```python
  "report": [
      "`0` success",
      "`3` no `.harness/` (not initialized)",
  ],
  ```
- Regenerate: `docs/cli-reference.md` by running `python scripts/gen_cli_reference.py` (it introspects the click tree via `_walk`, so registering `report_cmd` + the map entry is all it needs — do NOT hand-edit the generated file).

  > There is NO separate `docs/reference/` command file, and `AGENTS.md` is a hand-authored protocol template that does not enumerate commands — `report` does NOT belong in it, so `sync --agents-md` is NOT required for this change (corrects the earlier draft).
- Update `private/OPEN-ITEMS.md` + `private/CAPABILITY-CONVERGENCE-LEDGER.md` (+ `.html`) per the repo's close ritual.

**Step 1:** Add the `_EXIT_CODES` entry, then `python scripts/gen_cli_reference.py`. Confirm `docs/cli-reference.md` now contains a `report` section with `0`/`3` exits.

**Step 2:** Run the full gate suite locally:

```bash
pytest -q
ruff check .
mypy src/super_harness
super-harness decision check     # confirms d-core-is-base (value_report imports only core)
super-harness doc check
super-harness sync --check
```

Expected: all green. Fix any failure before proceeding.

**Step 3:** Batch-complete the lifecycle (坑13 — do NOT `done` until every edit, incl. docs + ledger, is in):

```bash
super-harness verify value-report-stage1
super-harness done value-report-stage1
# then code review (Codex CLI + Claude), then merge
```

**Step 5: Commit** — `docs(report): cli-reference entry + generator exit-code map`

---

## Task 10: Independent adversarial code review before landing

Per auto-memory `feedback-codex-cross-review` + `feedback-best-change-not-minimal`: run **two independent reviewers** (Codex `codex exec --sandbox read-only` + a Claude reviewer). Give each the taxonomy contract (top of this file) as the acceptance oracle. Reviewers must specifically check:

- **No fabricated metrics** — every number traces to a real event field; `_usage_tokens` returns `None` (not a guess) on unknown shapes; no "acknowledged" user-action claim on open findings (CODX-003).
- **Honesty law holds** — the empty/near-zero window produces a negative bottom line, not silence or spin.
- **No phantom signals** — `scope_drift_detected` is never referenced; `nonpassing_rounds` reads `review_round_closed.payload.outcome` (values `rejected`/`execution_failed`, never a nonexistent `failed`), not the verdict (CODX-001, CODX-005).
- **Order-aware bypass** — a disclosure before a later bypass does NOT clear that bypass (CODX-002).
- **Windowing is honest** — disposition/causality use the full stream; only counting selects on window; unparseable-ts handling matches docs.
- **Architecture** — `value_report.py` imports only `core` (no `cli`/`gates`); `super-harness decision check` green.

Reject → fix → re-approve until both APPROVE. Then land per Task 9.

---

## Out of scope (Stage 2 — do NOT build now)

- Recording gate BLOCKs as durable signals (the hot-path design problem). This report leaves the honest "prevention catches leave no trace" footnote pointing here.
- Team/shared aggregation across repos (events are gitignored/local; per-repo only).
- Running bite-tests live at report time (`report` counts armed decisions from config only; `decision check` owns liveness).
- Distinguishing in-anger gate blocks from self-corrected noise (Stage 2 open question).
