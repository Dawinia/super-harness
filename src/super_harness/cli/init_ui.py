"""Terminal capability selection and deterministic plain init interfaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from super_harness.cli.init_plan import (
    InitChoices,
    InitPlan,
    InitPreflight,
    InitRequest,
    InteractionMode,
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
                self._output("  hint: will be written during apply")

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

        initial = initial_choices or InitChoices()
        try:
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
