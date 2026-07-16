"""Terminal capability selection and deterministic plain init interfaces."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import IO, Protocol, cast

import questionary
from rich.console import Console
from rich.text import Text

from super_harness.cli.init_plan import (
    FileAction,
    InitChoices,
    InitPlan,
    InitPreflight,
    InitRequest,
    InteractionMode,
    ReviewWrite,
    build_init_plan,
)

TextInput = Callable[[str], str]
TextOutput = Callable[[str], None]


@dataclass(frozen=True)
class TerminalCapabilities:
    """Input and output capabilities selected once at the CLI boundary."""

    mode: InteractionMode
    color: bool
    unicode: bool
    width: int


def _supports_unicode(encoding: str | None) -> bool:
    if not encoding:
        return False
    try:
        "✓✗●│…".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def detect_terminal_capabilities(
    *,
    stdin_tty: bool,
    stdout_tty: bool,
    term: str | None,
    no_color: bool,
    encoding: str | None,
    width: int | None,
) -> TerminalCapabilities:
    """Select interaction and rendering capabilities from injected stream facts."""

    normalized_term = (term or "").strip().lower()
    cursor_limited = normalized_term in {"dumb", "unknown"} or normalized_term.startswith("dumb-")
    if not stdin_tty:
        mode = InteractionMode.NON_INTERACTIVE
    elif stdout_tty and not cursor_limited:
        mode = InteractionMode.GUIDED
    else:
        mode = InteractionMode.LINE

    normalized_width = 80 if width is None else max(1, width)
    return TerminalCapabilities(
        mode=mode,
        color=mode is InteractionMode.GUIDED and not no_color,
        unicode=_supports_unicode(encoding),
        width=normalized_width,
    )


def detect_runtime_terminal_capabilities(
    stdin: IO[str],
    stdout: IO[str],
    environ: Mapping[str, str],
    *,
    width: int | None = None,
) -> TerminalCapabilities:
    """Capture real streams/environment once while remaining directly injectable."""

    return detect_terminal_capabilities(
        stdin_tty=stdin.isatty(),
        stdout_tty=stdout.isatty(),
        term=environ.get("TERM"),
        no_color="NO_COLOR" in environ,
        encoding=getattr(stdout, "encoding", None),
        width=width if width is not None else shutil.get_terminal_size((80, 24)).columns,
    )


class ChoiceCollectionDecision(str, Enum):
    """Closed outcomes from collecting configuration values."""

    REVIEW = "review"
    CANCEL = "cancel"


class ReviewDecision(str, Enum):
    """Closed decisions available at the final pre-write boundary."""

    CONFIRM = "confirm"
    BACK = "back"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ChoiceCollectionResult:
    """Immutable handoff from UI choice collection to plan construction."""

    decision: ChoiceCollectionDecision
    choices: InitChoices


class StepRenderState(str, Enum):
    """Executor-neutral states understood by plain step rendering."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    WARNED = "warned"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class StepRenderEvent:
    """Minimal immutable value usable before the executor module exists."""

    step_id: str
    state: StepRenderState
    detail: str


class StepEventLike(Protocol):
    """Structural render boundary that future executor events can satisfy."""

    @property
    def step_id(self) -> str: ...

    @property
    def state(self) -> object: ...

    @property
    def detail(self) -> str: ...


class ExecutionResultLike(Protocol):
    @property
    def success(self) -> bool: ...

    @property
    def message(self) -> str | None: ...

    @property
    def next_command(self) -> str | None: ...

    @property
    def recovery_command(self) -> str | None: ...


@dataclass(frozen=True)
class _Option:
    value: str
    label: str
    source: str | None = None


_INTEGRATIONS = (
    _Option("codex", "Codex"),
    _Option("claude-code", "Claude Code"),
)
_REVIEW_PRODUCERS = (
    _Option("codex-cli", "Codex CLI", "codex"),
    _Option("claude-cli", "Claude CLI", "claude"),
)
_NARROW_WIDTH = 60


class _CollectionCancelled(Exception):
    pass


@dataclass(frozen=True)
class GuidedPromptOption:
    """Library-neutral prompt option used at the Questionary boundary."""

    value: str
    title: str
    checked: bool = False
    disabled: str | None = None


