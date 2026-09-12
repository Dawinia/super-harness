"""Import externally produced review conclusions.

The active review surface deliberately has no producer, model profile, round,
retry budget, or human nonce executor. Those objects remain readable through
historical report/attestation readers, but cannot create new authority.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.approval import (
    ApprovalError,
    approval_record,
    evidence_digest,
    evidence_is_recognized,
    load_json_record,
    load_recognition,
    validate_evidence,
)
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import EmitPreconditionError
from super_harness.core.events import Actor, Event
from super_harness.core.identity import resolve_identity
from super_harness.core.paths import HarnessNotInitialized, events_path, find_harness_root
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.review_verdict import read_change_events
from super_harness.core.scope_match import GitScopeError
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION

# Kept as a pure historical reader helper for old report tests.  It is not
# used by the active external-evidence import path and does not select or run a
# reviewer.
_MODEL_QUALIFIER_RE = re.compile(r"\[[^\[\]]*\]")


def _model_qualifiers(value: str) -> tuple[frozenset[str], str]:
    return frozenset(_MODEL_QUALIFIER_RE.findall(value)), _MODEL_QUALIFIER_RE.sub("", value)


def _model_contradicts(requested: str, actual: str) -> bool:
    """Return whether two historical model identifiers explicitly conflict."""
    req = requested.strip().lower()
    act = actual.strip().lower()
    if not req or not act:
        return False
    req_quals, req_base = _model_qualifiers(req)
    act_quals, act_base = _model_qualifiers(act)
    if not req_quals <= act_quals:
        return True
    return req_base not in act_base and act_base not in req_base


@click.group("review")
def review_group() -> None:
    """Import externally produced plan/code evidence."""


def _retired(name: str) -> None:
    click.echo(
        format_error(
            subcommand=f"review {name}",
            message="the reviewer execution command was removed from the active core",
            hint="Produce a recognized evidence record externally, then use `review import`.",
        ),
        err=True,
    )
    sys.exit(EXIT_VALIDATION)


@review_group.command("approve")
@click.argument("change")
def approve(change: str) -> None:
    """Retired; external evidence must be imported instead."""
    del change
    _retired("approve")


@review_group.command("reject")
@click.argument("change")
def reject(change: str) -> None:
    """Retired; external evidence must be imported instead."""
    del change
    _retired("reject")


@review_group.command("skip")
@click.argument("change")
def skip(change: str) -> None:
    """Retired; skips cannot create new-contract authority."""
    del change
    _retired("skip")


@review_group.command("prepare")
@click.argument("change")
def prepare(change: str) -> None:
    """Retired; the core no longer prepares reviewer runs."""
    del change
    _retired("prepare")


@review_group.command("begin")
@click.argument("change")
def begin(change: str) -> None:
    """Retired; the core no longer begins reviewer runs."""
    del change
    _retired("begin")


@review_group.command("authorize")
@click.argument("change")
def authorize(change: str) -> None:
    """Retired; the core no longer authorizes reviewer retries."""
    del change
    _retired("authorize")


@review_group.group("result")
def result_group() -> None:
    """Retired historical result namespace."""


@result_group.command("import")
@click.argument("change")
def result_import(change: str) -> None:
    """Retired; import the external conclusion with review import."""
    del change
    _retired("result import")


@review_group.group("run")
def run_group() -> None:
    """Retired historical run namespace."""


@run_group.command("fail")
@click.argument("change")
def run_fail(change: str) -> None:
    """Retired; producer failures are external evidence-process concerns."""
    del change
    _retired("run fail")


@review_group.group("human")
def human_group() -> None:
    """Retired historical TTY review namespace."""


@human_group.command("inspect")
@click.argument("change")
def human_inspect(change: str) -> None:
    """Retired; the core no longer runs a human nonce flow."""
    del change
    _retired("human inspect")


@human_group.command("draft")
@click.argument("change")
def human_draft(change: str) -> None:
    """Retired; the core no longer runs a human nonce flow."""
    del change
    _retired("human draft")


@human_group.command("confirm")
@click.argument("change")
def human_confirm(change: str) -> None:
    """Retired; the core no longer runs a human nonce flow."""
    del change
    _retired("human confirm")


def _subject_for_change(root: Path, change: str, kind: str) -> tuple[str, dict[str, Any]]:
    state = derive_state(events_path(root)).get(change)
    if state is None:
        raise ApprovalError(f"unknown Change {change!r}")
    if kind == "plan":
        candidate = state.pending_revision
        subject = candidate.get("subject") if isinstance(candidate, dict) else None
        if not isinstance(subject, dict) and isinstance(state.effective_approval, dict):
            subject = state.effective_approval.get("subject")
    else:
        subject = state.current_code_subject
    if not isinstance(subject, dict) or not isinstance(subject.get("subject_id"), str):
        raise ApprovalError(f"Change {change!r} has no current {kind} subject")
    return subject["subject_id"], subject


def _prior_evidence(root: Path, change: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in read_change_events(events_path(root), change):
        if event.type == "review_evidence_imported":
            evidence = event.payload.get("evidence")
            if isinstance(evidence, dict):
                out.append(dict(evidence))
    return out


def _emit_imported_conclusion(
    root: Path,
    *,
    change: str,
    evidence: dict[str, Any],
    subject: dict[str, Any],
    actor: Actor,
    historical_only: bool = False,
) -> None:
    state = derive_state(events_path(root)).get(change)
    if state is None:
        raise ApprovalError(f"unknown Change {change!r}")
    framework = state.framework
    writer = EventWriter(events_path(root))
    digest = evidence_digest(evidence)
    evidence_event = Event(
        event_id=new_event_id(),
        type="review_evidence_imported",
        change_id=change,
        timestamp=utc_now_iso(),
        actor=actor,
        framework=framework,
        payload={
            "evidence": evidence,
            "evidence_digest": digest,
            "subject_id": evidence["subject_id"],
            "historical_only": historical_only,
        },
    )
    pending = state.pending_revision
    if historical_only or (
        evidence["kind"] == "plan"
        and isinstance(pending, dict)
        and pending.get("status") == "withdrawn"
    ):
        # Preserve a late verdict for audit, but never let it reactivate a
        # candidate that was explicitly withdrawn.
        writer.emit(evidence_event)
        return
    if evidence["kind"] == "plan":
        if evidence["decision"] == "approve":
            writer.emit_many(
                [
                    evidence_event,
                    Event(
                        event_id=new_event_id(),
                        type="plan_approved",
                        change_id=change,
                        timestamp=utc_now_iso(),
                        actor=actor,
                        framework=framework,
                        payload={
                            "evidence_id": evidence["evidence_id"],
                            "evidence": evidence,
                            "approval": approval_record(
                                subject=subject,
                                evidence_id=str(evidence["evidence_id"]),
                                evidence_digest=digest,
                            ),
                        },
                    ),
                ]
            )
        else:
            writer.emit_many(
                [
                    evidence_event,
                    Event(
                        event_id=new_event_id(),
                        type="plan_rejected",
                        change_id=change,
                        timestamp=utc_now_iso(),
                        actor=actor,
                        framework=framework,
                        payload={"evidence": evidence, "candidate_id": subject["subject_id"]},
                    ),
                ]
            )
    elif evidence["decision"] == "approve":
        writer.emit_many(
            [
                evidence_event,
                Event(
                    event_id=new_event_id(),
                    type="code_review_passed",
                    change_id=change,
                    timestamp=utc_now_iso(),
                    actor=actor,
                    framework=framework,
                    payload={
                        "evidence": evidence,
                        "evidence_id": evidence["evidence_id"],
                        "code_subject": subject,
                    },
                ),
            ]
        )
    else:
        writer.emit_many(
            [
                evidence_event,
                Event(
                    event_id=new_event_id(),
                    type="code_review_failed",
                    change_id=change,
                    timestamp=utc_now_iso(),
                    actor=actor,
                    framework=framework,
                    payload={
                        "evidence": evidence,
                        "evidence_id": evidence["evidence_id"],
                        "code_subject": subject,
                    },
                ),
            ]
        )


@review_group.command("import")
@click.argument("change")
@click.option(
    "--evidence",
    "evidence_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON conclusion produced by a user-recognized external process.",
)
@click.pass_context
def import_evidence(ctx: click.Context, change: str, evidence_path: str) -> None:
    """Import one exact, externally produced plan or code conclusion."""
    subcommand = "review import"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
        evidence = load_json_record(Path(evidence_path))
        validate_evidence(evidence, change_id=change)
        recognition = load_recognition(root)
        if not evidence_is_recognized(evidence, recognition):
            raise ApprovalError("evidence process or issuer is not user-recognized")
        if recognition.policy_digest:
            provenance = evidence.get("provenance", {})
            if (
                not isinstance(provenance, dict)
                or provenance.get("policy_digest") != recognition.policy_digest
            ):
                raise ApprovalError("evidence policy digest is not recognized")
        subject_id, subject = _subject_for_change(root, change, str(evidence["kind"]))
        historical_only = evidence.get("subject_id") != subject_id
        prior = _prior_evidence(root, change)
        same_id = [item for item in prior if item.get("evidence_id") == evidence["evidence_id"]]
        if same_id:
            if evidence_digest(same_id[-1]) != evidence_digest(evidence):
                raise ApprovalError("evidence_id was reused with different content")
            data = {
                "change": change,
                "evidence_id": evidence["evidence_id"],
                "idempotent": True,
            }
            if ctx.obj.get("json"):
                click.echo(
                    json_envelope(
                        command=subcommand,
                        status="pass",
                        exit_code=EXIT_OK,
                        data=data,
                    )
                )
            elif not ctx.obj.get("quiet"):
                click.echo(f"super-harness: evidence {evidence['evidence_id']} already imported")
            sys.exit(EXIT_OK)
        same_subject = [item for item in prior if item.get("subject_id") == subject_id]
        if same_subject and evidence.get("supersedes") != same_subject[-1].get("evidence_id"):
            raise ApprovalError(
                "a new conclusion for this subject must explicitly supersede the current conclusion"
            )
        actor = Actor(type="human", identifier=resolve_identity(root, None))
        _emit_imported_conclusion(
            root,
            change=change,
            evidence=evidence,
            subject=subject,
            actor=actor,
            historical_only=historical_only,
        )
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    except (ApprovalError, EmitPreconditionError, GitScopeError) as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)
    data = {
        "change": change,
        "evidence_id": evidence["evidence_id"],
        "kind": evidence["kind"],
        "decision": evidence["decision"],
    }
    if ctx.obj.get("json"):
        click.echo(json_envelope(command=subcommand, status="pass", exit_code=EXIT_OK, data=data))
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: imported {evidence['kind']} evidence "
            f"{evidence['evidence_id']} for {change}"
        )
    sys.exit(EXIT_OK)


__all__ = ["review_group"]
