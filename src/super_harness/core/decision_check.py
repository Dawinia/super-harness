# src/super_harness/core/decision_check.py
"""Pure dangling check: decisions + anchors → up / down / errors.

Whole-repo invariant. Referential integrity only (design §4): blocks anchors
that name no ratified decision; warns about ratified decisions with no anchor.
``docs/decisions/**`` is ALWAYS excluded from anchor scanning so records never
self-match.
"""
# @decision:d-dangling-check
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from super_harness.core.anchor_scanner import scan_sentinel_locations
from super_harness.core.decisions import RecordError, compute_body_hash, load_decisions
from super_harness.core.source_scope import load_source_scope

ANCHOR_KEYWORD = "@decision:"
ALWAYS_EXCLUDE = ["docs/decisions/**"]


@dataclass
class DanglingUp:
    id: str
    file: str
    line: int


@dataclass
class IntegrityViolation:
    id: str
    file: str


@dataclass
class CheckResult:
    dangling_up: list[DanglingUp]
    dangling_down: list[str]
    errors: list[RecordError]
    integrity_violations: list[IntegrityViolation] = field(default_factory=list)
    unhashed_ratified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.dangling_up and not self.errors and not self.integrity_violations


def run_check(workspace_root: Path) -> CheckResult:
    decisions, errors = load_decisions(workspace_root)
    if errors:
        return CheckResult(dangling_up=[], dangling_down=[], errors=errors)
    ratified = {d.id for d in decisions if d.status == "ratified"}

    integrity_violations: list[IntegrityViolation] = []
    for d in decisions:
        if d.status != "ratified" or d.ratified_text_hash is None:
            continue  # missing hash → lazy-warn path (Task 5), not a violation
        if compute_body_hash(d.body) != d.ratified_text_hash:
            rel = str(d.path.relative_to(workspace_root)) if d.path else d.id
            integrity_violations.append(IntegrityViolation(id=d.id, file=rel))
    integrity_violations.sort(key=lambda v: v.id)

    unhashed_ratified = sorted(
        d.id for d in decisions
        if d.status == "ratified" and d.ratified_text_hash is None
    )

    violated = {v.id for v in integrity_violations}
    effective_ratified = ratified - violated

    include, exclude = load_source_scope(workspace_root)
    locations = scan_sentinel_locations(
        workspace_root,
        file_globs=include,
        keyword=ANCHOR_KEYWORD,
        exclude_globs=exclude + ALWAYS_EXCLUDE,
    )
    anchored_ids = set(locations.keys())

    dangling_up: list[DanglingUp] = []
    for aid, locs in locations.items():
        if aid not in effective_ratified:
            for f, ln in locs:
                dangling_up.append(DanglingUp(id=aid, file=f, line=ln))
    dangling_up.sort(key=lambda d: (d.id, d.file, d.line))

    dangling_down = sorted(ratified - anchored_ids)
    return CheckResult(
        dangling_up=dangling_up,
        dangling_down=dangling_down,
        errors=errors,
        integrity_violations=integrity_violations,
        unhashed_ratified=unhashed_ratified,
    )
