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
from typing import Any

from super_harness.core.approval import (
    ApprovalError,
    evidence_digest,
    evidence_is_recognized,
    has_unresolved_candidate,
    load_recognition,
    make_code_subject,
    missing_coverage,
    validate_approval,
    validate_code_subject,
    validate_evidence,
)
from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.events import Event, EventSchemaError, parse_event_line
from super_harness.core.reducer import derive_state
from super_harness.core.scope_match import GitScopeError

ATTESTATIONS_DIRNAME = ".harness/attestations"
PLACEHOLDER_IDENTITY = "cli"
MILESTONE_EVENTS: frozenset[str] = frozenset(
    {"plan_approved", "implementation_complete", "code_review_passed"}
)
REQUIRED_STATE = "READY_TO_MERGE"


def canonical_path(raw: str) -> str:
    """Normalize to POSIX form: forward slashes, no leading './', collapsed
    intra-path '.'/'..' segments (via ``posixpath.normpath``).

    Applied to BOTH git-diff output and stored ``scope.files`` so set membership
    is spelling-independent (``./src/x`` == ``src/x``). Note: a leading ``..`` or
    an absolute path is preserved as-is (``normpath`` cannot resolve it without a
    base) — git never emits such paths for tracked files, and a non-canonical
    scope entry that fails to match simply fails closed (uncovered → FAIL).
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
        blockers.append(f"attestation {slug}: state is {cs.current_state}, not {REQUIRED_STATE}")
    missing = sorted(MILESTONE_EVENTS - set(cs.event_counts.keys()))
    if missing:
        blockers.append(f"attestation {slug}: missing milestone event(s) {missing}")
    events: list[Event] = []
    for raw in attestation_path.read_text(encoding="utf-8").splitlines():
        try:
            event = parse_event_line(raw)
        except EventSchemaError:
            continue
        if event.change_id == slug:
            events.append(event)
    blockers.extend(_review_receipt_blockers(events, slug))
    return blockers


def _review_receipt_blockers(events: list[Event], slug: str) -> list[str]:
    """Require imported receipts once a stream opts into the new run protocol."""

    if not any(event.type == "review_round_started" for event in events):
        return []  # Historical lifecycle events remain readable and verifiable.
    milestone = next(
        (event for event in reversed(events) if event.type == "code_review_passed"),
        None,
    )
    if milestone is None or milestone.payload.get("skipped") is True:
        return []
    receipt_ids = milestone.payload.get("receipt_ids")
    if (
        not isinstance(receipt_ids, list)
        or not receipt_ids
        or any(not isinstance(receipt_id, str) or not receipt_id for receipt_id in receipt_ids)
    ):
        return [f"attestation {slug}: protocol code review approval has no imported receipt_ids"]
    imported: dict[str, str] = {}
    for event in events:
        if event.type != "review_result_imported":
            continue
        receipt = event.payload.get("receipt")
        if not isinstance(receipt, dict):
            continue
        receipt_id = receipt.get("receipt_id")
        source = receipt.get("source")
        if isinstance(receipt_id, str) and isinstance(source, str):
            imported[receipt_id] = source
    missing_receipts = [receipt_id for receipt_id in receipt_ids if receipt_id not in imported]
    if missing_receipts:
        return [
            f"attestation {slug}: code review references missing imported receipt(s) "
            f"{missing_receipts}"
        ]
    expected_sources = milestone.payload.get("independent_sources")
    if isinstance(expected_sources, list) and set(expected_sources) != {
        imported[receipt_id] for receipt_id in receipt_ids
    }:
        return [f"attestation {slug}: imported receipt sources do not match independent_sources"]
    return []


@dataclass
class AttestationVerdict:
    ok: bool
    blockers: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    attestations: list[str] = field(default_factory=list)


def _is_attestation_path(canonical: str) -> bool:
    # Only actual attestation EVIDENCE (.jsonl) is exempt from the subject set.
    # A non-.jsonl file committed under the dir (e.g. .harness/attestations/x.py)
    # must stay a subject requiring coverage — otherwise it would be neither
    # subject nor evidence and escape the gate entirely (fail-OPEN, B1 / §5).
    return canonical.startswith(ATTESTATIONS_DIRNAME + "/") and canonical.endswith(".jsonl")


def verify_attestations(
    root: Path,
    diff_entries: list[DiffEntry],
    *,
    base: str | None = None,
    head: str | None = None,
) -> AttestationVerdict:
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
        if cs.effective_approval is not None:
            blockers.extend(
                _new_contract_blockers(root, cs, slug, diff_entries, base=base, head=head)
            )
        this_covered = {canonical_path(f) for f in cs.scope.get("files", [])}
        if not (this_covered & subjects):
            blockers.append(
                f"attestation {slug}: its scope covers no file in this diff "
                "(stale or forward-planted)"
            )
            continue
        covered |= this_covered
        validated.append(slug)
        disclosure = independence_for_attestation(att_path)
        # Both roles, same bar. Plan review joined on the cut that made a plan-reviewer
        # skip reachable after a rejection; leaving it off would have shipped the escape
        # hatch and the missing signal together.
        for role, label in (("code_review", "code review"), ("plan_review", "plan review")):
            row = disclosure[role]
            if row["skipped"] and not row["override"]:
                blockers.append(
                    f"attestation {slug}: {label} was skipped without --override "
                    "(a deliberate `review skip --override --reason ...` is required to merge)"
                )
        gb = gate_bypass_for_attestation(att_path)
        if gb["undisclosed"] > 0:
            blockers.append(
                f"attestation {slug}: the gate was bypassed {gb['undisclosed']} time(s) "
                f"during this change without disclosure (a deliberate "
                f'`attest write {slug} --disclose-gate-bypass "<reason>"` is required to merge)'
            )

    for f in sorted(subjects - covered):
        blockers.append(f"changed file not covered by any complete lifecycle: {f}")

    return AttestationVerdict(
        ok=not blockers,
        blockers=blockers,
        subjects=sorted(subjects),
        covered=sorted(covered),
        attestations=validated,
    )


def _new_contract_blockers(
    root: Path,
    state: Any,
    slug: str,
    diff_entries: list[DiffEntry],
    *,
    base: str | None,
    head: str | None,
) -> list[str]:
    """Check authority and subjects for a new-contract attestation."""
    blockers: list[str] = []
    try:
        approval = validate_approval(state.effective_approval, change_id=slug)
    except ApprovalError as exc:
        return [f"attestation {slug}: invalid effective plan approval: {exc}"]

    try:
        # Merge verification must consult the policy from the trusted base.
        # Reading the candidate's policy here would let the same PR widen the
        # process that is supposed to recognize its own evidence.
        recognition = (
            load_recognition(root, ref=base) if base is not None else load_recognition(root)
        )
    except ApprovalError as exc:
        return [f"attestation {slug}: review recognition is not usable: {exc}"]

    evidence_by_id = {
        item.get("evidence_id"): item
        for item in state.evidence_references
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    }
    plan_evidence = evidence_by_id.get(approval.get("evidence_id"))
    if not isinstance(plan_evidence, dict):
        blockers.append(f"attestation {slug}: effective approval evidence is not retained")
    else:
        try:
            validate_evidence(plan_evidence, change_id=slug)
        except ApprovalError:
            plan_valid = False
        else:
            plan_valid = True
        if not (
            plan_valid
            and plan_evidence.get("kind") == "plan"
            and plan_evidence.get("decision") == "approve"
            and plan_evidence.get("subject_id") == approval.get("subject_id")
            and evidence_digest(plan_evidence) == approval.get("evidence_digest")
            and evidence_is_recognized(plan_evidence, recognition)
        ):
            blockers.append(
                f"attestation {slug}: effective approval is not backed by recognized plan evidence"
            )
        provenance = plan_evidence.get("provenance")
        if recognition.policy_digest and (
            not isinstance(provenance, dict)
            or provenance.get("policy_digest") != recognition.policy_digest
        ):
            blockers.append(
                f"attestation {slug}: plan evidence is bound to another recognition policy"
            )

    if has_unresolved_candidate(state):
        blockers.append(f"attestation {slug}: unresolved plan revision candidate")
    code_subject = state.current_code_subject
    if not isinstance(code_subject, dict):
        blockers.append(f"attestation {slug}: missing current code subject")
        return blockers
    try:
        validated_code = validate_code_subject(code_subject, change_id=slug)
        expected = make_code_subject(
            root,
            change_id=slug,
            approval_id=str(approval["approval_id"]),
            base=base or str(validated_code["base"]),
            head=head or str(validated_code["head"]),
        )
    except (ApprovalError, GitScopeError, KeyError) as exc:
        blockers.append(f"attestation {slug}: cannot recompute code subject: {exc}")
    else:
        if expected["subject_id"] != validated_code.get("subject_id"):
            blockers.append(
                f"attestation {slug}: code subject does not match the complete Git change set"
            )
        actual_paths = {
            path for entry in diff_entries for path in entry.paths if not _is_attestation_path(path)
        }
        subject_paths = set()
        for item in validated_code.get("manifest", []):
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("path"), str):
                subject_paths.add(item["path"])
            if isinstance(item.get("old_path"), str):
                subject_paths.add(item["old_path"])
        if actual_paths != subject_paths:
            blockers.append(
                f"attestation {slug}: code subject manifest does not cover the exact PR paths"
            )
    verification = state.current_verification
    if not isinstance(verification, dict):
        blockers.append(f"attestation {slug}: missing verification subject")
    elif verification.get("subject_id") != code_subject.get("subject_id"):
        blockers.append(f"attestation {slug}: verification is for a different code subject")
    elif verification.get("outcome") != "passed":
        blockers.append(f"attestation {slug}: current verification did not pass")

    attestation_events = events_from_stateful_attestation(root, slug)
    code_review = next(
        (event for event in reversed(attestation_events) if event.type == "code_review_passed"),
        None,
    )
    if code_review is None:
        blockers.append(f"attestation {slug}: missing code review pass event")
    else:
        evidence_id = code_review.payload.get("evidence_id")
        code_evidence = evidence_by_id.get(evidence_id)
        if not isinstance(code_evidence, dict):
            blockers.append(f"attestation {slug}: code review evidence is not retained")
        else:
            try:
                validate_evidence(code_evidence, change_id=slug)
            except ApprovalError:
                code_valid = False
            else:
                code_valid = True
            if not (
                code_valid
                and code_evidence.get("kind") == "code"
                and code_evidence.get("decision") == "approve"
                and code_evidence.get("subject_id") == code_subject.get("subject_id")
                and evidence_is_recognized(code_evidence, recognition)
            ):
                blockers.append(
                    f"attestation {slug}: code review is not backed by recognized code evidence"
                )
        if isinstance(code_evidence, dict) and recognition.policy_digest:
            provenance = code_evidence.get("provenance")
            if (
                not isinstance(provenance, dict)
                or provenance.get("policy_digest") != recognition.policy_digest
            ):
                blockers.append(
                    f"attestation {slug}: code evidence is bound to another recognition policy"
                )
    coverage = state.coverage_manifest
    try:
        missing = missing_coverage(code_subject, coverage)
    except ApprovalError:
        missing = ["<invalid code subject>"]
    if missing:
        blockers.append(f"attestation {slug}: implementation coverage omits a changed file")
    return blockers


def events_from_stateful_attestation(root: Path, slug: str) -> list[Event]:
    """Read one attestation's valid event lines for new-contract checks."""
    path = root / ATTESTATIONS_DIRNAME / f"{slug}.jsonl"
    events: list[Event] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for raw in lines:
        try:
            event = parse_event_line(raw)
        except EventSchemaError:
            continue
        if event.change_id == slug:
            events.append(event)
    return events


