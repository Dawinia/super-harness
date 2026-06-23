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
    if any_fail and not findings:
        raise VerdictError("a checklist item is `fail` but findings is empty")
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


def check_coverage(verdict: dict[str, Any], required_items: list[str]) -> list[str]:
    """Return the required checklist item ids NOT covered by the verdict (in order)."""
    covered = {e["item"] for e in verdict["checklist"]}
    return [i for i in required_items if i not in covered]
