"""Behavior tests for compiling per-source review baselines and contracts."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from super_harness.core.events import Actor, Event
from super_harness.engineering.review_contract import (
    ReviewContractError,
    compile_review_contract,
    resolve_source_baseline,
)
from super_harness.engineering.review_governance import (
    ReviewerRoleGovernance,
    ReviewerSourceGovernance,
    ReviewGovernance,
)
from super_harness.engineering.review_profiles import ReviewProducerProfile


def _event(event_type: str, payload: dict[str, object]) -> Event:
    return Event(
        event_id=f"e-{event_type}",
        type=event_type,
        change_id="change",
        timestamp="2026-07-11T00:00:00Z",
        actor=Actor(type="agent", identifier="reviewer"),
        framework="plain",
        payload=payload,
    )


def _git(ws: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, text=True)


def test_approved_source_result_establishes_baseline() -> None:
    events = [
        _event(
            "review_verdict_recorded",
            {
                "reviewer": "code-reviewer",
                "source": "external",
                "outcome": "approved",
                "reviewed_head": "a" * 40,
            },
        )
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="external",
        required_checklist=("correctness",),
    ) == "a" * 40


def test_complete_structured_rejection_establishes_baseline() -> None:
    events = [
        _event(
            "code_review_failed",
            {
                "reviewer": "code-reviewer",
                "source": "subagent",
                "reviewed_head": "b" * 40,
                "verdict": {
                    "checklist": [
                        {"item": "correctness", "status": "fail"},
                        {"item": "tests", "status": "pass"},
                    ]
                },
            },
        )
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="subagent",
        required_checklist=("correctness", "tests"),
    ) == "b" * 40


def test_plan_redeclaration_invalidates_older_source_baseline() -> None:
    events = [
        _event(
            "review_verdict_recorded",
            {
                "reviewer": "code-reviewer",
                "source": "external",
                "outcome": "approved",
                "reviewed_head": "a" * 40,
            },
        ),
        _event("plan_redeclared", {"reason": "requirements changed"}),
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="external",
        required_checklist=("correctness",),
    ) is None


def test_partial_rejection_invalidates_older_approval() -> None:
    events = [
        _event(
            "review_verdict_recorded",
            {
                "reviewer": "code-reviewer",
                "source": "subagent",
                "outcome": "approved",
                "reviewed_head": "a" * 40,
            },
        ),
        _event(
            "code_review_failed",
            {
                "reviewer": "code-reviewer",
                "source": "subagent",
                "reviewed_head": "b" * 40,
                "verdict": {"checklist": [{"item": "correctness", "status": "fail"}]},
            },
        ),
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="subagent",
        required_checklist=("correctness", "tests"),
    ) is None


def test_old_result_without_reviewed_head_cannot_establish_baseline() -> None:
    events = [
        _event(
            "review_verdict_recorded",
            {"reviewer": "code-reviewer", "source": "external", "outcome": "approved"},
        )
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="external",
        required_checklist=(),
    ) is None


def test_source_results_are_isolated() -> None:
    events = [
        _event(
            "review_verdict_recorded",
            {
                "reviewer": "code-reviewer",
                "source": "external",
                "outcome": "approved",
                "reviewed_head": "a" * 40,
            },
        ),
        _event(
            "code_review_failed",
            {"reviewer": "code-reviewer", "source": "subagent"},
        ),
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="external",
        required_checklist=("correctness",),
    ) == "a" * 40


def test_approval_milestone_does_not_hide_source_result() -> None:
    events = [
        _event(
            "review_verdict_recorded",
            {
                "reviewer": "code-reviewer",
                "source": "external",
                "outcome": "approved",
                "reviewed_head": "c" * 40,
            },
        ),
        _event(
            "code_review_passed",
            {
                "reviewer": "code-reviewer",
                "source": "external",
                "reviewed_head": "c" * 40,
                "independent_sources": ["subagent", "external"],
            },
        ),
    ]

    assert resolve_source_baseline(
        events,
        reviewer="code-reviewer",
        source="external",
        required_checklist=("correctness",),
    ) == "c" * 40


def test_unresolvable_source_baseline_falls_back_to_full_change(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "src/a.py")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "change")
    event = _event(
        "code_review_failed",
        {
            "reviewer": "code-reviewer",
            "source": "external",
            "reviewed_head": "f" * 40,
            "verdict": {
                "checklist": [{"item": "correctness", "status": "fail"}],
                "findings": [
                    {
                        "id": "RC-001",
                        "severity": "major",
                        "file": "src/a.py",
                        "summary": "Historical baseline cannot be resolved.",
                    }
                ],
            },
        },
    )
    profile = ReviewProducerProfile(
        source="external",
        protocol="codex-cli",
        model="review-model",
        cost_class="standard",
        agent_options={"reasoning_effort": "medium"},
    )
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={
            "external": ReviewerSourceGovernance(
                name="external", kind="automated"
            )
        },
        roles={
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("external",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )
        },
        require_distinct_model_families=False,
    )
    bundle = {
        "base": "main",
        "change": "change",
        "reviewer": "code-reviewer",
        "bundle_digest": "digest",
        "checklist": ["correctness"],
        "spec_path": "",
        "plan_path": "",
    }

    compiled = compile_review_contract(
        tmp_path,
        bundle=bundle,
        governance=governance,
        profiles={"external": profile},
        events=[event],
        declared=["src/"],
    )

    assert compiled["assignments"][0]["inspection"]["mode"] == "full-change"
    assert compiled["assignments"][0]["inspection"]["files"] == ["src/a.py"]
    prompt = compiled["assignments"][0]["prompt"]
    assert '"id":"RC-001"' in prompt
    assert '"summary":"Historical baseline cannot be resolved."' in prompt


def test_imported_finding_from_execution_failed_round_is_in_next_prompt(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "src/a.py")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "change")
    events = [
        _event(
            "review_result_imported",
            {
                "reviewer": "code-reviewer",
                "source": "external",
                "target_head": "f" * 40,
                "verdict": {
                    "scope_sufficient": True,
                    "checklist": [{"item": "correctness", "status": "fail"}],
                    "findings": [
                        {
                            "id": "external/run-1/RC-001",
                            "severity": "major",
                            "file": "src/a.py",
                            "summary": "Imported finding survived a peer execution failure.",
                        }
                    ],
                    "prior_findings": [],
                },
            },
        ),
        _event(
            "review_round_closed",
            {
                "reviewer": "code-reviewer",
                "round_id": "round-1",
                "outcome": "execution_failed",
            },
        ),
    ]
    profile = ReviewProducerProfile(
        source="external",
        protocol="codex-cli",
        model="review-model",
        cost_class="standard",
        agent_options={"reasoning_effort": "medium"},
    )
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={
            "external": ReviewerSourceGovernance(
                name="external", kind="automated"
            )
        },
        roles={
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("external",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )
        },
        require_distinct_model_families=False,
    )
    bundle = {
        "base": "main",
        "change": "change",
        "reviewer": "code-reviewer",
        "bundle_digest": "digest",
        "checklist": ["correctness"],
        "spec_path": "",
        "plan_path": "",
    }

    compiled = compile_review_contract(
        tmp_path,
        bundle=bundle,
        governance=governance,
        profiles={"external": profile},
        events=events,
        declared=["src/"],
    )

    prompt = compiled["assignments"][0]["prompt"]
    assert '"id":"external/run-1/RC-001"' in prompt
    assert "Imported finding survived a peer execution failure." in prompt


def _rebased_repo_with_orphaned_plan_head(tmp_path: Path, *, plan_body: str) -> str:
    """Build a repo where the plan_approved head is NOT an ancestor of HEAD
    (a divergent rebase), returning that orphaned approved-plan SHA. ``plan_body``
    is the plan artifact content written on the CURRENT (target) branch."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    plan = tmp_path / "docs" / "plan.md"
    plan.write_text("---\nchange: change\nstage: plan\n---\n\noriginal plan\n")
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    # approved-plan line: implementation commit that plan review approved.
    _git(tmp_path, "checkout", "-qb", "approved")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "impl")
    approved_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    # divergent target line from base (approved_head is not an ancestor of it).
    _git(tmp_path, "checkout", "-qb", "target", "main")
    plan.write_text(f"---\nchange: change\nstage: plan\n---\n\n{plan_body}\n")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "impl-rebased")
    return approved_head


