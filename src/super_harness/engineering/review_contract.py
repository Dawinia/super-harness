"""Compile deterministic, per-source review inspection contracts."""
from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

from super_harness.core.events import Event
from super_harness.core.review_bundle import resolve_declared_artifact_paths
from super_harness.core.review_verdict import derive_open_findings
from super_harness.core.scope_match import (
    GitScopeError,
    is_ancestor,
    merge_base_commit,
    resolve_commit,
    scope_diff_argv,
    split_changed_by_scope_between,
)
from super_harness.engineering.review_governance import ReviewGovernance
from super_harness.engineering.review_profiles import ReviewProducerProfile


class ReviewContractError(ValueError):
    """Repository state cannot compile to a trustworthy review contract."""


def _open_finding_records(
    events: list[Event], change_id: str
) -> list[dict[str, Any]]:
    open_ids = derive_open_findings(events, change_id)
    latest_by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.change_id != change_id:
            continue
        payload = event.payload or {}
        # Mirrors derive_open_findings: only the new receipt protocol and the
        # rejection milestone carry authoritative findings. The unreleased legacy
        # review_verdict_recorded event is never emitted and its ids can no longer
        # appear in open_ids, so it is excluded here for consistency (PR#79 #9/#3).
        is_code_result = event.type == "code_review_failed" or (
            event.type == "review_result_imported"
            and payload.get("reviewer") == "code-reviewer"
        )
        if not is_code_result:
            continue
        verdict = payload.get("verdict")
        findings = verdict.get("findings") if isinstance(verdict, dict) else None
        if not isinstance(findings, list):
            continue
        for finding in findings:
            finding_id = finding.get("id") if isinstance(finding, dict) else None
            if isinstance(finding_id, str):
                latest_by_id[finding_id] = dict(finding)
    return [latest_by_id[finding_id] for finding_id in open_ids if finding_id in latest_by_id]


def resolve_source_baseline(
    events: Iterable[Event],
    *,
    reviewer: str,
    source: str,
    required_checklist: tuple[str, ...],
) -> str | None:
    """Return the latest complete source result's reviewed commit, if usable."""
    for event in reversed(list(events)):
        if event.type in {"plan_redeclared", "intent_redeclared"}:
            return None
        if event.type == "review_result_imported":
            payload = event.payload or {}
            if payload.get("reviewer") != reviewer or payload.get("source") != source:
                continue
            verdict = payload.get("verdict")
            if not isinstance(verdict, dict) or verdict.get("scope_sufficient") is False:
                return None
            checklist = verdict.get("checklist")
            covered = {
                row.get("item")
                for row in checklist
                if isinstance(row, dict) and isinstance(row.get("item"), str)
            } if isinstance(checklist, list) else set()
            if not set(required_checklist).issubset(covered):
                return None
            reviewed_head = payload.get("target_head")
            return (
                reviewed_head
                if isinstance(reviewed_head, str) and reviewed_head
                else None
            )
        if event.type not in {"review_verdict_recorded", "code_review_failed", "plan_rejected"}:
            continue
        payload = event.payload or {}
        if payload.get("reviewer") != reviewer or payload.get("source") != source:
            continue
        complete = payload.get("outcome") == "approved"
        if event.type in {"code_review_failed", "plan_rejected"}:
            verdict = payload.get("verdict")
            checklist = verdict.get("checklist") if isinstance(verdict, dict) else None
            covered = {
                row.get("item")
                for row in checklist
                if isinstance(row, dict) and isinstance(row.get("item"), str)
            } if isinstance(checklist, list) else set()
            complete = set(required_checklist).issubset(covered)
        if not complete:
            return None
        reviewed_head = payload.get("reviewed_head")
        return reviewed_head if isinstance(reviewed_head, str) and reviewed_head else None
    return None


