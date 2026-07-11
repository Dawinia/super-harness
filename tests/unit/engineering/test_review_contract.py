"""Behavior tests for compiling per-source review baselines and contracts."""
from __future__ import annotations

from super_harness.core.events import Actor, Event
from super_harness.engineering.review_contract import resolve_source_baseline


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
