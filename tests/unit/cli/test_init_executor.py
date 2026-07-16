from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields

import pytest

from super_harness.cli.init_executor import (
    InitExecutionResult,
    InitExecutor,
    InitOperationError,
    InitOperationResult,
    InitOperations,
    InitStepEvent,
    StepState,
)
from super_harness.cli.init_plan import (
    GitHubDecision,
    HarnessState,
    InitPlan,
    ReviewWrite,
)
from super_harness.cli.init_ui import NonInteractiveInitUI

EXPECTED_STEPS = (
    "scaffold",
    "skeleton_config",
    "review_config",
    "agent_integrations",
    "agents_md",
    "gitignore",
    "github",
)


def _plan(*, github: bool = True) -> InitPlan:
    return InitPlan(
        harness_state=HarnessState.ABSENT,
        review_write=ReviewWrite.UPDATE,
        integrations=("codex",),
        review_producers=("codex-cli",),
        review_models={"codex": "gpt-review"},
        github_decision=GitHubDecision.CREATE if github else GitHubDecision.SKIP,
        file_actions=(),
    )


def _operations(
    calls: list[str],
    overrides: dict[str, Callable[[InitPlan], InitOperationResult]] | None = None,
) -> InitOperations:
    overrides = overrides or {}

    def operation(step_id: str) -> Callable[[InitPlan], InitOperationResult]:
        def run(plan: InitPlan) -> InitOperationResult:
            assert isinstance(plan, InitPlan)
            calls.append(step_id)
            override = overrides.get(step_id)
            if override is not None:
                return override(plan)
            return InitOperationResult(detail=f"completed {step_id}")

        return run

    return InitOperations(**{step_id: operation(step_id) for step_id in EXPECTED_STEPS})


def _states(result: InitExecutionResult) -> tuple[tuple[str, StepState], ...]:
    return tuple((event.step_id, event.state) for event in result.ledger)


def test_success_emits_started_then_succeeded_in_stable_order() -> None:
    calls: list[str] = []
    observed: list[InitStepEvent] = []

    result = InitExecutor(_operations(calls)).apply(_plan(), observed.append)

    assert result.success is True
    assert result.exit_code == 0
    assert calls == list(EXPECTED_STEPS)
    assert _states(result) == tuple(
        pair
        for step_id in EXPECTED_STEPS
        for pair in ((step_id, StepState.STARTED), (step_id, StepState.SUCCEEDED))
    )
    assert observed == list(result.ledger)
    assert all(seen is recorded for seen, recorded in zip(observed, result.ledger, strict=True))
    assert result.next_command == "super-harness status"
    assert result.recovery_command is None


def test_inactive_github_step_is_absent_from_calls_and_ledger() -> None:
    calls: list[str] = []

    result = InitExecutor(_operations(calls)).apply(_plan(github=False))

    assert result.success is True
    assert calls == list(EXPECTED_STEPS[:-1])
    assert {event.step_id for event in result.ledger} == set(EXPECTED_STEPS[:-1])
    assert all(event.step_id != "github" for event in result.ledger)


def test_warning_emits_warned_and_execution_continues() -> None:
    calls: list[str] = []

    result = InitExecutor(
        _operations(
            calls,
            {
                "review_config": lambda _plan: InitOperationResult(
                    detail="kept existing local profile", warned=True
                )
            },
        )
    ).apply(_plan())

    review_events = tuple(event for event in result.ledger if event.step_id == "review_config")
    assert tuple(event.state for event in review_events) == (
        StepState.STARTED,
        StepState.WARNED,
    )
    assert review_events[-1].detail == "kept existing local profile"
    assert result.success is True
    assert calls == list(EXPECTED_STEPS)