def _review_prompt(
    *,
    source: str,
    context: str | None,
    inspection: dict[str, Any],
    checklist: list[str],
    bundle_digest: str,
    open_findings: list[dict[str, Any]],
    blocking_severity: str,
    pass_with_open: bool,
) -> str:
    argv = json.dumps(inspection["diff_argv"], separators=(",", ":"))
    empty_target_guidance = (
        "The assigned target is empty; do not construct a broader diff.\n"
        if not inspection["diff_argv"]
        else ""
    )
    prior_finding_guidance = (
        "Open prior findings: "
        f"{json.dumps(open_findings, separators=(',', ':'))}\n"
        "Verify each against the assigned target and include one prior_findings "
        "disposition for every id.\n"
        if open_findings
        else ""
    )
    pass_with_open_guidance = (
        f"This round rejects the change only when a finding's severity is at or "
        f"above `{blocking_severity}` (or scope is insufficient). A checklist item "
        "marked `fail` whose findings are all below that threshold passes with the "
        "finding left open — it stays recorded and surfaced by `super-harness "
        "report`, it does not force a re-review round. To block the change, raise a "
        f"finding at or above `{blocking_severity}`.\n"
        if pass_with_open
        else ""
    )
    return (
        f"Review source: {source}\n"
        f"Supporting context: {context or 'repository'}\n"
        f"Inspection mode: {inspection['mode']}\n"
        f"Inspection argv: {argv}\n"
        f"Checklist: {json.dumps(checklist, separators=(',', ':'))}\n\n"
        f"{empty_target_guidance}"
        f"{prior_finding_guidance}"
        "Review only the assigned target delta. You may read any unchanged repository "
        "context needed to understand architecture, binding decisions, and impact. "
        "Report only issues caused by this target, dependencies or "
        "regressions made relevant by it, or unresolved prior findings. Do not expand to "
        "the whole PR or unrelated pre-existing issues. Continue through the full assigned "
        "target after finding a blocker. If this scope is insufficient, return a partial "
        "rejection instead of expanding it. Return one JSON object with this recordable shape:\n"
        f"bundle_digest: {bundle_digest}\n"
        "checklist:\n"
        "  - item: <copy each assigned checklist item exactly, once>\n"
        "    status: pass | fail | na\n"
        "    note: <optional>\n"
        "findings: []  # required non-empty when any checklist item fails\n"
        "# finding fields: id, severity (blocker | major | minor), file, summary\n"
        "prior_findings: []  # dispose open ids with resolved or wontfix + note\n"
        f"{pass_with_open_guidance}"
        "Do not edit files or invoke super-harness verdict commands."
    )


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def compile_review_contract(
    root: Path,
    *,
    bundle: dict[str, Any],
    governance: ReviewGovernance,
    profiles: dict[str, ReviewProducerProfile] | None = None,
    events: list[Event],
    declared: list[str],
) -> dict[str, Any]:
    """Add target and deterministic per-participant assignments to ``bundle``."""
    reviewer = str(bundle.get("reviewer") or "")
    role = governance.roles.get(reviewer)
    if role is None:
        raise ReviewContractError(f"review role {reviewer!r} is not configured")
    participants = role.participants
    resolved_profiles = profiles or {}

    target_head = resolve_commit(root)
    full_base = merge_base_commit(root, str(bundle["base"]), target_head)
    checklist = [str(item) for item in bundle.get("checklist", [])]
    assignments: list[dict[str, Any]] = []
    open_findings = (
        _open_finding_records(events, str(bundle["change"]))
        if reviewer == "code-reviewer"
        else []
    )
    current_artifacts = [
        path
        for path in (bundle.get("spec_path"), bundle.get("plan_path"))
        if isinstance(path, str) and path
    ]

    # Advisory warnings surfaced to the caller (prepare reports these). Kept
    # visible rather than silently swallowed so a skipped plan-drift guard is
    # never mistaken for "no drift" (PR#79 finding #8 follow-up).
    drift_warnings: list[str] = []

    if reviewer == "code-reviewer":
        latest_plan_approval = next(
            (event for event in reversed(events) if event.type == "plan_approved"),
            None,
        )
        approved_plan_head = (
            latest_plan_approval.payload.get("reviewed_head")
            if latest_plan_approval is not None
            and isinstance(latest_plan_approval.payload.get("reviewed_head"), str)
            else None
        )
        approved_artifacts: list[str] = []
        if isinstance(approved_plan_head, str):
            try:
                approved_artifacts = [
                    path
                    for path in resolve_declared_artifact_paths(
                        root,
                        declared,
                        str(bundle["change"]),
                        ref=approved_plan_head,
                    )
                    if path
                ]
            except GitScopeError:
                # The approved plan head is no longer a resolvable ref (e.g. gc'd
                # after a history rewrite). Fall back to current-target artifacts
                # only rather than crashing the prepare (PR#79 finding #8).
                approved_artifacts = []
                drift_warnings.append(
                    "approved plan review head "
                    f"{approved_plan_head[:12]} is unresolvable (history rewritten "
                    "or gc'd); plan-drift detection is limited to current-target "
                    "artifacts"
                )
        artifact_paths = sorted(set(current_artifacts + approved_artifacts))
        if isinstance(approved_plan_head, str) and artifact_paths:
            # Detect plan/spec drift by comparing the artifact *content* between the
            # approved plan head and the current target. `git diff base..head` is a
            # direct two-tree diff and does not require ancestry, so a routine
            # `git rebase`/squash that leaves the plan byte-identical no longer
            # permanently blocks code-review prepare with an unfollowable "not an
            # ancestor" error (PR#79 finding #8); a genuine plan change still trips
            # the redeclaration guard below.
            try:
                changed_artifacts, _ = split_changed_by_scope_between(
                    root,
                    base=approved_plan_head,
                    head=target_head,
                    declared=artifact_paths,
                )
            except GitScopeError:
                changed_artifacts = []
                drift_warnings.append(
                    "plan-drift guard skipped: cannot diff plan/spec artifacts "
                    f"against approved plan head {approved_plan_head[:12]} "
                    "(unresolvable ref); the guard could not confirm the plan is "
                    "unchanged"
                )
            if changed_artifacts:
                raise ReviewContractError(
                    "approved plan/spec changed without plan redeclaration; run "
                    "`super-harness plan redeclare <change> --reason <why>`"
                )

    assignment_scope = (
        current_artifacts
        if reviewer == "plan-reviewer" and current_artifacts
        else declared
    )

    profile_payload: dict[str, object] = {}
    for source in participants:
        source_kind = governance.sources[source].kind
        profile = resolved_profiles.get(source)
        if source_kind == "automated" and profile is None:
            raise ReviewContractError(
                f"automated source {source!r} has no resolved local profile"
            )
        context = "repository"
        protocol = profile.protocol if profile is not None else "human"
        model = profile.model if profile is not None else None
        cost_class = profile.cost_class if profile is not None else None
        agent_options = dict(profile.agent_options) if profile is not None else {}

        profile_payload[source] = {
            "kind": source_kind,
            "protocol": protocol,
            "model": model,
            "cost_class": cost_class,
            "agent_options": agent_options,
        }
        baseline = resolve_source_baseline(
            events,
            reviewer=reviewer,
            source=source,
            required_checklist=tuple(checklist),
        )
        resolved_baseline: str | None = None
        if baseline is not None:
            try:
                resolved_baseline = resolve_commit(root, baseline)
            except GitScopeError:
                # Historical review events are not authoritative Git refs. An
                # unresolvable one loses incremental eligibility; current target
                # and ancestry failures still fail closed outside this branch.
                resolved_baseline = None
        incremental = resolved_baseline is not None and is_ancestor(
            root, resolved_baseline, target_head
        )
        inspection_base = (
            resolved_baseline if incremental and resolved_baseline is not None else full_base
        )
        mode = "incremental" if incremental else "full-change"
        files, _ = split_changed_by_scope_between(
            root, base=inspection_base, head=target_head, declared=assignment_scope
        )
        inspection = {
            "mode": mode,
            "base": inspection_base,
            "head": target_head,
            "files": files,
            "diff_argv": scope_diff_argv(inspection_base, target_head, files),
        }
        prompt = _review_prompt(
            source=source,
            context=context,
            inspection=inspection,
            checklist=checklist,
            bundle_digest=str(bundle["bundle_digest"]),
            open_findings=open_findings,
            blocking_severity=role.blocking_severity,
            pass_with_open=reviewer == "code-reviewer",
        )
        assignments.append(
            {
                "source": source,
                "kind": source_kind,
                "protocol": protocol,
                "model": model,
                "cost_class": cost_class,
                "context": context,
                "agent_options": agent_options,
                "inspection": inspection,
                "prompt": prompt,
                "prompt_digest": sha256(prompt.encode("utf-8")).hexdigest(),
            }
        )

    bundle["target_head"] = target_head
    bundle["plan_review_required"] = reviewer == "plan-reviewer"
    bundle["assignments"] = assignments
    bundle["participant_digest"] = _digest(sorted(participants))
    bundle["profile_digest"] = _digest(profile_payload)
    bundle["warnings"] = [
        *drift_warnings,
        *(
            [
                "large inspection target; review remains one complete assignment "
                "and is not automatically sharded"
            ]
            if any(
                len(assignment["inspection"]["files"]) >= 50
                for assignment in assignments
            )
            else []
        ),
    ]
    contract_payload = dict(bundle)
    contract_payload.pop("contract_digest", None)
    bundle["contract_digest"] = _digest(contract_payload)
    return bundle
