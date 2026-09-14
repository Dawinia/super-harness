"""Emit-time validation for EventWriter (lifecycle-event-model §3.8.1 layered validation).

Two-layer policy:
- Emit-time (this module): STRICT — reject illegal transitions BEFORE writing to
  events.jsonl. Raises `EmitPreconditionError`.
- Reducer-time (Task 1.6): TOLERANT — warn + skip illegal events at replay
  (events already on disk are immutable per Axiom 7).

This module also encodes hard prerequisites beyond the per-state transition
table (e.g. `implementation_complete` must follow a `verification_passed` on
the same change_id per spec §3.4). The transition table alone can't express
"event X requires event Y to have happened before" — only "state S accepts
event X" — so we layer `_HARD_PREREQ_EVENTS` on top.

Cost note: validate_preconditions reads events.jsonl on every emit (O(N) per
emit where N = total event count). v0.1 accepts this. v0.2 may add in-memory
state cache + per-change_id seen-events bitmask for O(1) emit if it shows up
in profiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from super_harness.core.approval import (
    ApprovalError,
    authorizing_event,
    evidence_digest,
    evidence_is_recognized,
    has_unresolved_candidate,
    load_recognition,
    make_code_subject,
    missing_coverage,
    recognition_contract_active,
    validate_approval,
    validate_code_subject,
    validate_evidence,
    validate_evidence_supersession,
    validate_implementation_assessment,
    validate_plan_subject,
)
from super_harness.core.events import Event, EventSchemaError, parse_event_line
from super_harness.core.scope_match import GitScopeError
from super_harness.core.transitions import INVALID, compute_target_state

__all__ = [
    "EmitPreconditionError",
    "OrderingViolation",
    "find_ordering_violations",
    "is_new_contract_event",
    "validate_preconditions",
]


class EmitPreconditionError(ValueError):
    """Raised by EventWriter when the new event would create an illegal transition."""


@dataclass(frozen=True)
class OrderingViolation:
    """One illegal step found while forward-walking a change's event stream.

    Emitted by `find_ordering_violations` (the whole-stream sibling of
    `validate_preconditions`, which only vets a single new candidate event).
    Frozen — a violation is an immutable diagnostic record.

    Fields:
        event_id: the offending event's id (as recorded on disk).
        event_type: the offending event's type.
        from_state: the change's derived state JUST BEFORE this event (the state
            the transition table rejected the event from), or None if the change
            had no prior state (the event was illegal as a first event).
        reason: a human-readable explanation (illegal transition vs missing hard
            prerequisite).
    """

    event_id: str
    event_type: str
    from_state: str | None
    reason: str


# Events with hard prerequisites beyond the transition table.
# Each entry: event_type -> list of event types that must have been emitted
# previously on the same change_id.
_HARD_PREREQ_EVENTS: dict[str, list[str]] = {
    # implementation_complete must follow verification_passed (lifecycle §3.4)
    "implementation_complete": ["verification_passed"],
}


def _current_state(events_file: Path, change_id: str) -> str | None:
    """Compute the current state for a single change by replaying its events.

    Cheap because each `emit` only needs the latest state; we don't build full
    state.yaml here. Mirrors reducer-time tolerant semantics (§3.8.1): illegal
    events on disk are skipped, not raised — emit-time strictness applies to
    the NEW event we're about to write, not to history.
    """
    if not events_file.exists():
        return None
    current: str | None = None
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            # Tolerant per §3.8.1 reducer-time: skip malformed lines on disk.
            continue
        if ev.change_id != change_id:
            continue
        target = compute_target_state(current, ev.type)
        if target == INVALID:
            continue  # tolerant per §3.8.1 reducer-time
        current = target
    return current


def _change_event_types(events_file: Path, change_id: str) -> set[str]:
    """Return the set of event types previously emitted for this change_id."""
    if not events_file.exists():
        return set()
    seen: set[str] = set()
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            continue
        if ev.change_id == change_id:
            seen.add(ev.type)
    return seen


def validate_preconditions(events_file: Path, new_event: Event) -> None:
    """Raise EmitPreconditionError if the new event violates strict emit-time rules.

    Two checks:
    1. Transition legality: (current_state, event_type) must be a legal
       transition per the table in `transitions.py`.
    2. Hard prerequisites: certain events require specific prior events on the
       same change_id (e.g. implementation_complete needs verification_passed).
    """
    current = _current_state(events_file, new_event.change_id)
    target = compute_target_state(current, new_event.type)
    if target == INVALID:
        if current is None:
            # No prior state for this change_id — only intent_declared can start
            # a change. Include that hint in the message so callers (and the
            # tests in test_emit_validation.py) get an actionable error.
            raise EmitPreconditionError(
                f"event {new_event.type!r} illegal as first event "
                f"(change_id={new_event.change_id}); "
                f"a change must start with 'intent_declared' "
                f"to reach INTENT_DECLARED state"
            )
        raise EmitPreconditionError(
            f"event {new_event.type!r} illegal from state {current!r} "
            f"(change_id={new_event.change_id})"
        )
    # additional hard prerequisites
    required = _HARD_PREREQ_EVENTS.get(new_event.type, [])
    if required:
        seen = _change_event_types(events_file, new_event.change_id)
        missing = [r for r in required if r not in seen]
        if missing:
            raise EmitPreconditionError(
                f"event {new_event.type!r} requires prior {missing} "
                f"(change_id={new_event.change_id})"
            )
    _validate_authority(events_file, new_event)


def _validate_authority(events_file: Path, new_event: Event) -> None:
    """Enforce new-contract authority at the shared writer seam.

    A state name is not evidence.  This guard therefore folds the event stream
    and checks the approval/reference payload before an authorizing event can
    be appended.  Legacy events already on disk are replayed by the reducer,
    but a new writer cannot create a new legacy approval or skip this check by
    passing ``skip_validation`` (the writer rejects that separately).
    """
    from super_harness.core.reducer import derive_state

    try:
        root = events_file.parent.parent
        active_recognition = recognition_contract_active(root)
        current = derive_state(events_file).get(new_event.change_id)
        payload = new_event.payload or {}
        new_contract = active_recognition or bool(
            payload.get("plan_subject")
            or payload.get("approval")
            or payload.get("evidence")
            or payload.get("code_subject")
            or (
                current is not None
                and (
                    current.effective_approval is not None
                    or current.pending_revision is not None
                    or current.current_code_subject is not None
                )
            )
        )
        if new_event.type == "plan_ready":
            subject = payload.get("plan_subject")
            if subject is None:
                if active_recognition:
                    raise ApprovalError(
                        "active review recognition requires a complete plan subject"
                    )
            else:
                validate_plan_subject(subject, change_id=new_event.change_id)
        elif new_event.type == "plan_revision_submitted":
            validate_plan_subject(payload.get("plan_subject"), change_id=new_event.change_id)
            if current is None or current.effective_approval is None:
                raise ApprovalError("a plan revision requires an existing effective approval")
            prior = payload.get("prior_approval")
            if prior != current.effective_approval.get("approval_id"):
                raise ApprovalError("plan revision is linked to a different approval")
            if has_unresolved_candidate(current):
                raise ApprovalError(
                    "withdraw the current plan revision before submitting another candidate"
                )
        elif new_event.type == "review_evidence_imported":
            evidence = validate_evidence(payload.get("evidence"), change_id=new_event.change_id)
            if current is None:
                raise ApprovalError("review evidence requires an existing Change")
            if payload.get("subject_id") != evidence.get("subject_id"):
                raise ApprovalError("review evidence event subject does not match its evidence")
            recognition = load_recognition(events_file.parent.parent)
            if not evidence_is_recognized(evidence, recognition):
                raise ApprovalError("evidence process or issuer is not user-recognized")
            if recognition.policy_digest:
                provenance = evidence.get("provenance", {})
                if (
                    not isinstance(provenance, dict)
                    or provenance.get("policy_digest") != recognition.policy_digest
                ):
                    raise ApprovalError("evidence policy digest is not recognized")
            subject = _current_subject(current, str(evidence["kind"]))
            historical_only = payload.get("historical_only") is True
            if subject is None:
                raise ApprovalError("review evidence requires a current subject")
            if subject.get("subject_id") != evidence.get("subject_id") and not historical_only:
                raise ApprovalError("review evidence does not match the current subject")
            for prior in current.evidence_references:
                if prior.get("evidence_id") == evidence.get("evidence_id"):
                    if evidence_digest(prior) != evidence_digest(evidence):
                        raise ApprovalError("evidence_id was reused with different content")
                    raise ApprovalError("review evidence was already imported")
            validate_evidence_supersession(evidence, current.evidence_references)
        elif new_event.type == "plan_approved":
            # Only the new evidence-backed record is writable.  Historical
            # ``skipped`` milestones remain readable in old logs.
            if not new_contract and payload.get("approval") is None:
                return
            approval = validate_approval(payload.get("approval"), change_id=new_event.change_id)
            if payload.get("evidence_id") != approval.get("evidence_id"):
                raise ApprovalError("plan approval evidence_id does not match approval")
            if current is None:
                raise ApprovalError("plan approval requires an existing Change")
            pending = current.pending_revision
            if not isinstance(pending, dict):
                raise ApprovalError("plan approval requires a current plan subject")
            if pending.get("status") == "withdrawn":
                raise ApprovalError("a withdrawn plan candidate cannot become approved")
            if pending.get("candidate_id") != approval.get("subject_id"):
                raise ApprovalError("plan approval does not match the current plan candidate")
            approved_evidence = _find_evidence(current, str(approval["evidence_id"]))
            if approved_evidence is None or approved_evidence.get("decision") != "approve":
                raise ApprovalError("plan approval requires its imported approving evidence")
            if evidence_digest(approved_evidence) != approval.get("evidence_digest"):
                raise ApprovalError("plan approval evidence digest does not match the import")
        elif new_event.type == "plan_rejected":
            if not new_contract:
                return
            evidence = validate_evidence(payload.get("evidence"), change_id=new_event.change_id)
            if evidence.get("decision") != "reject":
                raise ApprovalError("plan rejection requires rejecting evidence")
            if current is None or not isinstance(current.pending_revision, dict):
                raise ApprovalError("plan rejection requires a current plan candidate")
            if current.pending_revision.get("candidate_id") != evidence.get("subject_id"):
                raise ApprovalError("plan rejection does not match the current plan candidate")
            imported = _find_evidence(current, str(evidence["evidence_id"]))
            if imported is None or evidence_digest(imported) != evidence_digest(evidence):
                raise ApprovalError("plan rejection requires its imported evidence")
        elif new_event.type == "implementation_recorded":
            if current is None or current.effective_approval is None:
                raise ApprovalError(
                    "implementation assessment requires an applicable plan approval"
                )
            assessment = payload.get("assessment")
            coverage = payload.get("coverage")
            if not isinstance(coverage, list) or any(
                not isinstance(item, dict) for item in coverage
            ):
                raise ApprovalError("implementation assessment needs an object and coverage list")
            validate_implementation_assessment(
                assessment,
                approval=current.effective_approval,
                change_id=new_event.change_id,
            )
        elif new_event.type in {"verification_passed", "verification_failed"}:
            if not new_contract:
                return
            verification = payload.get("verification")
            if not isinstance(verification, dict):
                raise ApprovalError("new-contract verification needs a subject record")
            expected_outcome = "passed" if new_event.type == "verification_passed" else "failed"
            if verification.get("outcome") != expected_outcome:
                raise ApprovalError(
                    f"{new_event.type} outcome does not match its verification record"
                )
            code_subject = validate_code_subject(
                verification.get("code_subject"), change_id=new_event.change_id
            )
            if verification.get("subject_id") != code_subject.get("subject_id"):
                raise ApprovalError("verification subject_id does not match its code subject")
            if current is None or current.effective_approval is None:
                raise ApprovalError("verification requires an applicable plan approval")
            if code_subject.get("approval_id") != current.effective_approval.get("approval_id"):
                raise ApprovalError("verification uses a different plan approval")
            _validate_git_subject(events_file, code_subject)
        elif authorizing_event(new_event.type):
            if not new_contract:
                return
            has_authority = current is not None and (
                current.effective_approval is not None
                if new_contract
                else (
                    current.effective_approval is not None
                    or current.legacy_plan_approval is not None
                )
            )
            if not has_authority:
                raise ApprovalError(
                    f"event {new_event.type!r} requires an applicable plan approval"
                )
            if (
                current is not None
                and has_unresolved_candidate(current)
                and new_event.type
                in {
                    "implementation_complete",
                    "code_review_passed",
                    "merged",
                }
            ):
                raise ApprovalError(
                    "an unresolved plan revision must be withdrawn or approved "
                    "before completion/review/merge"
                )
            if new_event.type in {
                "implementation_complete",
                "code_review_passed",
                "code_review_failed",
            }:
                subject = payload.get("code_subject")
                if current is not None and current.effective_approval is not None:
                    validated_subject = validate_code_subject(
                        subject, change_id=new_event.change_id
                    )
                    if validated_subject.get("approval_id") != current.effective_approval.get(
                        "approval_id"
                    ):
                        raise ApprovalError("code subject uses a different plan approval")
                    if new_event.type == "implementation_complete":
                        if not current.implementation_assessments:
                            raise ApprovalError(
                                "implementation completion requires an implementation assessment"
                            )
                        if not current.coverage_manifest:
                            raise ApprovalError(
                                "implementation completion requires a coverage manifest"
                            )
                        missing = missing_coverage(validated_subject, current.coverage_manifest)
                        if missing:
                            raise ApprovalError(
                                "implementation coverage omits: " + ", ".join(missing)
                            )
                        verification = current.current_verification
                        if not isinstance(verification, dict):
                            raise ApprovalError(
                                "implementation completion requires current verification"
                            )
                        if verification.get("outcome") != "passed":
                            raise ApprovalError(
                                "implementation completion requires a passed verification"
                            )
                        if verification.get("subject_id") != validated_subject.get("subject_id"):
                            raise ApprovalError(
                                "implementation completion uses a different verification subject"
                            )
                    elif new_event.type in {"code_review_passed", "code_review_failed"}:
                        evidence_id = payload.get("evidence_id")
                        review_evidence = (
                            _find_evidence(current, str(evidence_id))
                            if isinstance(evidence_id, str)
                            else None
                        )
                        expected_decision = (
                            "approve" if new_event.type == "code_review_passed" else "reject"
                        )
                        if (
                            review_evidence is None
                            or review_evidence.get("kind") != "code"
                            or review_evidence.get("decision") != expected_decision
                        ):
                            raise ApprovalError(
                                "code review outcome requires matching imported evidence"
                            )
                        if review_evidence.get("subject_id") != validated_subject.get("subject_id"):
                            raise ApprovalError(
                                "code review evidence uses a different code subject"
                            )
                    _validate_git_subject(events_file, validated_subject)
            elif new_event.type == "merged":
                if current is None:
                    raise ApprovalError("merge requires an existing Change")
                if not isinstance(current.current_code_subject, dict):
                    raise ApprovalError("merge requires a current code subject")
                verification = current.current_verification
                if not isinstance(verification, dict) or verification.get("outcome") != "passed":
                    raise ApprovalError("merge requires current passed verification")
        elif new_event.type == "plan_withdrawn":
            if current is None or not isinstance(current.pending_revision, dict):
                raise ApprovalError("plan withdrawal requires a current revision candidate")
            pending = current.pending_revision
            if payload.get("candidate_id") != pending.get("candidate_id"):
                raise ApprovalError("plan withdrawal does not match the current candidate")
            if pending.get("status") not in {"pending", "rejected"}:
                raise ApprovalError("only pending or rejected candidates can be withdrawn")
            if payload.get("effective_approval") != (current.effective_approval or {}).get(
                "approval_id"
            ):
                raise ApprovalError("plan withdrawal is linked to a different approval")
    except (ApprovalError, GitScopeError, TypeError, KeyError) as exc:
        raise EmitPreconditionError(str(exc)) from exc


def _current_subject(state: object, kind: str) -> dict[str, object] | None:
    if kind == "code":
        subject = getattr(state, "current_code_subject", None)
        return subject if isinstance(subject, dict) else None
    pending = getattr(state, "pending_revision", None)
    if isinstance(pending, dict):
        subject = pending.get("subject")
        if isinstance(subject, dict):
            return subject
    approval = getattr(state, "effective_approval", None)
    if isinstance(approval, dict):
        subject = approval.get("subject")
        return subject if isinstance(subject, dict) else None
    return None


def _find_evidence(state: object, evidence_id: str) -> dict[str, object] | None:
    for evidence in getattr(state, "evidence_references", []):
        if isinstance(evidence, dict) and evidence.get("evidence_id") == evidence_id:
            return evidence
    return None


def _validate_git_subject(events_file: Path, subject: dict[str, object]) -> None:
    """Recompute a code subject before accepting a new-contract milestone."""
    expected = make_code_subject(
        events_file.parent.parent,
        change_id=str(subject["change_id"]),
        approval_id=str(subject["approval_id"]),
        base=str(subject["base"]),
        head=str(subject["head"]),
    )
    if expected["subject_id"] != subject.get("subject_id"):
        raise ApprovalError("code subject does not match the complete Git change set")


def is_new_contract_event(events_file: Path, event: Event) -> bool:
    """Whether an event belongs to a new-contract stream.

    Legacy events remain replayable for historical attestations.  A subject or
    evidence payload, or a current state carrying one of the derived new
    authority fields, opts the write into the stricter contract.
    """
    from super_harness.core.reducer import derive_state

    payload = event.payload or {}
    if any(
        payload.get(key) is not None
        for key in ("plan_subject", "approval", "evidence", "code_subject")
    ):
        return True
    current = derive_state(events_file).get(event.change_id)
    return bool(
        current is not None
        and (
            current.effective_approval is not None
            or current.pending_revision is not None
            or current.current_code_subject is not None
        )
    )


def find_ordering_violations(events_file: Path, change_id: str) -> list[OrderingViolation]:
    """Forward-walk a change's events; report transitions the table rejects.

    The whole-stream sibling of `validate_preconditions` (which only vets a
    SINGLE new candidate before append). Used by the Phase 8 `lifecycle-ordering`
    baseline check as an integrity / tamper signal: a well-behaved stream (every
    event appended through the strict emit-time gate) can never be out of order,
    so any violation here means the stream was hand-edited, imported with
    `skip_validation`, or otherwise corrupted.

    Two illegal-step classes are detected, both built on the existing transition
    primitives (`compute_target_state` / `INVALID` / `_HARD_PREREQ_EVENTS`) — the
    transition table is NOT re-implemented here:

    1. Illegal transition: `compute_target_state(current, ev.type)` returns
       `INVALID`. The bad event is recorded and `current` is NOT advanced past it
       (kept as-is) so subsequent legal events are still validated against the
       last GOOD state, matching the reducer's "preserve state" tolerance.
    2. Missing hard prerequisite (`_HARD_PREREQ_EVENTS`): an event whose required
       prior event type(s) have not yet been seen for this change.

    Malformed lines are skipped (tolerant, like the reducer / `_current_state`).

    Args:
        events_file: the `.harness/events.jsonl` path (may not exist).
        change_id: restrict the walk to this change's events.

    Returns:
        Violations in append order. Empty list for a clean stream (or a
        nonexistent file / a change with no events).
    """
    if not events_file.exists():
        return []
    violations: list[OrderingViolation] = []
    current: str | None = None
    seen: set[str] = set()
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            # Tolerant per §3.8.1 reducer-time: skip malformed lines on disk.
            continue
        if ev.change_id != change_id:
            continue

        # Hard-prerequisite check BEFORE recording this type as seen (a prereq
        # cannot be satisfied by the event itself).
        required = _HARD_PREREQ_EVENTS.get(ev.type, [])
        missing = [r for r in required if r not in seen]
        if missing:
            violations.append(
                OrderingViolation(
                    event_id=ev.event_id,
                    event_type=ev.type,
                    from_state=current,
                    reason=(
                        f"event {ev.type!r} requires prior {missing} "
                        f"on the same change before it can appear"
                    ),
                )
            )

        seen.add(ev.type)

        target = compute_target_state(current, ev.type)
        if target == INVALID:
            reason = (
                f"event {ev.type!r} illegal as first event for this change"
                if current is None
                else f"event {ev.type!r} illegal from state {current!r}"
            )
            violations.append(
                OrderingViolation(
                    event_id=ev.event_id,
                    event_type=ev.type,
                    from_state=current,
                    reason=reason,
                )
            )
            # Do NOT advance `current` past the bad event — keep the last good
            # state so subsequent legal events still validate (reducer parity).
            continue
        current = target

    return violations
