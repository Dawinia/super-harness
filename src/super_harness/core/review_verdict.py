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
            raise VerdictError(f"checklist[{entry.get('item')!r}].status must be one of {sorted(_STATUSES)}")
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


def check_coverage(verdict: dict[str, Any], required_items: list[str]) -> list[str]:
    """Return the required checklist item ids NOT covered by the verdict (in order)."""
    covered = {e["item"] for e in verdict["checklist"]}
    return [i for i in required_items if i not in covered]
