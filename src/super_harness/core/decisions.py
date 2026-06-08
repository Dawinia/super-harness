"""Decision records — the human-ratified unit anchors root in.

One file per decision at ``docs/decisions/<id>.md`` (markdown + YAML
frontmatter). Pure: parse / validate / load / serialize. No CLI, no events.
See docs/plans/2026-06-08-decision-records-anchors-design.md §2 / §4.4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from super_harness.core.frontmatter import split_frontmatter

DecisionStatus = Literal["proposed", "ratified", "superseded", "retired"]
_VALID_STATUSES = frozenset({"proposed", "ratified", "superseded", "retired"})
_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_RESERVED_NAMES = frozenset({"README.md"})


@dataclass
class Decision:
    id: str
    status: DecisionStatus
    ratified_by: str | None = None
    ratified_at: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    body: str = ""
    path: Path | None = None


@dataclass
class RecordError:
    kind: Literal["duplicate_id", "malformed"]
    file: str
    detail: str
    id: str | None = None


def decisions_dir(workspace_root: Path) -> Path:
    return workspace_root / "docs" / "decisions"


def is_valid_id(candidate: str) -> bool:
    return bool(_ID_RE.match(candidate))


def parse_decision_file(path: Path) -> Decision:
    """Parse one record. Raises ValueError if malformed (§4.4 predicate)."""
    parsed = split_frontmatter(path.read_text(encoding="utf-8"))
    if parsed is None:
        raise ValueError("missing or malformed frontmatter")
    data, body = parsed
    did = data.get("id")
    if not isinstance(did, str) or not _ID_RE.match(did):
        raise ValueError(f"missing or invalid id (must match {_ID_RE.pattern})")
    if path.stem != did:
        raise ValueError(f"filename stem {path.stem!r} != id {did!r}")
    status = data.get("status")
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    return Decision(
        id=did,
        status=status,
        ratified_by=data.get("ratified_by"),
        ratified_at=data.get("ratified_at"),
        supersedes=data.get("supersedes"),
        superseded_by=data.get("superseded_by"),
        body=body,
        path=path,
    )


def load_decisions(workspace_root: Path) -> tuple[list[Decision], list[RecordError]]:
    """Enumerate + validate every record. Fail-closed: malformed/dup → errors."""
    ddir = decisions_dir(workspace_root)
    decisions: list[Decision] = []
    errors: list[RecordError] = []
    if not ddir.is_dir():
        return decisions, errors
    seen: dict[str, str] = {}
    for p in sorted(ddir.glob("*.md")):
        if p.name in _RESERVED_NAMES or p.name.startswith(("_", ".")):
            continue
        rel = str(p.relative_to(workspace_root))
        try:
            d = parse_decision_file(p)
        except (ValueError, OSError, yaml.YAMLError) as e:
            errors.append(RecordError(kind="malformed", file=rel, detail=str(e)))
            continue
        cf = d.id.casefold()
        if cf in seen:
            errors.append(
                RecordError(
                    kind="duplicate_id",
                    id=d.id,
                    file=rel,
                    detail=f"duplicate (case-folded) of {seen[cf]!r}",
                )
            )
            continue
        seen[cf] = d.id
        decisions.append(d)
    return decisions, errors


def serialize_decision(decision: Decision) -> str:
    fm: dict[str, str] = {"id": decision.id, "status": decision.status}
    for key in ("ratified_by", "ratified_at", "supersedes", "superseded_by"):
        val = getattr(decision, key)
        if val:
            fm[key] = val
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{fm_text}\n---\n{decision.body}\n"


def write_decision(decision: Decision) -> None:
    assert decision.path is not None
    decision.path.parent.mkdir(parents=True, exist_ok=True)
    decision.path.write_text(serialize_decision(decision), encoding="utf-8")