def _code_reviewer_inputs() -> tuple[ReviewGovernance, dict[str, object]]:
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={"external": ReviewerSourceGovernance(name="external", kind="automated")},
        roles={
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("external",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )
        },
        require_distinct_model_families=False,
    )
    bundle = {
        "base": "main",
        "change": "change",
        "reviewer": "code-reviewer",
        "bundle_digest": "digest",
        "checklist": ["correctness"],
        "spec_path": "",
        "plan_path": "docs/plan.md",
    }
    return governance, bundle


def test_rebased_plan_head_with_identical_plan_does_not_block_prepare(
    tmp_path: Path,
) -> None:
    """Regression (PR#79 finding #8): a routine rebase orphans the approved plan
    head; when the plan artifact content is unchanged, code-review prepare must
    still compile instead of failing with an unfollowable 'not an ancestor'."""
    approved_head = _rebased_repo_with_orphaned_plan_head(tmp_path, plan_body="original plan")
    governance, bundle = _code_reviewer_inputs()
    profile = ReviewProducerProfile(
        source="external", protocol="codex-cli", model="m",
        cost_class="standard", agent_options={},
    )
    events = [_event("plan_approved", {"reviewed_head": approved_head})]

    compiled = compile_review_contract(
        tmp_path, bundle=bundle, governance=governance,
        profiles={"external": profile}, events=events, declared=["docs/", "src/"],
    )
    assert compiled["assignments"][0]["source"] == "external"


