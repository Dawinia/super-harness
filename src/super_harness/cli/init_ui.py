"""Terminal capability selection and deterministic plain init interfaces."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import IO, Protocol, cast, runtime_checkable

import questionary
from rich.console import Console
from rich.text import Text

from super_harness.cli.init_github import GithubPlan
from super_harness.cli.init_models import ReviewerModelCandidate
from super_harness.cli.init_plan import (
    FileAction,
    GitHubDecision,
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
GithubResolver = Callable[[], GithubPlan]


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


def _safe_isatty(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _safe_encoding(stream: IO[str]) -> str | None:
    try:
        return getattr(stream, "encoding", None)
    except Exception:
        return None


def detect_terminal_capabilities(
    *,
    stdin_tty: bool,
    stdout_tty: bool,
    term: str | None,
    no_color: bool,
    encoding: str | None,
    width: int | None,
    ci: bool = False,
) -> TerminalCapabilities:
    """Select interaction and rendering capabilities from injected stream facts."""

    normalized_term = (term or "").strip().lower()
    cursor_limited = normalized_term in {"dumb", "unknown"} or normalized_term.startswith("dumb-")
    if ci or not stdin_tty:
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
        stdin_tty=_safe_isatty(stdin),
        stdout_tty=_safe_isatty(stdout),
        term=environ.get("TERM"),
        no_color="NO_COLOR" in environ,
        encoding=_safe_encoding(stdout),
        width=width if width is not None else shutil.get_terminal_size((80, 24)).columns,
        ci=bool(environ.get("CI")),
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

    @property
    def elapsed_ms(self) -> int: ...


def _format_elapsed(elapsed_ms: int) -> str:
    normalized = max(0, elapsed_ms)
    if normalized < 1_000:
        return f"{normalized}ms"
    return f"{normalized / 1_000:.1f}s"


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
_GUIDED_REVIEWER_LABELS = {
    "codex-cli": "Codex reviewer — runs via Codex CLI",
    "claude-cli": "Claude reviewer — runs via Claude CLI",
}

_QUESTIONARY_CHECKBOX_STYLE = questionary.Style(
    [
        ("selected", "fg:ansigreen noreverse"),
        ("text", "dim noreverse"),
        ("choice", "noreverse"),
        ("highlighted", "noreverse"),
    ]
)
_QUESTIONARY_NO_COLOR_CHECKBOX_STYLE = questionary.Style(
    [
        ("selected", "noreverse"),
        ("text", "dim noreverse"),
        ("choice", "noreverse"),
        ("highlighted", "noreverse"),
    ]
)
_QUESTIONARY_QMARK = "◆"
_QUESTIONARY_POINTER = "›"  # noqa: RUF001 - intentional terminal pointer glyph
_QUESTIONARY_CHECKBOX_INSTRUCTION = "(↑/↓ move · space select · enter confirm)"
_QUESTIONARY_SELECT_INSTRUCTION = "(↑/↓ move · enter confirm)"
_QUESTIONARY_ASCII_QMARK = "?"
_QUESTIONARY_ASCII_POINTER = ">"
_QUESTIONARY_ASCII_CHECKBOX_INSTRUCTION = "(up/down move, space select, enter confirm)"
_QUESTIONARY_ASCII_SELECT_INSTRUCTION = "(up/down move, enter confirm)"
_NARROW_WIDTH = 60


class _CollectionCancelled(Exception):
    pass


@contextmanager
def _questionary_no_cpr() -> Iterator[None]:
    previous = os.environ.get("PROMPT_TOOLKIT_NO_CPR")
    os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PROMPT_TOOLKIT_NO_CPR", None)
        else:
            os.environ["PROMPT_TOOLKIT_NO_CPR"] = previous


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

    def __init__(self, *, color: bool = True, unicode: bool = True) -> None:
        self._checkbox_style = (
            _QUESTIONARY_CHECKBOX_STYLE if color else _QUESTIONARY_NO_COLOR_CHECKBOX_STYLE
        )
        self._qmark = _QUESTIONARY_QMARK if unicode else _QUESTIONARY_ASCII_QMARK
        self._pointer = _QUESTIONARY_POINTER if unicode else _QUESTIONARY_ASCII_POINTER
        self._checkbox_instruction = (
            _QUESTIONARY_CHECKBOX_INSTRUCTION
            if unicode
            else _QUESTIONARY_ASCII_CHECKBOX_INSTRUCTION
        )
        self._select_instruction = (
            _QUESTIONARY_SELECT_INSTRUCTION if unicode else _QUESTIONARY_ASCII_SELECT_INSTRUCTION
        )

    @staticmethod
    def _choices(options: Sequence[GuidedPromptOption]) -> list[questionary.Choice]:
        return [
            questionary.Choice(
                [("class:choice", option.title)],
                value=option.value,
                checked=option.checked,
                disabled=option.disabled or None,
            )
            for option in options
        ]

    def checkbox(
        self, message: str, choices: Sequence[GuidedPromptOption]
    ) -> tuple[str, ...] | None:
        with _questionary_no_cpr():
            question = questionary.checkbox(
                message,
                choices=self._choices(choices),
                style=self._checkbox_style,
                qmark=self._qmark,
                pointer=self._pointer,
                instruction=self._checkbox_instruction,
                erase_when_done=True,
            )
            answer = question.unsafe_ask()
        return None if answer is None else tuple(cast(list[str], answer))

    def text(self, message: str, *, default: str | None = None) -> str | None:
        with _questionary_no_cpr():
            question = questionary.text(
                message,
                default=default or "",
                qmark=self._qmark,
                erase_when_done=True,
            )
            answer = question.unsafe_ask()
        return None if answer is None else str(answer)

    def select(
        self,
        message: str,
        choices: Sequence[GuidedPromptOption],
        *,
        default: str | None = None,
    ) -> str | None:
        with _questionary_no_cpr():
            question = questionary.select(
                message,
                choices=self._choices(choices),
                default=default,
                qmark=self._qmark,
                pointer=self._pointer,
                instruction=self._select_instruction,
                erase_when_done=True,
            )
            answer = question.unsafe_ask()
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
    def open_session(self) -> None: ...
    def close_session(self) -> None: ...
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


@runtime_checkable
class GuidedAnswerRenderAdapter(Protocol):
    """Optional completed-answer capability for guided renderers."""

    def render_answer(self, label: str, value: str) -> None: ...


_RAIL_GLYPHS = {
    True: dict(zip(RailState, ("◇", "◆", "●", "✗"), strict=True)),
    False: dict(zip(RailState, ("|", "+", "*", "x"), strict=True)),
}

_FILE_ACTION_HINTS = {
    FileAction.CREATE: "will be written during apply",
    FileAction.UPDATE: "will be written during apply",
    FileAction.DELETE: "will be removed during apply",
    FileAction.PRESERVE: "will be left unchanged",
    FileAction.SKIP: "not part of this run",
}
_RAIL_STYLES = dict(zip(RailState, ("dim", "cyan", "green", "red"), strict=True))
_EVENT_GLYPHS = {
    True: dict(zip(StepRenderState, ("…", "✓", "!", "✗", "!"), strict=True)),
    False: dict(zip(StepRenderState, ("+", "*", "!", "x", "x"), strict=True)),
}

_FILE_ACTION_ORDER = (
    FileAction.UPDATE,
    FileAction.CREATE,
    FileAction.DELETE,
    FileAction.PRESERVE,
    FileAction.SKIP,
)
_FILE_ACTION_LABELS = {
    FileAction.UPDATE: "Update",
    FileAction.CREATE: "Create",
    FileAction.DELETE: "Delete",
    FileAction.PRESERVE: "Preserve",
    FileAction.SKIP: "Skip",
}
_PUBLIC_STEP_LABELS = {
    "scaffold": "Harness scaffolding",
    "skeleton_config": "Harness configuration",
    "review_config": "Review configuration",
    "agent_integrations": "Agent integrations",
    "agents_md": "AGENTS.md",
    "gitignore": ".gitignore",
    "github": "GitHub setup",
}


def _file_action_display_rows(
    plan: InitPlan,
    action: FileAction,
    *,
    collapse_harness: bool,
) -> tuple[str, ...]:
    selected = tuple(item for item in plan.file_actions if item.action is action)
    if not collapse_harness:
        return tuple(str(item.path) for item in selected)

    harness = tuple(item for item in selected if ".harness" in item.path.parts)
    other = tuple(item for item in selected if ".harness" not in item.path.parts)
    rows: list[str] = []
    if len(harness) > 1:
        rows.append(f".harness configuration ({len(harness)} files)")
    elif harness:
        rows.append(str(harness[0].path))
    rows.extend(str(item.path) for item in other)
    return tuple(rows)


class RichGuidedRenderer:
    """Single-column Rich renderer that never acquires live terminal ownership."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        unicode: bool,
        color: bool,
        width: int,
        verbose: bool = False,
    ) -> None:
        self._unicode = unicode
        self._color = color
        self._width = max(1, width)
        self._verbose = verbose
        self._console = console or Console(color_system="auto" if color else None, width=width)
        self._apply_started = False
        self._session_open = False
        self._session_closed = False

    def open_session(self) -> None:
        if self._session_open or self._session_closed:
            return
        self._session_open = True
        self._print(f"{'┌' if self._unicode else '+'} super-harness init")

    def close_session(self) -> None:
        if not self._session_open or self._session_closed:
            return
        self._print("└" if self._unicode else "+")
        self._session_open = False
        self._session_closed = True

    def _print(self, value: str, *, style: str | None = None) -> None:
        self._console.print(
            Text(value, style=style if self._color and style else ""),
            overflow="fold",
            crop=False,
        )

    def _print_review_row(
        self,
        rail: str,
        value: str,
        *,
        style: str | None = None,
    ) -> None:
        leading_spaces = len(value) - len(value.lstrip(" "))
        available = max(1, self._width - Text(rail).cell_len - 2)
        indent = min(leading_spaces, max(0, available - 1))
        wrapped = Text(value.lstrip(" ")).wrap(
            self._console,
            max(1, available - indent),
            overflow="fold",
        ) or [Text()]
        for line in wrapped:
            self._print(f"{rail}  {' ' * indent}{line}", style=style)

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
        if secondary is not None:
            self._print(f"{'│' if self._unicode else '|'}  {secondary}", style="dim")

    def render_answer(self, label: str, value: str) -> None:
        glyph = _RAIL_GLYPHS[self._unicode][RailState.PENDING]
        heading = f"{glyph}  {label}"
        prefix = f"{heading}  "
        prefix_width = Text(prefix).cell_len
        if prefix_width >= self._width:
            self._print(heading, style="dim")
            indent = " " * min(3, max(0, self._width - 1))
            wrapped_value = Text(value).wrap(
                self._console,
                max(1, self._width - Text(indent).cell_len),
                overflow="fold",
            )
            for line in wrapped_value:
                self._print(f"{indent}{line}", style="dim")
            return
        wrapped = Text(value).wrap(
            self._console,
            max(1, self._width - prefix_width),
            overflow="fold",
        ) or [Text()]
        for index, line in enumerate(wrapped):
            leading = prefix if index == 0 else " " * prefix_width
            self._print(f"{leading}{line}", style="dim")

    def render_plan(self, plan: InitPlan) -> None:
        owns_session = not self._session_open
        if owns_session:
            self.open_session()
        integration_labels = {option.value: option.label for option in _INTEGRATIONS}
        rail = "│" if self._unicode else "|"
        self._print_review_row(rail, "Integrations")
        if plan.integrations:
            for integration in plan.integrations:
                self._print_review_row(
                    rail, f"  {integration_labels.get(integration, integration)}"
                )
        else:
            self._print_review_row(rail, "  (none)")

        self._print_review_row(rail, "Automated reviewers")
        if plan.review_models:
            for source, model in plan.review_models.items():
                self._print_review_row(rail, f"  {source.title()}  {model}")
        else:
            self._print_review_row(rail, "  Human review only")

        self._print_review_row(rail, "GitHub")
        github = (
            "Ensure workflow and PR template"
            if plan.github_decision is GitHubDecision.CREATE
            else "Skip GitHub setup"
        )
        self._print_review_row(rail, f"  {github}")

        self._print_review_row(rail, "Files")
        visible_actions = (
            _FILE_ACTION_ORDER
            if self._verbose
            else (FileAction.UPDATE, FileAction.CREATE, FileAction.DELETE)
        )
        for action in visible_actions:
            count = sum(item.action is action for item in plan.file_actions)
            if count == 0:
                continue
            noun = "file" if count == 1 else "files"
            self._print_review_row(rail, f"  {_FILE_ACTION_LABELS[action]:<9} {count} {noun}")
            for row in _file_action_display_rows(
                plan,
                action,
                collapse_harness=not self._verbose,
            ):
                self._print_review_row(rail, f"    {row}")
        if self._verbose and plan.backup_paths:
            noun = "settings file" if len(plan.backup_paths) == 1 else "settings files"
            self._print_review_row(rail, f"  {'Back up':<9} {len(plan.backup_paths)} {noun}")
            for path in plan.backup_paths:
                self._print_review_row(rail, f"    {path}")
        if not self._verbose:
            hidden_count = sum(
                item.action in {FileAction.PRESERVE, FileAction.SKIP}
                for item in plan.file_actions
            )
            if hidden_count:
                noun = "file" if hidden_count == 1 else "files"
                self._print_review_row(
                    rail,
                    f"  {hidden_count} unchanged {noun} hidden · use --verbose to inspect",
                    style="dim",
                )
        if owns_session:
            self.close_session()

    def render_validation(self, message: str) -> None:
        self._print(f"{'!' if self._unicode else 'x'}  {message}", style="yellow")

    def render_event(self, event: StepEventLike) -> None:
        state = event.state.value if isinstance(event.state, Enum) else str(event.state)
        glyphs = {key.value: value for key, value in _EVENT_GLYPHS[self._unicode].items()}
        if not self._apply_started:
            self.render_stage(RailStage.APPLY, RailState.CURRENT, "Applying setup")
            self._apply_started = True
        if state == StepRenderState.STARTED.value:
            return
        if state == StepRenderState.SUCCEEDED.value:
            if event.step_id in {"scaffold", "skeleton_config", "agents_md"}:
                return
            detail = {
                "review_config": "Harness configuration ready",
                "gitignore": "AGENTS.md and .gitignore updated",
            }.get(event.step_id, event.detail)
            if event.step_id == "agent_integrations" and detail.startswith("No agent"):
                return
            self._print(f"{glyphs[state]}  {detail}", style="green")
            return
        label = _PUBLIC_STEP_LABELS.get(event.step_id, "Setup")
        self._print(
            f"{glyphs.get(state, '|')}  {label}: {event.detail}",
            style="yellow" if state == StepRenderState.WARNED.value else "red",
        )


