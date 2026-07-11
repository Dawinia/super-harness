"""Behavior tests for compiling per-source review baselines and contracts."""
from __future__ import annotations

import subprocess
from pathlib import Path

from super_harness.core.events import Actor, Event
from super_harness.engineering.review_contract import (
    compile_review_contract,
    resolve_source_baseline,
)
from super_harness.engineering.reviewer_policy import (
    ReviewerIndependencePolicy,
    ReviewerSourcePolicy,
)


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
    profile = ReviewerSourcePolicy(
        instructions="Review.",
        agent="codex",
        context="incremental",
        agent_options={"reasoning_effort": "medium"},
    )
    policy = ReviewerIndependencePolicy(
        reviewer="code-reviewer",
        strategy="subagent",
        min_independent=1,
        allowed_sources=("external",),
        source_instructions={"external": "Review."},
        source_profiles={"external": profile},
        participants=("external",),
    )
    bundle = {
        "base": "main",
        "change": "change",
        "bundle_digest": "digest",
        "checklist": ["correctness"],
        "spec_path": "",
        "plan_path": "",
    }

    compiled = compile_review_contract(
        tmp_path,
        bundle=bundle,
        policy=policy,
        events=[event],
        declared=["src/"],
    )

    assert compiled["assignments"][0]["inspection"]["mode"] == "full-change"
    assert compiled["assignments"][0]["inspection"]["files"] == ["src/a.py"]
    prompt = compiled["assignments"][0]["prompt"]
    assert '"id":"RC-001"' in prompt
    assert '"summary":"Historical baseline cannot be resolved."' in prompt
