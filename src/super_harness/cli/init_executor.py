"""Prompt-free execution of an immutable :class:`InitPlan`.

The executor owns only operation order and truthful process-local presentation
events.  Inactive optional operations are absent from the ledger; specifically,
``github`` is omitted when the plan's GitHub decision is ``SKIP``.  Event-sink
exceptions are isolated from execution: the authoritative ledger is recorded
first, operations run at most once, and later events continue to be offered to
the sink.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from super_harness.cli.init_plan import GitHubDecision, InitPlan
from super_harness.exit_codes import EXIT_GENERIC, EXIT_OK


class StepState(str, Enum):
    """Closed presentation state for one init operation."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    WARNED = "warned"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class InitStepEvent:
    """One immutable process-local executor event."""

    step_id: str
    state: StepState
    detail: str


@dataclass(frozen=True)
class InitOperationResult:
    """Truthful detail returned by an injected operation."""

    detail: str
    warned: bool = False


class InitOperation(Protocol):
    """A named, prompt-free operation over the already resolved plan."""

    def __call__(self, plan: InitPlan, /) -> InitOperationResult: ...


@dataclass(frozen=True)
class InitOperations:
    """Named injected operations in their stable execution order."""

    scaffold: InitOperation
    skeleton_config: InitOperation
    review_config: InitOperation
    agent_integrations: InitOperation
    agents_md: InitOperation
    gitignore: InitOperation
    github: InitOperation

    def ordered(self) -> tuple[tuple[str, InitOperation], ...]:
        """Return the stable public step identifiers and their operations."""

        return (
            ("scaffold", self.scaffold),
            ("skeleton_config", self.skeleton_config),
            ("review_config", self.review_config),
            ("agent_integrations", self.agent_integrations),
            ("agents_md", self.agents_md),
            ("gitignore", self.gitignore),
            ("github", self.github),
        )


class InitOperationError(RuntimeError):
    """Typed operation failure carrying existing CLI error semantics."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int,
        hint: str | None = None,
        recovery_command: str | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.hint = hint
        self.recovery_command = recovery_command


@dataclass(frozen=True)
class InitExecutionResult:
    """Deeply immutable final result plus the complete ordered event ledger."""

    ledger: tuple[InitStepEvent, ...]
    success: bool
    exit_code: int
    failed_step_id: str | None
    interrupted_step_id: str | None
    message: str | None
    hint: str | None
    next_command: str | None
    recovery_command: str | None
    elapsed_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "ledger", tuple(self.ledger))
        object.__setattr__(self, "elapsed_ms", max(0, self.elapsed_ms))


EventSink = Callable[[InitStepEvent], None]

_START_DETAILS = {
    "scaffold": "Scaffolding .harness.",
    "skeleton_config": "Writing skeleton configuration.",
    "review_config": "Configuring review sources.",
    "agent_integrations": "Configuring agent integrations.",
    "agents_md": "Updating AGENTS.md.",
    "gitignore": "Updating .gitignore.",
    "github": "Applying GitHub setup.",
}


class InitExecutor:
    """Run resolved init operations once, without prompting or rendering."""

    def __init__(
        self,
        operations: InitOperations,
        *,
        success_next_command: str = "super-harness status",
        default_recovery_command: str = "super-harness init --force",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._operations = operations
        self._success_next_command = success_next_command
        self._default_recovery_command = default_recovery_command
        self._monotonic = monotonic

    def apply(
        self,
        plan: InitPlan,
        event_sink: EventSink | None = None,
    ) -> InitExecutionResult:
        """Apply active operations and return the authoritative partial ledger."""

        started_at = self._monotonic()
        ledger: list[InitStepEvent] = []
        for step_id, operation in self._operations.ordered():
            if step_id == "github" and plan.github_decision is GitHubDecision.SKIP:
                continue

            self._emit(
                ledger,
                event_sink,
                InitStepEvent(step_id, StepState.STARTED, _START_DETAILS[step_id]),
            )
            try:
                outcome = operation(plan)
            except KeyboardInterrupt:
                detail = f"Interrupted while running {step_id}."
                self._emit(
                    ledger,
                    event_sink,
                    InitStepEvent(step_id, StepState.INTERRUPTED, detail),
                )
                return InitExecutionResult(
                    ledger=tuple(ledger),
                    success=False,
                    exit_code=EXIT_GENERIC,
                    failed_step_id=None,
                    interrupted_step_id=step_id,
                    message=detail,
                    hint="Completed steps were kept; correct the issue before retrying.",
                    next_command=None,
                    recovery_command=self._default_recovery_command,
                    elapsed_ms=self._elapsed_ms(started_at),
                )
            except InitOperationError as error:
                detail = str(error)
                self._emit(
                    ledger,
                    event_sink,
                    InitStepEvent(step_id, StepState.FAILED, detail),
                )
                return InitExecutionResult(
                    ledger=tuple(ledger),
                    success=False,
                    exit_code=error.exit_code,
                    failed_step_id=step_id,
                    interrupted_step_id=None,
                    message=detail,
                    hint=error.hint,
                    next_command=None,
                    recovery_command=(error.recovery_command or self._default_recovery_command),
                    elapsed_ms=self._elapsed_ms(started_at),
                )
            except Exception:
                detail = f"Unexpected failure while running {step_id}."
                self._emit(
                    ledger,
                    event_sink,
                    InitStepEvent(step_id, StepState.FAILED, detail),
                )
                return InitExecutionResult(
                    ledger=tuple(ledger),
                    success=False,
                    exit_code=EXIT_GENERIC,
                    failed_step_id=step_id,
                    interrupted_step_id=None,
                    message=detail,
                    hint="Inspect the named operation, correct the issue, and retry.",
                    next_command=None,
                    recovery_command=self._default_recovery_command,
                    elapsed_ms=self._elapsed_ms(started_at),
                )

            state = StepState.WARNED if outcome.warned else StepState.SUCCEEDED
            self._emit(
                ledger,
                event_sink,
                InitStepEvent(step_id, state, outcome.detail),
            )

        return InitExecutionResult(
            ledger=tuple(ledger),
            success=True,
            exit_code=EXIT_OK,
            failed_step_id=None,
            interrupted_step_id=None,
            message=None,
            hint=None,
            next_command=self._success_next_command,
            recovery_command=None,
            elapsed_ms=self._elapsed_ms(started_at),
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((self._monotonic() - started_at) * 1_000))

    @staticmethod
    def _emit(
        ledger: list[InitStepEvent],
        event_sink: EventSink | None,
        event: InitStepEvent,
    ) -> None:
        ledger.append(event)
        if event_sink is None:
            return
        try:
            event_sink(event)
        except Exception:
            # Presentation is best-effort. The ledger remains authoritative and
            # the operation must never be repeated because its sink failed.
            pass