class WizardDecision(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


@dataclass(frozen=True)
class WizardResult:
    decision: WizardDecision
    plan: InitPlan | None
    github_plan: GithubPlan | None = None

    @classmethod
    def confirmed(cls, plan: InitPlan, github_plan: GithubPlan | None = None) -> WizardResult:
        return cls(WizardDecision.CONFIRM, plan, github_plan)

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


def _resolve_github_choices(
    choices: InitChoices,
    github_resolver: GithubResolver | None,
) -> tuple[InitChoices, GithubPlan | None]:
    if choices.github_decision is not GitHubDecision.CREATE or github_resolver is None:
        return choices, None
    plan = github_resolver()
    decisions = {
        item.inspection.path.relative_to(plan.root).as_posix(): item.decision
        for item in (plan.pr_template, plan.workflow)
    }
    return replace(choices, github_file_decisions=decisions), plan


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
        verbose: bool = False,
    ) -> None:
        self._prompts = prompt_adapter or QuestionaryPromptAdapter(color=color, unicode=unicode)
        self._renderer = renderer or RichGuidedRenderer(
            unicode=unicode,
            color=color,
            width=width,
            verbose=verbose,
        )

    def open_session(self) -> None:
        self._renderer.open_session()

    def close_session(self) -> None:
        self._renderer.close_session()

    def collect_github_setup(
        self,
        request: InitRequest,
        preflight: InitPreflight,
    ) -> GitHubDecision | None:
        """Resolve the optional top-level GitHub setup choice before inspection."""

        if request.setup_github:
            return GitHubDecision.CREATE
        if not preflight.github_available:
            return GitHubDecision.SKIP
        answer = self._prompts.select(
            "GitHub setup",
            (
                GuidedPromptOption("skip", "Skip GitHub setup", True),
                GuidedPromptOption("create", "Configure GitHub", False),
            ),
            default="skip",
        )
        if answer is None:
            return None
        return GitHubDecision(answer)

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
        preflight: InitPreflight,
        defaults: frozenset[str],
        explicit_models: Mapping[str, str],
    ) -> tuple[GuidedPromptOption, ...]:
        def create(option: _Option) -> GuidedPromptOption:
            available = option.value in preflight.available_review_producers
            ready = option.source in explicit_models or bool(
                preflight.reviewer_model_candidates.get(option.source or "", ())
            )
            if not available:
                disabled = "executable not found"
            elif not ready:
                disabled = preflight.reviewer_model_errors.get(
                    option.source or "", "model not configured"
                )
            else:
                disabled = None
            if disabled is not None:
                status = disabled
            elif option.value in preflight.detected_review_producers:
                status = "detected · recommended"
            else:
                status = "available"
            return GuidedPromptOption(
                option.value,
                f"{_GUIDED_REVIEWER_LABELS[option.value]}  {status}",
                disabled is None and option.value in defaults,
                disabled,
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
            "Integrations", self._integration_options(preflight, defaults)
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
        options = self._producer_options(preflight, defaults, request.review_models)
        if not any(option.disabled is None for option in options):
            self._renderer.render_validation(
                "No automated reviewers are ready; install a CLI and configure its model."
            )
            return ()
        answer = self._prompts.checkbox(
            "Automated reviewers",
            options,
        )
        if answer is None:
            return _CANCEL
        return tuple(value for value in answer if value in preflight.available_review_producers)

    def _collect_models(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        producers: tuple[str, ...] | None,
        initial: InitChoices,
    ) -> Mapping[str, str] | None:
        selected_sources = {
            option.source
            for option in _REVIEW_PRODUCERS
            if option.value in (request.review_producers or producers or ())
        }
        models = {
            source: model
            for source, model in initial.review_models.items()
            if source in selected_sources
        }
        models.update(request.review_models)
        known = dict(models)
        options = {option.value: option for option in _REVIEW_PRODUCERS}
        for producer in request.review_producers or producers or ():
            option = options.get(producer)
            if option is None or option.source is None or option.source in known:
                continue
            candidates = preflight.reviewer_model_candidates.get(option.source, ())
            if not candidates:
                self._renderer.render_validation(
                    preflight.reviewer_model_errors.get(option.source, "model not configured")
                )
                return None
            if len(candidates) == 1:
                answer = candidates[0].model
            else:
                choices = tuple(
                    GuidedPromptOption(
                        candidate.model,
                        f"{candidate.model}  {candidate.origin}",
                        checked=index == 0,
                    )
                    for index, candidate in enumerate(candidates)
                )
                selected_answer = self._prompts.select(
                    f"Model for {option.label.removesuffix(' CLI')} reviewer",
                    choices,
                    default=candidates[0].model,
                )
                if selected_answer is None:
                    return None
                answer = selected_answer
            models[option.source] = answer
            known[option.source] = answer
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
        models = self._collect_models(request, preflight, typed_producers, initial)
        if models is None:
            return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
        github_decision = self.collect_github_setup(request, preflight)
        if github_decision is None:
            return ChoiceCollectionResult(ChoiceCollectionDecision.CANCEL, initial)
        choices = InitChoices(
            integrations=typed_integrations,
            review_write=initial.review_write,
            review_producers=typed_producers,
            review_models=models,
            existing_files=initial.existing_files,
            github_decision=github_decision,
            github_file_decisions=(
                initial.github_file_decisions if github_decision is initial.github_decision else {}
            ),
        )
        return ChoiceCollectionResult(ChoiceCollectionDecision.REVIEW, choices)

    def review(self, plan: InitPlan, *, assume_yes: bool = False) -> ReviewDecision:
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

    def _render_answers(self, plan: InitPlan) -> None:
        if not isinstance(self._renderer, GuidedAnswerRenderAdapter):
            return
        integration_labels = {option.value: option.label for option in _INTEGRATIONS}
        integrations = ", ".join(
            integration_labels.get(integration, integration)
            for integration in plan.integrations
        )
        self._renderer.render_answer("Integrations", integrations or "(none)")

        producers = {option.value: option for option in _REVIEW_PRODUCERS}
        reviewer_answers: list[str] = []
        for producer in plan.review_producers:
            option = producers[producer]
            if option.source is None:
                continue
            label = option.label.removesuffix(" CLI")
            reviewer_answers.append(f"{label} ({plan.review_models[option.source]})")
        self._renderer.render_answer(
            "Automated reviewers",
            ", ".join(reviewer_answers) or "(none)",
        )
        self._renderer.render_answer(
            "GitHub",
            (
                "Workflow and PR template"
                if plan.github_decision is GitHubDecision.CREATE
                else "Skipped"
            ),
        )

    def prepare_plan(
        self,
        request: InitRequest,
        preflight: InitPreflight,
        *,
        assume_yes: bool | None = None,
        initial_choices: InitChoices | None = None,
        github_resolver: GithubResolver | None = None,
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
            choices, github_plan = _resolve_github_choices(collection.choices, github_resolver)
            plan = build_init_plan(request, preflight, choices)
            self._render_answers(plan)
            decision = self.review(plan, assume_yes=effective_assume_yes)
            if decision is ReviewDecision.BACK:
                continue
            if decision is ReviewDecision.CANCEL:
                return WizardResult.cancelled()
            return WizardResult.confirmed(plan, github_plan)

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

    def render_interrupted(self) -> None:
        self._renderer.render_stage(RailStage.APPLY, RailState.PENDING, "No writes started")
        self._renderer.render_stage(RailStage.OUTCOME, RailState.FAILED, "Setup interrupted")

    def render_already_initialized(self, harness: object) -> None:
        del harness
        self._renderer.render_stage(
            RailStage.OUTCOME,
            RailState.COMPLETED,
            "Already initialized",
            secondary=(
                "Next: super-harness status; Review/reconfigure: super-harness init --force"
            ),
        )

    def render_plan(self, plan: InitPlan) -> None:
        self._renderer.render_plan(plan)

    def render_event(self, event: StepEventLike) -> None:
        self._renderer.render_event(event)

    def on_step(self, event: StepEventLike) -> None:
        self.render_event(event)

    def render_outcome(self, result: ExecutionResultLike) -> None:
        state = RailState.COMPLETED if result.success else RailState.FAILED
        elapsed = _format_elapsed(result.elapsed_ms)
        detail = (
            f"Setup complete in {elapsed}"
            if result.success
            else f"{result.message or 'Setup failed'} after {elapsed}"
        )
        command = result.next_command if result.success else result.recovery_command
        command_label = "Next" if result.success else "Recovery"
        self._renderer.render_stage(
            RailStage.OUTCOME,
            state,
            detail,
            secondary=f"{command_label}: {command}" if command else None,
        )


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

    def open_session(self) -> None:
        pass

    def close_session(self) -> None:
        pass

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

    def render_interrupted(self) -> None:
        self._output("Setup interrupted")

    def render_already_initialized(self, harness: object) -> None:
        del harness
        self._output("Already initialized")
        self._output("Next: super-harness status")
        self._output("Review/reconfigure: super-harness init --force")

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
        github_resolver: GithubResolver | None = None,
    ) -> WizardResult:
        choices = initial_choices
        while True:
            collection = self.collect(request, preflight, initial_choices=choices)
            if collection.decision is ChoiceCollectionDecision.CANCEL:
                return WizardResult.cancelled()
            collected = collection.choices
            if collected.github_decision is None:
                collected = replace(
                    collected,
                    github_decision=(
                        GitHubDecision.CREATE if request.setup_github else GitHubDecision.SKIP
                    ),
                )
            choices, github_plan = _resolve_github_choices(collected, github_resolver)
            plan = build_init_plan(request, preflight, choices)
            decision = self.review(plan, assume_yes=request.assume_yes)
            if decision is ReviewDecision.BACK:
                continue
            if decision is ReviewDecision.CANCEL:
                return WizardResult.cancelled()
            return WizardResult.confirmed(plan, github_plan)


class LineInitUI(_PlainInitUI):
    """Deterministic one-question-per-option interaction for limited terminals."""

    def collect_github_setup(
        self,
        request: InitRequest,
        preflight: InitPreflight,
    ) -> GitHubDecision:
        if request.setup_github:
            return GitHubDecision.CREATE
        if not preflight.github_available:
            return GitHubDecision.SKIP
        return (
            GitHubDecision.CREATE
            if self._ask_yes_no("Configure GitHub files and repository settings?", default=False)
            else GitHubDecision.SKIP
        )

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
            models = self._collect_models(request, preflight, producers, initial)
            github_decision = self.collect_github_setup(request, preflight)
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
                github_decision=github_decision,
                github_file_decisions=(
                    initial.github_file_decisions
                    if github_decision is initial.github_decision
                    else {}
                ),
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
            has_explicit_model = option.source in request.review_models
            has_candidate = bool(preflight.reviewer_model_candidates.get(option.source or "", ()))
            if not has_explicit_model and not has_candidate:
                reason = preflight.reviewer_model_errors.get(
                    option.source or "", "model not configured"
                )
                self._output(f"{option.label} reviewer unavailable ({reason}).")
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
        preflight: InitPreflight,
        selected_producers: tuple[str, ...] | None,
        initial: InitChoices,
    ) -> Mapping[str, str]:
        producers = (
            request.review_producers if request.review_producers else selected_producers or ()
        )
        selected_sources = {
            option.source for option in _REVIEW_PRODUCERS if option.value in producers
        }
        entered_models = {
            source: model
            for source, model in initial.review_models.items()
            if source in selected_sources
        }
        entered_models.update(request.review_models)
        known_models = dict(entered_models)
        options = {option.value: option for option in _REVIEW_PRODUCERS}
        for producer in producers:
            option = options.get(producer)
            if option is None or option.source is None or option.source in known_models:
                continue
            candidates = preflight.reviewer_model_candidates.get(option.source, ())
            if not candidates:
                self._output(
                    preflight.reviewer_model_errors.get(option.source, "model not configured")
                )
                raise _CollectionCancelled
            if len(candidates) == 1:
                candidate = candidates[0]
                model = candidate.model
                self._output(f"{option.label} reviewer model: {model} ({candidate.origin}).")
            else:
                model = self._ask_model_choice(option.label, candidates)
            entered_models[option.source] = model
            known_models[option.source] = model
        return entered_models

    def _ask_model_choice(
        self,
        label: str,
        candidates: tuple[ReviewerModelCandidate, ...],
    ) -> str:
        self._output(f"Choose model for {label} reviewer:")
        for index, candidate in enumerate(candidates, start=1):
            self._output(f"  {index}. {candidate.model} ({candidate.origin})")
        while True:
            answer = self._input("Model [1]: ").strip().lower()
            if not answer:
                return candidates[0].model
            if answer in {"cancel", "quit", "q"}:
                raise _CollectionCancelled
            if answer.isdigit() and 1 <= int(answer) <= len(candidates):
                return candidates[int(answer) - 1].model
            self._output(f"Choose a number from 1 to {len(candidates)}.")

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

    def collect_github_setup(
        self,
        request: InitRequest,
        preflight: InitPreflight,
    ) -> GitHubDecision:
        del preflight
        return GitHubDecision.CREATE if request.setup_github else GitHubDecision.SKIP

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
    verbose: bool = False,
) -> InteractiveInitUI | LineInitUI | NonInteractiveInitUI:
    """Select one UI once; callers may inject both streams for deterministic tests."""

    rendered_output = (lambda _message: None) if quiet else output_fn
    if capabilities.mode is InteractionMode.GUIDED:
        return InteractiveInitUI(
            unicode=capabilities.unicode,
            color=capabilities.color,
            width=capabilities.width,
            verbose=verbose,
        )
    ui_type = LineInitUI if capabilities.mode is InteractionMode.LINE else NonInteractiveInitUI
    return ui_type(
        input_fn=input_fn,
        output_fn=rendered_output,
        unicode=capabilities.unicode,
        color=capabilities.color,
        width=capabilities.width,
    )
