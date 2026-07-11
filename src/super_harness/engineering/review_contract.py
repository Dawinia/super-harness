"""Compile deterministic, per-source review inspection contracts."""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from super_harness.core.events import Event
from super_harness.core.review_bundle import resolve_declared_artifact_paths
from super_harness.core.review_verdict import derive_open_finding_records
from super_harness.core.scope_match import (
    GitScopeError,
    is_ancestor,
    merge_base_commit,
    resolve_commit,
    scope_diff_argv,
    split_changed_by_scope_between,
)
from super_harness.engineering.reviewer_policy import ReviewerIndependencePolicy


class ReviewContractError(ValueError):
    """Repository state cannot compile to a trustworthy review contract."""


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
    return (
        f"Review source: {source}\n"
        f"Context policy: {context or 'legacy'}\n"
        f"Inspection mode: {inspection['mode']}\n"
        f"Inspection argv: {argv}\n"
        f"Checklist: {json.dumps(checklist, separators=(',', ':'))}\n\n"
        f"{empty_target_guidance}"
        f"{prior_finding_guidance}"
        "Review only the assigned target delta. Read unchanged files only for directly "
        "affected context. Report only issues caused by this target, dependencies or "
        "regressions made relevant by it, or unresolved prior findings. Do not expand to "
        "the whole PR or unrelated pre-existing issues. Continue through the full assigned "
        "target after finding a blocker. If this scope is insufficient, return a partial "
        "rejection instead of expanding it. Return YAML only with this recordable shape:\n"
        f"bundle_digest: {bundle_digest}\n"
        "checklist:\n"
        "  - item: <copy each assigned checklist item exactly, once>\n"
        "    status: pass | fail | na\n"
        "    note: <optional>\n"
        "findings: []  # required non-empty when any checklist item fails\n"
        "# finding fields: id, severity (blocker | major | minor), file, summary\n"
        "prior_findings: []  # dispose open ids with resolved or wontfix + note\n"
        "Do not edit files or invoke super-harness verdict commands."
    )


def compile_review_contract(
    root: Path,
    *,
    bundle: dict[str, Any],
    policy: ReviewerIndependencePolicy,
    events: list[Event],
    declared: list[str],
) -> dict[str, Any]:
    """Add target and deterministic per-participant assignments to ``bundle``."""
    target_head = resolve_commit(root)
    full_base = merge_base_commit(root, str(bundle["base"]), target_head)
    checklist = [str(item) for item in bundle.get("checklist", [])]
    assignments: list[dict[str, Any]] = []
    open_findings = (
        derive_open_finding_records(events, str(bundle["change"]))
        if policy.reviewer == "code-reviewer"
        else []
    )
    current_artifacts = [
        path
        for path in (bundle.get("spec_path"), bundle.get("plan_path"))
        if isinstance(path, str) and path
    ]

    if policy.reviewer == "code-reviewer":
        approved_plan_head = next(
            (
                event.payload.get("reviewed_head")
                for event in reversed(events)
                if event.type == "plan_approved"
                and isinstance(event.payload.get("reviewed_head"), str)
            ),
            None,
        )
        approved_artifacts: list[str] = []
        if isinstance(approved_plan_head, str):
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
        artifact_paths = sorted(set(current_artifacts + approved_artifacts))
        if isinstance(approved_plan_head, str) and artifact_paths:
            if not is_ancestor(root, approved_plan_head, target_head):
                raise ReviewContractError(
                    "approved plan review head is not an ancestor of the current target"
                )
            changed_artifacts, _ = split_changed_by_scope_between(
                root,
                base=approved_plan_head,
                head=target_head,
                declared=artifact_paths,
            )
            if changed_artifacts:
                raise ReviewContractError(
                    "approved plan/spec changed without plan redeclaration; run "
                    "`super-harness plan redeclare <change> --reason <why>`"
                )

    assignment_scope = (
        current_artifacts
        if policy.reviewer == "plan-reviewer" and current_artifacts
        else declared
    )

    for source in policy.participants:
        profile = policy.source_profiles.get(source)
        if profile is None:
            continue
        baseline = resolve_source_baseline(
            events,
            reviewer=policy.reviewer,
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
        incremental = (
            profile.context != "full-change"
            and resolved_baseline is not None
            and is_ancestor(root, resolved_baseline, target_head)
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
        assignments.append(
            {
                "source": source,
                "agent": profile.agent,
                "context": profile.context,
                "agent_options": dict(profile.agent_options),
                "inspection": inspection,
                "prompt": _review_prompt(
                    source=source,
                    context=profile.context,
                    inspection=inspection,
                    checklist=checklist,
                    bundle_digest=str(bundle["bundle_digest"]),
                    open_findings=open_findings,
                ),
            }
        )

    bundle["target_head"] = target_head
    bundle["plan_review_required"] = policy.reviewer == "plan-reviewer"
    bundle["assignments"] = assignments
    return bundle
