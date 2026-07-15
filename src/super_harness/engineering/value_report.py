"""Roll up the existing event stream into an honest value summary (Stage 1).

Zero new event types: this module only READS events.jsonl and reuses core
primitives. Placed in ``engineering/`` (not ``core/``) because it composes review
+ decision loaders; ``engineering`` may import ``core``, preserving d-core-is-base.

Metric taxonomy is the locked contract in
docs/plans/2026-07-15-value-report-stage1.md. Only ``review`` and ``bypass audit``
leave a realized-effect trace today; every other guardrail's success is invisible
(that is Stage 2). Design law: every number can show a negative; never fabricate.
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
    # Band 1 - realized effect
    findings_resolved: int
    findings_open_undisposed: int
    undisclosed_bypasses: int
    # Band 2 - cost
    review_tokens: int
    review_runs_total: int
    review_runs_with_usage: int
    findings_wontfix: int
    rejected_rounds: int
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
    true. With a bound set, an unparseable timestamp cannot be placed -> excluded.
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
    window, never disposed anywhere in the FULL stream (no user-action claim, no
    window-boundary fabrication).
    """
    resolved_w, wontfix_w = _dispositions(windowed)
    if all_events:
        resolved_all, wontfix_all = _dispositions(all_events)
        disposed_all = resolved_all | wontfix_all
    else:
        disposed_all = set()
    raised_w = _raised_ids(windowed)
    open_undisposed = raised_w - disposed_all
    return len(resolved_w), len(wontfix_w), len(open_undisposed)


def _undisclosed_bypasses(windowed: list[Event], all_events: list[Event]) -> int:
    """Order-aware count: a `gate_bypassed` in the window is undisclosed when no
    `gate_bypass_disclosed` follows it on the same change (disclosure only covers
    bypasses BEFORE it)."""
    windowed_ids = {e.event_id for e in windowed}
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


def _rejected_rounds(events: list[Event]) -> int:
    """Count ONLY `review_round_closed` closures the reviewer rejected. Excludes
    `execution_failed` — that outcome conflates infra-stale / incomplete-governance
    / the normal human-quorum-pending path, so counting it would fabricate rework.
    """
    return sum(
        1 for ev in events
        if ev.type == "review_round_closed"
        and (ev.payload or {}).get("outcome") == "rejected"
    )


def _armed_decisions(workspace_root: Path) -> int:
    """Ratified decisions carrying an executable check (bite-test). Best-effort:
    any load error -> 0 (the footnote must never crash the report)."""
    try:
        from super_harness.core.decisions import load_decisions
        decisions, _errors = load_decisions(workspace_root)
    except Exception:
        return 0
    return sum(1 for d in decisions if d.status == "ratified" and d.check is not None)


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
    findings_resolved, findings_wontfix, findings_open_undisposed = _finding_counts(
        windowed, all_events
    )
    review_tokens, review_runs_total, review_runs_with_usage = _review_cost(windowed)
    return ValueReport(
        since=since,
        until=until,
        changes_touched=len({e.change_id for e in windowed}),
        findings_resolved=findings_resolved,
        findings_open_undisposed=findings_open_undisposed,
        undisclosed_bypasses=_undisclosed_bypasses(windowed, all_events),
        review_tokens=review_tokens,
        review_runs_total=review_runs_total,
        review_runs_with_usage=review_runs_with_usage,
        findings_wontfix=findings_wontfix,
        rejected_rounds=_rejected_rounds(windowed),
        armed_decisions=_armed_decisions(workspace_root),
    )