class GuidedPromptAdapter(Protocol):
    def checkbox(
        self, message: str, choices: Sequence[GuidedPromptOption]
    ) -> tuple[str, ...] | None: ...
    def text(self, message: str, *, default: str | None = None) -> str | None: ...
    def select(
        self,
        message: str,
        choices: Sequence[GuidedPromptOption],
        *,
        default: str | None = None,
    ) -> str | None: ...


class QuestionaryPromptAdapter:
    """Translate library-neutral prompt values to and from Questionary."""

    @staticmethod
    def _choices(options: Sequence[GuidedPromptOption]) -> list[questionary.Choice]:
        return [
            questionary.Choice(
                option.title,
                value=option.value,
                checked=option.checked,
                disabled=option.disabled or None,
            )
            for option in options
        ]

    def checkbox(
        self, message: str, choices: Sequence[GuidedPromptOption]
    ) -> tuple[str, ...] | None:
        answer = questionary.checkbox(message, choices=self._choices(choices)).ask()
        return None if answer is None else tuple(cast(list[str], answer))

    def text(self, message: str, *, default: str | None = None) -> str | None:
        answer = questionary.text(message, default=default or "").ask()
        return None if answer is None else str(answer)

    def select(
        self,
        message: str,
        choices: Sequence[GuidedPromptOption],
        *,
        default: str | None = None,
    ) -> str | None:
        answer = questionary.select(message, choices=self._choices(choices), default=default).ask()
        return None if answer is None else str(answer)


class RailStage(str, Enum):
    PREFLIGHT = "preflight"
    CONFIGURATION = "configuration"
    REVIEW = "review"
    APPLY = "apply"
    OUTCOME = "outcome"


class RailState(str, Enum):
    PENDING = "pending"
    CURRENT = "current"
    COMPLETED = "completed"
    FAILED = "failed"


class GuidedRenderAdapter(Protocol):
    def render_stage(
        self,
        stage: RailStage,
        state: RailState,
        detail: str,
        *,
        secondary: str | None = None,
    ) -> None: ...
    def render_plan(self, plan: InitPlan) -> None: ...
    def render_validation(self, message: str) -> None: ...
    def render_event(self, event: StepEventLike) -> None: ...


_RAIL_GLYPHS = {
    True: dict(zip(RailState, ("◇", "◆", "●", "✗"), strict=True)),
    False: dict(zip(RailState, ("|", "+", "*", "x"), strict=True)),
}

_FILE_ACTION_HINTS = {
    FileAction.CREATE: "will be written during apply",
    FileAction.UPDATE: "will be written during apply",
    FileAction.PRESERVE: "will be left unchanged",
    FileAction.SKIP: "not part of this run",
}
_RAIL_STYLES = dict(zip(RailState, ("dim", "cyan", "green", "red"), strict=True))
_EVENT_GLYPHS = {
    True: dict(zip(StepRenderState, ("…", "✓", "!", "✗", "!"), strict=True)),
    False: dict(zip(StepRenderState, ("+", "*", "!", "x", "x"), strict=True)),
}


