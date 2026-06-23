# src/super_harness/core/review_verdict.py
"""Parse + validate a structured review verdict file (for `review approve/reject`).

Shape (YAML):
    bundle_digest: <str>
    checklist:
      - item: <str>
        status: pass | fail | na
        note: <str, optional>
    findings:               # required non-empty when any checklist item is `fail`
      - id: <str>
        severity: blocker | major | minor
        file: <str>
        summary: <str>
    prior_findings: ...     # slice-2 only; ignored here if present

This module validates SHAPE; the emit-time CLI check (cli/review.py) layers on the
freshness (digest) + coverage gates. Inferential quality is never checked here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from super_harness.core.events import Event, EventSchemaError, parse_event_line

_STATUSES = {"pass", "fail", "na"}
_SEVERITIES = {"blocker", "major", "minor"}
_DISPOSITIONS = {"resolved", "wontfix"}


class VerdictError(ValueError):
    """The verdict file is missing, unparseable, or structurally invalid."""


def parse_verdict_file(path: Path) -> dict[str, Any]:
    """Load + structurally validate a verdict file. Raises `VerdictError`."""
    if not path.is_file():
        raise VerdictError(f"verdict file not found: {path}")
    try:
        parsed: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        raise VerdictError(f"verdict file is not valid YAML: {e}") from e
    if not isinstance(parsed, dict):
        raise VerdictError("verdict file must be a YAML mapping")
    if not isinstance(parsed.get("bundle_digest"), str) or not parsed["bundle_digest"]:
        raise VerdictError("verdict.bundle_digest must be a non-empty string")
    checklist = parsed.get("checklist")
    if not isinstance(checklist, list) or not checklist:
        raise VerdictError("verdict.checklist must be a non-empty list")
    any_fail = False
    for entry in checklist:
        if not isinstance(entry, dict) or not isinstance(entry.get("item"), str):
            raise VerdictError(f"each checklist entry needs a string `item`: {entry!r}")
        status = entry.get("status")
        if status not in _STATUSES:
            raise VerdictError(
                f"checklist[{entry.get('item')!r}].status must be one of {sorted(_STATUSES)}"
            )
        any_fail = any_fail or status == "fail"
    findings = parsed.get("findings") or []
    if not isinstance(findings, list):
        raise VerdictError("verdict.findings must be a list")
    for f in findings:
        if not isinstance(f, dict) or f.get("severity") not in _SEVERITIES:
            raise VerdictError(f"each finding needs severity in {sorted(_SEVERITIES)}: {f!r}")
        if not isinstance(f.get("id"), str) or not f["id"]:
            raise VerdictError(f"each finding needs a non-empty string `id`: {f!r}")
    if any_fail and not findings:
        raise VerdictError("a checklist item is `fail` but findings is empty")
    prior = parsed.get("prior_findings") or []
    if not isinstance(prior, list):
        raise VerdictError("verdict.prior_findings must be a list")
    for pf in prior:
        if not isinstance(pf, dict) or not isinstance(pf.get("id"), str) or not pf["id"]:
            raise VerdictError(f"each prior_finding needs a non-empty string `id`: {pf!r}")
        if pf.get("disposition") not in _DISPOSITIONS:
            raise VerdictError(
                f"prior_finding[{pf['id']!r}].disposition must be one of {sorted(_DISPOSITIONS)}"
            )
        if pf["disposition"] == "wontfix" and not (isinstance(pf.get("note"), str) and pf["note"]):
            raise VerdictError(f"prior_finding[{pf['id']!r}] disposition=wontfix requires a note")
    return parsed


def read_change_events(events_file: Path, change_id: str) -> list[Event]:
    """Read the parsed events for one change, in append order (TOLERANT).

    Mirrors the reducer's read-tolerant policy: malformed lines are skipped, never
    raised — events.jsonl may carry lines from older tool versions or partial
    writes, and an emit-time check that crashes on those would be fail-open.
    Returns an empty list if the file does not exist.
    """
    if not events_file.exists():
        return []
    out: list[Event] = []
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            continue
        if ev.change_id == change_id:
            out.append(ev)
    return out


def derive_open_findings(events: list[Event], change_id: str) -> list[str]:
    """Open-finding ids the next approve must dispose, in append order.

    Walk every `code_review_failed` verdict for the change in append order; per
    verdict dispose its `prior_findings` ids FIRST, then add its `findings` ids
    (discard-then-add → a resolved finding re-listed by a later reject reopens).
    Tolerant: entries with a missing/non-string `id` are skipped (the raw stream
    can carry pre-validation payloads). See design slice-2 §4.D.
    """
    open_ids: dict[str, None] = {}  # ordered set: insertion-order preserved
    for ev in events:
        if ev.change_id != change_id or ev.type != "code_review_failed":
            continue
        verdict = (ev.payload or {}).get("verdict") or {}
        for pf in verdict.get("prior_findings") or []:
            pid = pf.get("id") if isinstance(pf, dict) else None
            if isinstance(pid, str):
                open_ids.pop(pid, None)
        for f in verdict.get("findings") or []:
            fid = f.get("id") if isinstance(f, dict) else None
            if isinstance(fid, str):
                open_ids[fid] = None
    return list(open_ids)


def check_disposed(verdict: dict[str, Any], open_ids: list[str]) -> list[str]:
    """Return the open ids the verdict's `prior_findings` does NOT dispose (in order)."""
    disposed = {
        pf["id"] for pf in (verdict.get("prior_findings") or [])
        if isinstance(pf, dict) and isinstance(pf.get("id"), str)
    }
    return [i for i in open_ids if i not in disposed]


def check_coverage(verdict: dict[str, Any], required_items: list[str]) -> list[str]:
    """Return the required checklist item ids NOT covered by the verdict (in order)."""
    covered = {e["item"] for e in verdict["checklist"]}
    return [i for i in required_items if i not in covered]
