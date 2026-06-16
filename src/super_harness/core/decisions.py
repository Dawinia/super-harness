"""Decision records — the human-ratified unit anchors root in.

One file per decision at ``docs/decisions/<id>.md`` (markdown + YAML
frontmatter). Pure: parse / validate / load / serialize. No CLI, no events.
See docs/plans/2026-06-08-decision-records-anchors-design.md §2 / §4.4.
"""
# @decision:d-decision-records
from __future__ import annotations

import hashlib
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
class Counterexample:
    path: str
    content: str


@dataclass
class Decision:
    id: str
    status: DecisionStatus
    ratified_by: str | None = None
    ratified_at: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    ratified_text_hash: str | None = None
    body: str = ""
    path: Path | None = None
    check: str | None = None
    counterexample: Counterexample | None = None


_FENCE_RE = re.compile(r"^```(?P<info>[^\n]*)\n(?P<inner>.*?)\n```", re.DOTALL | re.MULTILINE)


def _blocks(body: str, kind: str) -> list[re.Match[str]]:
    return [m for m in _FENCE_RE.finditer(body) if m.group("info").split()[:1] == [kind]]


def parse_check(body: str) -> str | None:
    ms = _blocks(body, "check")
    if not ms:
        return None
    if len(ms) > 1:
        raise ValueError("at most one ```check block per decision")
    return ms[0].group("inner").strip()


def parse_counterexample(body: str) -> Counterexample | None:
    ms = _blocks(body, "counterexample")
    if not ms:
        return None
    if len(ms) > 1:
        raise ValueError("at most one ```counterexample block per decision")
    info = ms[0].group("info")
    m = re.search(r"\bpath=(\S+)", info)
    if not m:
        raise ValueError("```counterexample block needs path=<relative-path>")
    return Counterexample(path=m.group(1), content=ms[0].group("inner").strip())


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


def normalize_body(body: str) -> str:
    """Minimal normalization for fingerprinting: line endings, per-line trailing
    whitespace, leading/trailing blank lines. Nothing else (design §3)."""
    unified = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in unified.split("\n")]
    return "\n".join(lines).strip()


def compute_body_hash(body: str) -> str:
    digest = hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


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
        ratified_text_hash=data.get("ratified_text_hash"),
        body=body,
        path=path,
        check=parse_check(body),
        counterexample=parse_counterexample(body),
    )


def load_decisions(workspace_root: Path) -> tuple[list[Decision], list[RecordError]]:
    """Enumerate + validate every record. Fail-closed: malformed/dup → errors."""
    ddir = decisions_dir(workspace_root)
    decisions: list[Decision] = []
    errors: list[RecordError] = []
    if not ddir.is_dir():
        return decisions, errors

    # Build candidate list (same exclusion rules as before).
    candidates = [
        p
        for p in sorted(ddir.glob("*.md"))
        if p.name not in _RESERVED_NAMES and not p.name.startswith(("_", "."))
    ]

    # Group by casefolded stem BEFORE parsing so collisions are detectable
    # even on case-insensitive filesystems where only one of the two files
    # survives the write.
    from collections import defaultdict

    groups: dict[str, list[Path]] = defaultdict(list)
    for p in candidates:
        groups[p.stem.casefold()].append(p)

    # Iterate groups in sorted key order for deterministic output.
    for cf_key in sorted(groups):
        group = groups[cf_key]
        if len(group) > 1:
            first = group[0]
            rel = str(first.relative_to(workspace_root))
            filenames = ", ".join(sorted(p.name for p in group))
            errors.append(
                RecordError(
                    kind="duplicate_id",
                    id=first.stem,
                    file=rel,
                    detail=f"case-folded filename collision: {filenames}",
                )
            )
            continue
        # Singleton group — parse normally.
        p = group[0]
        rel = str(p.relative_to(workspace_root))
        try:
            d = parse_decision_file(p)
        except (ValueError, OSError, yaml.YAMLError) as e:
            errors.append(RecordError(kind="malformed", file=rel, detail=str(e)))
            continue
        decisions.append(d)
    return decisions, errors


def serialize_decision(decision: Decision) -> str:
    fm: dict[str, str] = {"id": decision.id, "status": decision.status}
    for key in ("ratified_by", "ratified_at", "supersedes",
                "superseded_by", "ratified_text_hash"):
        val = getattr(decision, key)
        if val:
            fm[key] = val
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{fm_text}\n---\n{decision.body}\n"


def write_decision(decision: Decision) -> None:
    if decision.path is None:
        raise ValueError("write_decision requires decision.path to be set")
    decision.path.parent.mkdir(parents=True, exist_ok=True)
    decision.path.write_text(serialize_decision(decision), encoding="utf-8")