class RichGuidedRenderer:
    """Single-column Rich renderer that never acquires live terminal ownership."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        unicode: bool,
        color: bool,
        width: int,
    ) -> None:
        self._unicode = unicode
        self._color = color
        self._width = max(1, width)
        self._console = console or Console(color_system="auto" if color else None, width=width)

    def _print(self, value: str, *, style: str | None = None) -> None:
        self._console.print(
            Text(value, style=style if self._color and style else ""),
            overflow="fold",
            crop=False,
        )

    def render_stage(
        self,
        stage: RailStage,
        state: RailState,
        detail: str,
        *,
        secondary: str | None = None,
    ) -> None:
        self._print(
            f"{_RAIL_GLYPHS[self._unicode][state]}  {stage.value}: {detail}",
            style=_RAIL_STYLES[state],
        )
        if secondary is not None and self._width >= _NARROW_WIDTH:
            self._print(f"{'│' if self._unicode else '|'}  {secondary}", style="dim")

    def render_plan(self, plan: InitPlan) -> None:
        integrations = ", ".join(plan.integrations) if plan.integrations else "(none)"
        producers = ", ".join(plan.review_producers) if plan.review_producers else "(none)"
        self._print(f"|  Integrations: {integrations}")
        self._print(f"|  Review producers: {producers}")
        if plan.review_models:
            for source, model in plan.review_models.items():
                self._print(f"|  Model {source}: {model}")
        else:
            self._print("|  Review models: (none)")
        self._print(f"|  Review configuration: {plan.review_write.value}")
        self._print(f"|  GitHub setup: {plan.github_decision.value}")
        for action in plan.file_actions:
            self._print(f"|  File {action.action.value}: {action.path}")
            if self._width >= _NARROW_WIDTH:
                self._print(f"|    hint: {_FILE_ACTION_HINTS[action.action]}", style="dim")

    def render_validation(self, message: str) -> None:
        self._print(f"{'!' if self._unicode else 'x'}  {message}", style="yellow")

    def render_event(self, event: StepEventLike) -> None:
        state = event.state.value if isinstance(event.state, Enum) else str(event.state)
        glyphs = {key.value: value for key, value in _EVENT_GLYPHS[self._unicode].items()}
        self._print(f"{glyphs.get(state, '|')}  {event.step_id}: {event.detail}")


class WizardDecision(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


@dataclass(frozen=True)
class WizardResult:
    decision: WizardDecision
    plan: InitPlan | None

    @classmethod
    def confirmed(cls, plan: InitPlan) -> WizardResult:
        return cls(WizardDecision.CONFIRM, plan)

    @classmethod
    def cancelled(cls) -> WizardResult:
        return cls(WizardDecision.CANCEL, None)


_CANCEL = object()


def _interactive_initial_choices(
    request: InitRequest,
    preflight: InitPreflight,
    choices: InitChoices | None,
) -> InitChoices:
    initial = choices or InitChoices()
    if not request.force or initial.review_write is not None:
        return initial
    if preflight.review_config_error is not None:
        return initial
    models = dict(preflight.persisted_review_models)
    models.update(initial.review_models)
    return InitChoices(
        integrations=initial.integrations,
        review_write=ReviewWrite.UPDATE,
        review_producers=(
            initial.review_producers
            if initial.review_producers is not None
            else preflight.persisted_review_producers
        ),
        review_models=models,
        existing_files=initial.existing_files,
        github_decision=initial.github_decision,
        github_file_decisions=initial.github_file_decisions,
    )


class InteractiveInitUI:
    """Questionary input and Rich rail orchestration for capable terminals."""

    def __init__(
        self,
        *,
        prompt_adapter: GuidedPromptAdapter | None = None,
        renderer: GuidedRenderAdapter | None = None,
        unicode: bool = True,
        color: bool = True,
        width: int = 80,
    ) -> None:
        self._prompts = prompt_adapter or QuestionaryPromptAdapter()
        self._renderer = renderer or RichGuidedRenderer(unicode=unicode, color=color, width=width)

    @staticmethod
    def _integration_options(
        preflight: InitPreflight, defaults: frozenset[str]
    ) -> tuple[GuidedPromptOption, ...]:
        return tuple(
            GuidedPromptOption(
                option.value,
                f"{option.label}  "
                + (
                    "detected · recommended"
                    if option.value in preflight.detected_integrations
                    else "not detected"
                ),
                option.value in defaults,
            )
            for option in _INTEGRATIONS
        )

    @staticmethod
    def _producer_options(
        preflight: InitPreflight, defaults: frozenset[str]
    ) -> tuple[GuidedPromptOption, ...]:
        def create(option: _Option) -> GuidedPromptOption:
            available = option.value in preflight.available_review_producers
            status = (
                "detected · recommended"
                if option.value in preflight.detected_review_producers
                else "executable not found"
            )
            return GuidedPromptOption(
                option.value,
                f"{option.label}  {status}",
                available and option.value in defaults,
                None if available else "executable not found",
            )

        return tuple(create(option) for option in _REVIEW_PRODUCERS)

    def _collect_integrations(
        self, request: InitRequest, preflight: InitPreflight, initial: InitChoices
    ) -> tuple[str, ...] | None | object:
        if request.integrations:
            return initial.integrations
        if request.no_agent:
            return ()
        defaults = (
            frozenset(initial.integrations)
            if initial.integrations is not None
            else frozenset(preflight.detected_integrations)
        )
        answer = self._prompts.checkbox(
            "Coding-agent integrations", self._integration_options(preflight, defaults)
        )
        if answer is None:
            return _CANCEL
        allowed = {option.value for option in _INTEGRATIONS}
        return tuple(value for value in answer if value in allowed)

    def _collect_producers(
        self, request: InitRequest, preflight: InitPreflight, initial: InitChoices
    ) -> tuple[str, ...] | None | object:
        if request.review_producers:
            return initial.review_producers
        defaults = (
            frozenset(initial.review_producers)
            if initial.review_producers is not None
            else frozenset(preflight.detected_review_producers)
        )
        options = self._producer_options(preflight, defaults)
        if not any(option.disabled is None for option in options):
            self._renderer.render_validation(
                "No automated review producers are available; continuing without one."
            )
            return ()
        answer = self._prompts.checkbox("Automated review producers", options)
        if answer is None:
            return _CANCEL
        return tuple(value for value in answer if value in preflight.available_review_producers)

    def _collect_models(
        self,
        request: InitRequest,
        producers: tuple[str, ...] | None,
        initial: InitChoices,
    ) -> Mapping[str, str] | None:
        models = dict(initial.review_models)
        known = models | dict(request.review_models)
        options = {option.value: option for option in _REVIEW_PRODUCERS}
        for producer in request.review_producers or producers or ():
            option = options.get(producer)
            if option is None or option.source is None or option.source in known:
                continue
            while True:
                answer = self._prompts.text(f"Model for {option.label}", default=None)
                if answer is None:
                    return None
                if answer.strip():
                    models[option.source] = answer.strip()
                    known[option.source] = answer.strip()
                    break
                self._renderer.render_validation("A model is required.")
        return models

    def collect(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        initial_choices: InitChoices | None = None,
    ) -> ChoiceCollectionResult:
        initial = _interactive_initial_choices(request, preflight, initial_choices)
        self._renderer.render_stage(
            RailStage.CONFIGURATION, RailState.CURRENT, "Choose integrations and reviews"
        )
        if preflight.review_config_error is not None and initial.review_write is None:
            reset = self._prompts.select(
                "Existing review configuration is invalid. Reset it?",
                (
                    GuidedPromptOption("reset", "Reset review configuration", True),
                    GuidedPromptOption("cancel", "Cancel setup"),
                ),
                default="reset",
            )
            if reset != "reset":
                return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
            initial = InitChoices(
                integrations=initial.integrations,
                review_write=ReviewWrite.RESET,
                review_producers=(),
                review_models={},
                existing_files=initial.existing_files,
                github_decision=initial.github_decision,
                github_file_decisions=initial.github_file_decisions,
            )
        integrations = self._collect_integrations(request, preflight, initial)
        if integrations is _CANCEL:
            return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
        producers = self._collect_producers(request, preflight, initial)
        if producers is _CANCEL:
            return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
        typed_integrations = cast(tuple[str, ...] | None, integrations)
        typed_producers = cast(tuple[str, ...] | None, producers)
        models = self._collect_models(request, typed_producers, initial)
        if models is None:
            return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
        choices = InitChoices(
            integrations=typed_integrations,
            review_write=initial.review_write,
            review_producers=typed_producers,
            review_models=models,
            existing_files=initial.existing_files,
            github_decision=initial.github_decision,
            github_file_decisions=initial.github_file_decisions,
        )
        self._renderer.render_stage(
            RailStage.CONFIGURATION, RailState.COMPLETED, "Configuration collected"
        )
        return ChoiceCollectionResult(ChoiceCollectionDecision.REVIEW, choices)

    def review(self, plan: InitPlan, *, assume_yes: bool = False) -> ReviewDecision:
        self._renderer.render_stage(RailStage.REVIEW, RailState.CURRENT, "Review planned setup")
        self._renderer.render_plan(plan)
        if assume_yes:
            return ReviewDecision.CONFIRM
        choices = (
            GuidedPromptOption("confirm", "Confirm and continue", True),
            GuidedPromptOption("back", "Back to configuration"),
            GuidedPromptOption("cancel", "Cancel setup"),
        )
        while True:
            answer = self._prompts.select("Apply this plan?", choices, default="confirm")
            if answer is None:
                return ReviewDecision.CANCEL
            try:
                return ReviewDecision(answer)
            except ValueError:
                self._renderer.render_validation("Choose confirm, back, or cancel.")

    def prepare_plan(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        assume_yes: bool | None = None,
        initial_choices: InitChoices | None = None,
    ) -> WizardResult:
        self._renderer.render_stage(
            RailStage.PREFLIGHT,
            RailState.COMPLETED,
            f"Inspected {request.workspace}",
            secondary="Detection is read-only",
        )
        choices = initial_choices
        effective_assume_yes = request.assume_yes if assume_yes is None else assume_yes
        while True:
            collection = self.collect(request, preflight, initial_choices=choices)
            if collection.decision is ChoiceCollectionDecision.CANCEL:
                return WizardResult.cancelled()
            choices = collection.choices
            plan = build_init_plan(request, preflight, choices)
            decision = self.review(plan, assume_yes=effective_assume_yes)
            if decision is ReviewDecision.BACK:
                continue
            if decision is ReviewDecision.CANCEL:
                return WizardResult.cancelled()
            self._renderer.render_stage(RailStage.REVIEW, RailState.COMPLETED, "Plan confirmed")
            self._renderer.render_stage(RailStage.APPLY, RailState.PENDING, "Ready for executor")
            self._renderer.render_stage(
                RailStage.OUTCOME, RailState.PENDING, "Pending apply outcome"
            )
            return WizardResult.confirmed(plan)

    def run(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        assume_yes: bool = False,
        initial_choices: InitChoices | None = None,
    ) -> WizardResult:
        result = self.prepare_plan(
            request,
            preflight,
            assume_yes=assume_yes,
            initial_choices=initial_choices,
        )
        if result.decision is WizardDecision.CANCEL:
            self._render_cancelled()
        return result

    def _render_cancelled(self) -> None:
        self._renderer.render_stage(RailStage.APPLY, RailState.PENDING, "No writes started")
        self._renderer.render_stage(RailStage.OUTCOME, RailState.COMPLETED, "Setup cancelled")

    def render_cancelled(self) -> None:
        self._render_cancelled()

    def render_plan(self, plan: InitPlan) -> None:
        self._renderer.render_plan(plan)

    def render_event(self, event: StepEventLike) -> None:
        self._renderer.render_event(event)

    def on_step(self, event: StepEventLike) -> None:
        self.render_event(event)

    def render_outcome(self, result: ExecutionResultLike) -> None:
        state = RailState.COMPLETED if result.success else RailState.FAILED
        detail = "Setup complete" if result.success else (result.message or "Setup failed")
        self._renderer.render_stage(RailStage.OUTCOME, state, detail)


class _PlainInitUI:
    """Shared deterministic renderer with no cursor or ANSI behavior."""

    def __init__(
        self,
        *,
        input_fn: TextInput,
        output_fn: TextOutput,
        unicode: bool,
        width: int,
        color: bool = False,
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self._unicode = unicode
        self._width = max(1, width)
        # Kept as an explicit capability for API symmetry. Plain rendering never emits ANSI.
        self._color = color

    def collect(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        initial_choices: InitChoices | None = None,
    ) -> ChoiceCollectionResult:
        raise NotImplementedError

    def review(self, plan: InitPlan, *, assume_yes: bool = False) -> ReviewDecision:
        raise NotImplementedError

    def render_plan(self, plan: InitPlan) -> None:
        """Render all primary plan values without truncating paths or model names."""

        self._output("Init plan")
        self._render_values("Integrations", plan.integrations)
        self._render_values("Review producers", plan.review_producers)
        if plan.review_models:
            for source, model in plan.review_models.items():
                self._output(f"- Model {source}: {model}")
        else:
            self._output("- Review models: (none)")
        self._output(f"- Review configuration: {plan.review_write.value}")
        self._output(f"- GitHub setup: {plan.github_decision.value}")
        for action in plan.file_actions:
            self._output(f"- File {action.action.value}: {action.path}")
            if self._width >= _NARROW_WIDTH:
                self._output(f"  hint: {_FILE_ACTION_HINTS[action.action]}")

    def _render_values(self, label: str, values: tuple[str, ...]) -> None:
        rendered = ", ".join(values) if values else "(none)"
        self._output(f"- {label}: {rendered}")

    def render_event(self, event: StepEventLike) -> None:
        """Render one structurally typed executor event as a stable plain line."""

        state = event.state.value if isinstance(event.state, Enum) else str(event.state)
        glyphs: Mapping[str, str]
        if self._unicode:
            glyphs = {
                StepRenderState.STARTED.value: "…",
                StepRenderState.SUCCEEDED.value: "✓",
                StepRenderState.WARNED.value: "!",
                StepRenderState.FAILED.value: "✗",
                StepRenderState.INTERRUPTED.value: "!",
            }
        else:
            glyphs = {
                StepRenderState.STARTED.value: "...",
                StepRenderState.SUCCEEDED.value: "OK",
                StepRenderState.WARNED.value: "WARN",
                StepRenderState.FAILED.value: "FAIL",
                StepRenderState.INTERRUPTED.value: "INTERRUPTED",
            }
        glyph = glyphs.get(state, "-")
        self._output(f"{glyph} {event.step_id}: {event.detail}")

    def on_step(self, event: StepEventLike) -> None:
        self.render_event(event)

    def render_cancelled(self) -> None:
        self._output("Setup cancelled")

    def render_outcome(self, result: ExecutionResultLike) -> None:
        command = result.next_command if result.success else result.recovery_command
        if command:
            label = "Next" if result.success else "Recovery"
            self._output(f"{label}: {command}")

    def prepare_plan(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        initial_choices: InitChoices | None = None,
    ) -> WizardResult:
        choices = initial_choices
        while True:
            collection = self.collect(request, preflight, initial_choices=choices)
            if collection.decision is ChoiceCollectionDecision.CANCEL:
                return WizardResult.cancelled()
            choices = collection.choices
            plan = build_init_plan(request, preflight, choices)
            decision = self.review(plan, assume_yes=request.assume_yes)
            if decision is ReviewDecision.BACK:
                continue
            if decision is ReviewDecision.CANCEL:
                return WizardResult.cancelled()
            return WizardResult.confirmed(plan)


class LineInitUI(_PlainInitUI):
    """Deterministic one-question-per-option interaction for limited terminals."""

    def collect(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        initial_choices: InitChoices | None = None,
    ) -> ChoiceCollectionResult:
        """Collect unresolved values without accepting combined multi-select input."""

        initial = _interactive_initial_choices(request, preflight, initial_choices)
        try:
            if preflight.review_config_error is not None and initial.review_write is None:
                if not self._ask_yes_no(
                    "Existing review configuration is invalid. Reset it?",
                    default=False,
                ):
                    return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
                initial = InitChoices(
                    integrations=initial.integrations,
                    review_write=ReviewWrite.RESET,
                    review_producers=(),
                    review_models={},
                    existing_files=initial.existing_files,
                    github_decision=initial.github_decision,
                    github_file_decisions=initial.github_file_decisions,
                )
            integrations = self._collect_integrations(request, preflight, initial)
            producers = self._collect_producers(request, preflight, initial)
            models = self._collect_models(request, producers, initial)
        except _CollectionCancelled:
            return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)

        return ChoiceCollectionResult(
            ChoiceCollectionDecision.REVIEW,
            InitChoices(
                integrations=integrations,
                review_write=initial.review_write,
                review_producers=producers,
                review_models=models,
                existing_files=initial.existing_files,
                github_decision=initial.github_decision,
                github_file_decisions=initial.github_file_decisions,
            ),
        )

    def _collect_integrations(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        initial: InitChoices,
    ) -> tuple[str, ...] | None:
        if request.integrations:
            return initial.integrations
        if request.no_agent:
            return ()
        defaults = (
            frozenset(initial.integrations)
            if initial.integrations is not None
            else frozenset(preflight.detected_integrations)
        )
        selected = []
        for option in _INTEGRATIONS:
            if self._width >= _NARROW_WIDTH:
                if option.value in preflight.detected_integrations:
                    self._output(f"{option.label} integration detected (recommended).")
                else:
                    self._output(f"{option.label} integration not detected (still selectable).")
            if self._ask_yes_no(
                f"Select {option.label} integration?",
                default=option.value in defaults,
            ):
                selected.append(option.value)
        return tuple(selected)

    def _collect_producers(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        initial: InitChoices,
    ) -> tuple[str, ...] | None:
        if request.review_producers:
            return initial.review_producers
        defaults = (
            frozenset(initial.review_producers)
            if initial.review_producers is not None
            else frozenset(preflight.detected_review_producers)
        )
        selected = []
        for option in _REVIEW_PRODUCERS:
            if option.value not in preflight.available_review_producers:
                self._output(f"{option.label} review producer unavailable (executable not found).")
                continue
            if self._width >= _NARROW_WIDTH and option.value in preflight.detected_review_producers:
                self._output(f"{option.label} review producer detected (recommended).")
            if self._ask_yes_no(
                f"Select {option.label} review producer?",
                default=option.value in defaults,
            ):
                selected.append(option.value)
        return tuple(selected)

    def _collect_models(
        self,
        request: InitRequest,
        selected_producers: tuple[str, ...] | None,
        initial: InitChoices,
    ) -> Mapping[str, str]:
        producers = (
            request.review_producers if request.review_producers else selected_producers or ()
        )
        entered_models = dict(initial.review_models)
        known_models = dict(entered_models)
        known_models.update(request.review_models)
        options = {option.value: option for option in _REVIEW_PRODUCERS}
        for producer in producers:
            option = options.get(producer)
            if option is None or option.source is None or option.source in known_models:
                continue
            model = self._ask_required(f"Model for {option.label}: ")
            entered_models[option.source] = model
            known_models[option.source] = model
        return entered_models

    def _ask_yes_no(self, question: str, *, default: bool) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self._input(f"{question} {suffix} ").strip().lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            if answer in {"cancel", "quit", "q"}:
                raise _CollectionCancelled
            self._output("Please answer yes or no.")

    def _ask_required(self, prompt: str) -> str:
        while True:
            answer = self._input(prompt).strip()
            if answer:
                if answer.lower() in {"cancel", "quit", "q"}:
                    raise _CollectionCancelled
                return answer
            self._output("A model is required.")

    def review(self, plan: InitPlan, *, assume_yes: bool = False) -> ReviewDecision:
        """Render the plan and resolve the only prompt that ``--yes`` skips."""

        self.render_plan(plan)
        if assume_yes:
            return ReviewDecision.CONFIRM
        while True:
            answer = self._input("Apply this plan? [Y/back/cancel] ").strip().lower()
            if answer in {"", "y", "yes", "review", "apply", "confirm"}:
                return ReviewDecision.CONFIRM
            if answer in {"b", "back"}:
                return ReviewDecision.BACK
            if answer in {"n", "no", "c", "cancel", "q", "quit"}:
                return ReviewDecision.CANCEL
            self._output("Please answer yes, back, or cancel.")


class NonInteractiveInitUI(_PlainInitUI):
    """Prompt-free backend that renders values but derives no configuration."""

    def collect(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        initial_choices: InitChoices | None = None,
    ) -> ChoiceCollectionResult:
        del request, preflight
        return ChoiceCollectionResult(
            ChoiceCollectionDecision.REVIEW,
            initial_choices or InitChoices(),
        )

    def review(self, plan: InitPlan, *, assume_yes: bool = False) -> ReviewDecision:
        del assume_yes
        self.render_plan(plan)
        return ReviewDecision.CONFIRM


def create_init_ui(
    capabilities: TerminalCapabilities,
    *,
    input_fn: TextInput,
    output_fn: TextOutput,
    quiet: bool = False,
) -> InteractiveInitUI | LineInitUI | NonInteractiveInitUI:
    """Select one UI once; callers may inject both streams for deterministic tests."""

    rendered_output = (lambda _message: None) if quiet else output_fn
    if capabilities.mode is InteractionMode.GUIDED:
        return InteractiveInitUI(
            unicode=capabilities.unicode,
            color=capabilities.color,
            width=capabilities.width,
        )
    ui_type = LineInitUI if capabilities.mode is InteractionMode.LINE else NonInteractiveInitUI
    return ui_type(
        input_fn=input_fn,
        output_fn=rendered_output,
        unicode=capabilities.unicode,
        color=capabilities.color,
        width=capabilities.width,
    )