def test_rebased_plan_head_with_changed_plan_still_requires_redeclare(
    tmp_path: Path,
) -> None:
    """The drift guard still fires on a genuine plan change across the rebase."""
    approved_head = _rebased_repo_with_orphaned_plan_head(tmp_path, plan_body="REWRITTEN plan")
    governance, bundle = _code_reviewer_inputs()
    profile = ReviewProducerProfile(
        source="external", protocol="codex-cli", model="m",
        cost_class="standard", agent_options={},
    )
    events = [_event("plan_approved", {"reviewed_head": approved_head})]

    with pytest.raises(ReviewContractError, match="plan redeclaration"):
        compile_review_contract(
            tmp_path, bundle=bundle, governance=governance,
            profiles={"external": profile}, events=events, declared=["docs/", "src/"],
        )


def test_unresolvable_plan_head_warns_instead_of_silent_skip(tmp_path: Path) -> None:
    """Regression (PR#79 #8 follow-up): when the approved plan head is unresolvable
    (gc'd/rewritten), prepare must not crash AND must surface a visible warning
    that the plan-drift guard was skipped — never a silent fail-open."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "docs" / "plan.md").write_text(
        "---\nchange: change\nstage: plan\n---\n\nplan\n"
    )
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "impl")

    governance, bundle = _code_reviewer_inputs()
    profile = ReviewProducerProfile(
        source="external", protocol="codex-cli", model="m",
        cost_class="standard", agent_options={},
    )
    # A reviewed_head that does not resolve in this repo → GitScopeError path.
    events = [_event("plan_approved", {"reviewed_head": "f" * 40})]

    compiled = compile_review_contract(
        tmp_path, bundle=bundle, governance=governance,
        profiles={"external": profile}, events=events, declared=["docs/", "src/"],
    )
    warnings = compiled["warnings"]
    assert any("plan-drift guard skipped" in w or "unresolvable" in w for w in warnings), warnings


def test_prompt_documents_pass_with_open_finding_threshold(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "src/a.py")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "change")
    profile = ReviewProducerProfile(
        source="external",
        protocol="codex-cli",
        model="review-model",
        cost_class="standard",
        agent_options={"reasoning_effort": "medium"},
    )
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={
            "external": ReviewerSourceGovernance(name="external", kind="automated")
        },
        roles={
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("external",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )  # blocking_severity defaults to "major"
        },
        require_distinct_model_families=False,
    )
    bundle = {
        "base": "main",
        "change": "change",
        "reviewer": "code-reviewer",
        "bundle_digest": "digest",
        "checklist": ["correctness"],
        "spec_path": "",
        "plan_path": "",
    }

    compiled = compile_review_contract(
        tmp_path,
        bundle=bundle,
        governance=governance,
        profiles={"external": profile},
        events=[],
        declared=["src/"],
    )

    prompt = compiled["assignments"][0]["prompt"]
    assert "at or above `major`" in prompt
    assert "passes with the finding left open" in prompt


def test_plan_reviewer_prompt_omits_pass_with_open_finding(tmp_path: Path) -> None:
    # Pass-with-open-finding is code-review-only (plan findings are not tracked
    # or surfaced), so the plan-reviewer prompt must not claim it.
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "src/a.py")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "change")
    profile = ReviewProducerProfile(
        source="external",
        protocol="codex-cli",
        model="review-model",
        cost_class="standard",
        agent_options={"reasoning_effort": "medium"},
    )
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={
            "external": ReviewerSourceGovernance(name="external", kind="automated")
        },
        roles={
            "plan-reviewer": ReviewerRoleGovernance(
                reviewer="plan-reviewer",
                participants=("external",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )
        },
        require_distinct_model_families=False,
    )
    bundle = {
        "base": "main",
        "change": "change",
        "reviewer": "plan-reviewer",
        "bundle_digest": "digest",
        "checklist": ["correctness"],
        "spec_path": "",
        "plan_path": "",
    }

    compiled = compile_review_contract(
        tmp_path,
        bundle=bundle,
        governance=governance,
        profiles={"external": profile},
        events=[],
        declared=["src/"],
    )

    prompt = compiled["assignments"][0]["prompt"]
    assert "passes with the finding left open" not in prompt
    assert "blocking severity" not in prompt.lower()
