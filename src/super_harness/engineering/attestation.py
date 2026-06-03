# L1 anchor (HG-DF C) — @capability:capability-merge-gate
"""Layer-2 CI merge gate (HG-DF item C) — attestation write + verify logic.

Pure-function domain layer. The CLI (`cli/attest.py`) owns the git/filesystem
boundary and calls these. Reuses `find_ordering_violations` + `derive_state`
unchanged. See docs/plans/2026-06-03-layer2-merge-gate-design.md.

What this proves (per design §2): for every changed file in a PR, there EXISTS
a committed attestation declaring that file in scope, encoding a complete,
correctly-ordered lifecycle reaching READY_TO_MERGE incl. a genuine
`code_review_passed`. It does NOT prove the file was edited through the gated
path (an actor who bypasses the editor but also runs a trivial covering
lifecycle passes — deferred forgery-resistance, HG-12/B).
"""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.reducer import derive_state

ATTESTATIONS_DIRNAME = ".harness/attestations"
MILESTONE_EVENTS: frozenset[str] = frozenset(
    {"plan_approved", "implementation_complete", "code_review_passed"}
)
REQUIRED_STATE = "READY_TO_MERGE"


def canonical_path(raw: str) -> str:
    """Normalize to repo-root-relative POSIX form (forward slashes, no leading
    './', collapsed '..'/'.').

    Applied to BOTH git-diff output and stored ``scope.files`` so set membership
    is spelling-independent (``./src/x`` == ``src/x``).
    """
    p = raw.replace("\\", "/").strip()
    p = posixpath.normpath(p)
    return "" if p == "." else p


# --------------------------------------------------------------------------- #
# git --name-status parsing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DiffEntry:
    """One ``git diff --name-status`` row.

    ``paths`` is a 1-tuple for A/M/D and a 2-tuple ``(old, new)`` for
    renames/copies (status ``R<score>`` / ``C<score>``).
    """

    status: str
    paths: tuple[str, ...]


def parse_name_status(raw: str) -> list[DiffEntry]:
    """Parse ``git diff --name-status`` output into ``DiffEntry`` list.

    Each non-blank line is tab-separated: ``STATUS<TAB>PATH`` (A/M/D) or
    ``R<score><TAB>OLD<TAB>NEW`` (rename/copy — two path columns). Paths are
    canonicalized; blank lines and status-only lines are skipped.
    """
    entries: list[DiffEntry] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        paths = tuple(canonical_path(p) for p in parts[1:] if p.strip())
        if not status or not paths:
            continue
        entries.append(DiffEntry(status=status, paths=paths))
    return entries


# --------------------------------------------------------------------------- #
# Attestation extraction + write
# --------------------------------------------------------------------------- #
def extract_change_events(events_file: Path, slug: str) -> list[str]:
    """Return verbatim events.jsonl lines whose ``change_id == slug``, in append
    order.

    Raises ``ValueError`` if the file is missing or no line matches — a
    silent-empty attestation would be a useless / misleading artifact.
    """
    if not events_file.exists():
        raise ValueError(f"events file not found: {events_file}")
    out: list[str] = []
    for raw in events_file.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("change_id") == slug:
            out.append(raw)
    if not out:
        raise ValueError(f"no events for change {slug!r} in {events_file}")
    return out


def write_attestation(events_file: Path, attestations_dir: Path, slug: str) -> Path:
    """Snapshot the per-change event slice to ``<attestations_dir>/<slug>.jsonl``
    (idempotent overwrite)."""
    lines = extract_change_events(events_file, slug)
    attestations_dir.mkdir(parents=True, exist_ok=True)
    out_path = attestations_dir / f"{slug}.jsonl"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def check_attestation(attestation_path: Path, slug: str) -> list[str]:
    """Return blocker strings for one attestation file (empty list = OK).

    The filename↔content binding is checked FIRST (and short-circuits) so a slug
    mismatch FAILs cleanly rather than ``KeyError``-ing on the state lookup.
    Reuses ``derive_state`` + ``find_ordering_violations`` unchanged.
    """
    blockers: list[str] = []
    states = derive_state(attestation_path)
    if set(states.keys()) != {slug}:
        blockers.append(
            f"attestation {attestation_path.name}: filename slug {slug!r} does "
            f"not match contained change_id(s) {sorted(states.keys())}"
        )
        return blockers
    violations = find_ordering_violations(attestation_path, slug)
    if violations:
        blockers.append(
            f"attestation {slug}: lifecycle ordering invalid "
            f"({len(violations)} violation(s); first: {violations[0].reason})"
        )
    cs = states[slug]
    if cs.current_state != REQUIRED_STATE:
        blockers.append(
            f"attestation {slug}: state is {cs.current_state}, not {REQUIRED_STATE}"
        )
    missing = sorted(MILESTONE_EVENTS - set(cs.event_counts.keys()))
    if missing:
        blockers.append(f"attestation {slug}: missing milestone event(s) {missing}")
    return blockers


@dataclass
class AttestationVerdict:
    ok: bool
    blockers: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    attestations: list[str] = field(default_factory=list)


def _is_attestation_path(canonical: str) -> bool:
    return canonical.startswith(ATTESTATIONS_DIRNAME + "/")


def verify_attestations(root: Path, diff_entries: list[DiffEntry]) -> AttestationVerdict:
    """Core merge-gate verdict (design §4.2). Fail-closed: any blocker → not ok.

    A subject file (every changed path that is NOT under the attestations dir)
    passes only if it is in the ``scope.files`` of a complete, ordered,
    READY_TO_MERGE attestation that is newly ADDED in this diff and covers at
    least one subject of this diff.
    """
    blockers: list[str] = []
    subjects: set[str] = set()
    attestation_entries: list[DiffEntry] = []

    for e in diff_entries:
        if any(_is_attestation_path(p) for p in e.paths):
            attestation_entries.append(e)
        for p in e.paths:
            if not _is_attestation_path(p):
                subjects.add(p)

    # Attestation files must be ADD-only (closes the "edit a trusted attestation
    # to fabricate" vector). Collect the slugs of newly-added attestations.
    added_slugs: list[str] = []
    for e in attestation_entries:
        if e.status != "A":
            blockers.append(
                f"attestation file changed with status {e.status!r} (only "
                f"newly-ADDED attestations are allowed): {list(e.paths)}"
            )
            continue
        for p in e.paths:
            if _is_attestation_path(p) and p.endswith(".jsonl"):
                added_slugs.append(posixpath.basename(p)[: -len(".jsonl")])

    covered: set[str] = set()
    validated: list[str] = []
    for slug in added_slugs:
        att_path = root / ATTESTATIONS_DIRNAME / f"{slug}.jsonl"
        if not att_path.exists():
            blockers.append(f"attestation file for {slug!r} not found at head")
            continue
        att_blockers = check_attestation(att_path, slug)
        if att_blockers:
            blockers.extend(att_blockers)
            continue
        cs = derive_state(att_path)[slug]
        this_covered = {canonical_path(f) for f in cs.scope.get("files", [])}
        if not (this_covered & subjects):
            blockers.append(
                f"attestation {slug}: its scope covers no file in this diff "
                "(stale or forward-planted)"
            )
            continue
        covered |= this_covered
        validated.append(slug)

    for f in sorted(subjects - covered):
        blockers.append(f"changed file not covered by any complete lifecycle: {f}")

    return AttestationVerdict(
        ok=not blockers,
        blockers=blockers,
        subjects=sorted(subjects),
        covered=sorted(covered),
        attestations=validated,
    )