# --------------------------------------------------------------------------- #
# HG-12 cut 1: review-independence disclosure (substrate, NOT enforcement)
# --------------------------------------------------------------------------- #
def _classify_review(events: list[Event], milestone: str, author: str | None) -> dict[str, Any]:
    """One role's disclosure row, from the last occurrence of its PASS milestone.

    Truth table, first match wins:
      1. no milestone event                         → ``unattributed``
      2. reviewer ``actor.type == "ci"``            → ``ci`` (forward-compat;
         not producible via the current CLI, see design §4.1 row 2)
      3. ``payload["skipped"] is True``             → ``skipped``
      4. reviewer or author is the ``"cli"`` placeholder → ``unattributed``
      5. reviewer identifier == author identifier   → ``self-signed``
      6. otherwise                                  → ``independent``
    """
    reviews = [e for e in events if e.type == milestone]
    if not reviews:
        return {
            "classification": "unattributed",
            "reviewer": None,
            "skipped": False,
            "override": False,
            "reason": None,
        }
    r = reviews[-1]  # last wins (reject → re-review cycles)
    reviewer = r.actor.identifier
    skipped = r.payload.get("skipped") is True
    override = r.payload.get("override") is True
    if r.actor.type == "ci":
        cls = "ci"
    elif skipped:
        cls = "skipped"
    elif reviewer == PLACEHOLDER_IDENTITY or author == PLACEHOLDER_IDENTITY:
        cls = "unattributed"
    elif reviewer == author:
        cls = "self-signed"
    else:
        cls = "independent"
    return {
        "classification": cls,
        "reviewer": reviewer,
        "skipped": skipped,
        "override": override,
        "reason": r.payload.get("reason"),
    }