def test_domain_failure_retains_completed_ledger_and_domain_recovery_data() -> None:
    calls: list[str] = []

    def fail(_plan: InitPlan) -> InitOperationResult:
        raise InitOperationError(
            "gh authentication expired",
            exit_code=4,
            hint="Run `gh auth login`.",
            recovery_command="gh auth login && super-harness init --force",
        )

    result = InitExecutor(_operations(calls, {"review_config": fail})).apply(_plan())

    assert calls == list(EXPECTED_STEPS[:3])
    assert _states(result) == (
        ("scaffold", StepState.STARTED),
        ("scaffold", StepState.SUCCEEDED),
        ("skeleton_config", StepState.STARTED),
        ("skeleton_config", StepState.SUCCEEDED),
        ("review_config", StepState.STARTED),
        ("review_config", StepState.FAILED),
    )
    assert result.success is False
    assert result.exit_code == 4
    assert result.failed_step_id == "review_config"
    assert result.interrupted_step_id is None
    assert result.message == "gh authentication expired"
    assert result.hint == "Run `gh auth login`."
    assert result.recovery_command == "gh auth login && super-harness init --force"
    assert result.next_command is None


def test_unexpected_exception_returns_safe_generic_failure_without_traceback() -> None:
    calls: list[str] = []

    def fail(_plan: InitPlan) -> InitOperationResult:
        raise RuntimeError("secret token and implementation traceback details")

    result = InitExecutor(_operations(calls, {"scaffold": fail})).apply(_plan())

    assert calls == ["scaffold"]
    assert result.success is False
    assert result.exit_code == 1
    assert result.failed_step_id == "scaffold"
    assert result.message == "Unexpected failure while running scaffold."
    assert "secret token" not in result.message
    assert "Traceback" not in result.message
    assert result.ledger[-1] == InitStepEvent(
        "scaffold", StepState.FAILED, "Unexpected failure while running scaffold."
    )


def test_keyboard_interrupt_during_fourth_step_keeps_completed_ledger() -> None:
    calls: list[str] = []

    def interrupt(_plan: InitPlan) -> InitOperationResult:
        raise KeyboardInterrupt

    result = InitExecutor(_operations(calls, {"agent_integrations": interrupt})).apply(_plan())

    assert calls == list(EXPECTED_STEPS[:4])
    assert _states(result) == (
        *(
            pair
            for step_id in EXPECTED_STEPS[:3]
            for pair in ((step_id, StepState.STARTED), (step_id, StepState.SUCCEEDED))
        ),
        ("agent_integrations", StepState.STARTED),
        ("agent_integrations", StepState.INTERRUPTED),
    )
    assert result.success is False
    assert result.exit_code == 1
    assert result.failed_step_id is None
    assert result.interrupted_step_id == "agent_integrations"
    assert result.recovery_command == "super-harness init --force"


def test_event_sink_failure_is_isolated_without_rerunning_operations() -> None:
    calls: list[str] = []
    observed: list[InitStepEvent] = []
    raised = False

    def flaky_sink(event: InitStepEvent) -> None:
        nonlocal raised
        observed.append(event)
        if event.step_id == "skeleton_config" and event.state is StepState.STARTED and not raised:
            raised = True
            raise RuntimeError("presentation unavailable")

    result = InitExecutor(_operations(calls)).apply(_plan(), flaky_sink)

    assert result.success is True
    assert calls == list(EXPECTED_STEPS)
    assert observed == list(result.ledger)


def test_result_and_ledger_are_deeply_immutable() -> None:
    result = InitExecutor(_operations([])).apply(_plan(github=False))

    with pytest.raises(FrozenInstanceError):
        result.success = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.ledger[0].detail = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.ledger.append(  # type: ignore[attr-defined]
            InitStepEvent("extra", StepState.STARTED, "not allowed")
        )


def test_executor_has_no_prompt_render_or_rollback_boundary() -> None:
    import super_harness.cli.init_executor as executor_module

    source = inspect.getsource(executor_module).lower()
    forbidden = ("input(", "click.", "questionary", "render_", "events.jsonl", "rollback")

    assert all(token not in source for token in forbidden)
    assert all("rollback" not in field.name for field in fields(InitOperations))
    assert "rollback" not in inspect.signature(InitExecutor.apply).parameters


def test_step_events_render_through_existing_structural_ui_boundary() -> None:
    lines: list[str] = []
    ui = NonInteractiveInitUI(
        input_fn=lambda _prompt: pytest.fail("executor rendering must not prompt"),
        output_fn=lines.append,
        unicode=False,
        width=80,
    )
    event = InitStepEvent("scaffold", StepState.SUCCEEDED, "created .harness")

    ui.render_event(event)

    assert lines == ["OK scaffold: created .harness"]