def derive_independence(events: list[Event]) -> dict[str, Any]:
    """Classify a change's review independence, per role, from its events (pure).

    Both roles, from the same truth table (see ``_classify_review``) applied to their
    PASS milestones — ``code_review_passed`` and ``plan_approved``. Plan review joined
    code review here because ``review skip --reviewer plan-reviewer`` otherwise emitted
    ``plan_approved`` and merged with nothing said anywhere, which is why a series of
    mis-drawn skip-evidence boundaries all failed silently rather than loudly.

    This is disclosure, not enforcement: the identity is self-asserted and a solo
    owner can set both sides freely. The one blocker built on it lives in
    ``verify_attestations``.
    """
    author = next((e.actor.identifier for e in events if e.type == "intent_declared"), None)
    return {
        "author": author,
        "code_review": _classify_review(events, "code_review_passed", author),
        "plan_review": _classify_review(events, "plan_approved", author),
        # Informational, like the rest of this function — NOT a merge blocker.
        # Hitting the round budget is a legitimate, human-authorized act; it has to be
        # visible at the moment of merge, not forbidden. Deduped on (reviewer,
        # attempted_round) so a retrying agent cannot inflate a figure presented as
        # rounds; an event without that field counts once via its own id.
        "review_budget_rounds_held": len(
            {
                (
                    (e.payload or {}).get("reviewer"),
                    (e.payload or {}).get("attempted_round", f"event:{e.event_id}"),
                )
                for e in events
                if e.type == "review_budget_exceeded"
            }
        ),
    }


def independence_for_attestation(att_path: Path) -> dict[str, Any]:
    """Read an attestation file tolerantly and derive independence.

    Tolerant parse (warn-equivalent skip of malformed lines, never raise) so the
    non-failing disclosure path can never crash an otherwise-passing
    ``attest verify`` — mirrors the reducer / ``check_attestation`` policy.
    """
    events: list[Event] = []
    for raw in att_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            events.append(parse_event_line(s))
        except EventSchemaError:
            continue
    return derive_independence(events)


# --------------------------------------------------------------------------- #
# Gate-bypass disclosure (escape-hatch hardening, Part C) — merge blocker
# --------------------------------------------------------------------------- #
def gate_bypass_disclosure(events: list[Event]) -> dict[str, Any]:
    """Count gate bypasses vs disclosures by APPEND ORDER (pure; no timestamps).

    Append position is causal truth. A ``gate_bypassed`` is undisclosed iff it
    appears after the last ``gate_bypass_disclosed`` in the event list.
    """
    last_disclosed = max(
        (i for i, e in enumerate(events) if e.type == "gate_bypass_disclosed"),
        default=-1,
    )
    undisclosed = sum(
        1 for i, e in enumerate(events) if e.type == "gate_bypassed" and i > last_disclosed
    )
    bypassed = sum(1 for e in events if e.type == "gate_bypassed")
    disclosed = sum(1 for e in events if e.type == "gate_bypass_disclosed")
    reasons = [e.payload.get("reason") for e in events if e.type == "gate_bypass_disclosed"]
    return {
        "bypassed": bypassed,
        "disclosed": disclosed,
        "undisclosed": undisclosed,
        "reasons": reasons,
    }


def gate_bypass_for_attestation(att_path: Path) -> dict[str, Any]:
    """Read an attestation file tolerantly and derive gate-bypass disclosure.

    Mirrors ``independence_for_attestation``'s tolerant parse: malformed lines
    are skipped (never raise) so the merge-gate blocker can never crash on a
    partially-corrupt attestation.
    """
    events: list[Event] = []
    for raw in att_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            events.append(parse_event_line(s))
        except EventSchemaError:
            continue
    return gate_bypass_disclosure(events)
