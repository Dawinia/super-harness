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
    def render_result(
        self,
        state: RailState,
        detail: str,
        *,
        secondary: str | None = None,
    ) -> None: ...


@runtime_checkable
class GuidedAnswerRenderAdapter(Protocol):
    """Optional completed-answer capability for guided renderers."""

    def render_answer(self, label: str, value: str) -> None: ...


# v2 renderer glyph set — the ONLY glyphs RichGuidedRenderer emits, all on the
# spine. Live-frame glyphs (◆ ● ○) belong to Questionary's transient prompt chrome
# and are never composed here (see the design doc's v2 glyph-grammar section).
_SPINE_CHAR = {True: "│", False: "|"}
_CORNER_OPEN = {True: "┌", False: "+"}
_CORNER_CLOSE = {True: "└", False: "+"}
_GLYPH_COMPLETED = {True: "◇", False: "o"}
_GLYPH_WARNING = {True: "▲", False: "!"}
_GLYPH_FAILED = {True: "✗", False: "x"}
_GLYPH_STARTED = {True: "…", False: "~"}

_FILE_ACTION_HINTS = {
    FileAction.CREATE: "will be written during apply",
    FileAction.UPDATE: "will be written during apply",
    FileAction.DELETE: "will be removed during apply",
    FileAction.PRESERVE: "will be left unchanged",
    FileAction.SKIP: "not part of this run",
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
    "scaffold": "Harness configuration",
    "skeleton_config": "Harness configuration",
    "review_config": "Harness configuration",
    "agent_integrations": "Agent integrations",
    "agents_md": "Repository guidance",
    "gitignore": "Repository guidance",
    "github": "GitHub setup",
}
_PUBLIC_OUTCOME_MEMBERS = {
    "Harness configuration": frozenset({"scaffold", "skeleton_config", "review_config"}),
    "Agent integrations": frozenset({"agent_integrations"}),
    "Repository guidance": frozenset({"agents_md", "gitignore"}),
    "GitHub setup": frozenset({"github"}),
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
    """Single-column clack-style renderer: one continuous spine from ``┌`` to ``└``.

    Every persistent line is either a corner (``┌``/``└``), a bare spine separator
    ``│`` between logical groups, or a content line prefixed by a state glyph or the
    spine followed by two spaces. The renderer emits only the v2 glyph set
    (``┌ └ │ ◇ ▲ ✗`` and, verbose-only, ``…``); the live ``◆``/``●``/``○`` frames
    belong to Questionary and are never composed here.
    """

    _WRITE_ACTIONS = (FileAction.UPDATE, FileAction.CREATE, FileAction.DELETE)

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
        self._succeeded_steps: set[str] = set()
        self._rendered_outcomes: set[str] = set()
        self._session_open = False
        self._session_closed = False
        self._terminal_result: tuple[RailState, str, str | None] | None = None
        # Spine state machine: whether any content has been emitted since the opener,
        # whether the last line was already a separator (avoid doubles), and whether
        # the last content line was an apply outcome (its run shares one group).
        self._content_started = False
        self._last_was_separator = False
        self._last_was_outcome = False

    @property
    def _bar(self) -> str:
        return _SPINE_CHAR[self._unicode]

    def open_session(self) -> None:
        if self._session_open or self._session_closed:
            return
        self._session_open = True
        self._print(f"{_CORNER_OPEN[self._unicode]} super-harness init")
        # The opener counts as prior content so the first group is preceded by a
        # separator ("the opener is followed by one separator").
        self._content_started = True
        self._last_was_separator = False

    def close_session(self) -> None:
        if not self._session_open or self._session_closed:
            return
        corner = _CORNER_CLOSE[self._unicode]
        if self._terminal_result is None:
            self._print(corner)
        else:
            state, detail, secondary = self._terminal_result
            separator = "·" if self._unicode else "-"
            content = detail if secondary is None else f"{detail} {separator} {secondary}"
            self._group_break()
            self._emit_wrapped(
                f"{corner} ",
                None,
                content,
                style="green" if state is RailState.COMPLETED else "red",
            )
        self._session_open = False
        self._session_closed = True

    # --- spine primitives -------------------------------------------------------

    def _print(self, value: str, *, style: str | None = None) -> None:
        value = self._output_safe(value)
        self._console.print(
            Text(value, style=style if self._color and style else ""),
            overflow="fold",
            crop=False,
        )

    def _output_safe(self, value: str) -> str:
        encoding = _safe_encoding(self._console.file)
        if encoding is None:
            return value
        try:
            value.encode(encoding)
        except UnicodeEncodeError:
            return value.encode(encoding, errors="backslashreplace").decode(encoding)
        except LookupError:
            return value
        return value

    def _separator(self) -> None:
        """Emit one bare spine line (no trailing whitespace) between groups."""
        self._print(self._bar)
        self._last_was_separator = True

    def _group_break(self) -> None:
        """Insert a separator before a new logical group, avoiding leading/double."""
        if self._content_started and not self._last_was_separator:
            self._separator()

    def _emit_wrapped(
        self,
        first_prefix: str,
        cont_prefix: str | None,
        text: str,
        *,
        style: str | None = None,
    ) -> None:
        """Emit a content line, wrapping onto ``cont_prefix`` (the spine) if given.

        ``cont_prefix=None`` aligns continuations under the first prefix with spaces
        (used only by the ``└`` closer, which has no spine after it).
        """
        first_prefix = self._output_safe(first_prefix)
        text = self._output_safe(text)
        prefix_width = Text(first_prefix).cell_len
        if cont_prefix is None:
            cont = " " * prefix_width
        else:
            cont = self._output_safe(cont_prefix)
        wrapped = Text(text).wrap(
            self._console,
            max(1, self._width - prefix_width),
            overflow="fold",
        ) or [Text()]
        for index, line in enumerate(wrapped):
            leading = first_prefix if index == 0 else cont
            self._print(f"{leading}{line}", style=style)
        self._content_started = True
        self._last_was_separator = False

    def _content(self, glyph: str, text: str, *, style: str | None = None) -> None:
        """A glyph-led content line whose wraps hang on the spine."""
        bar = self._bar
        self._emit_wrapped(f"{glyph}  ", f"{bar}  ", text, style=style)

    def _spine(self, text: str, *, style: str | None = None) -> None:
        """A spine-led content line (review/warning detail)."""
        bar = self._bar
        self._emit_wrapped(f"{bar}  ", f"{bar}  ", text, style=style)

    # --- public render surface --------------------------------------------------

    def render_stage(
        self,
        stage: RailStage,
        state: RailState,
        detail: str,
        *,
        secondary: str | None = None,
    ) -> None:
        # Kept for the GuidedRenderAdapter protocol. The guided flow now renders the
        # workspace via render_answer; this stays on-spine and de-jargoned (no
        # ``stage.value:`` prefix) for any direct caller. The renderer emits only its
        # v2 glyph set, so a failed stage uses ✗ and everything else the ◇ completed
        # glyph (there is no renderer "active" glyph — that frame is Questionary's).
        self._group_break()
        if state is RailState.FAILED:
            glyph, style = _GLYPH_FAILED[self._unicode], "red"
        else:
            glyph, style = _GLYPH_COMPLETED[self._unicode], "green"
        self._content(glyph, detail, style=style)
        self._last_was_outcome = False
        if secondary is not None:
            self._spine(secondary, style="dim")

    def render_answer(self, label: str, value: str) -> None:
        self._group_break()
        glyph = _GLYPH_COMPLETED[self._unicode]
        self._content(glyph, f"{label}  {value}", style="green")
        self._last_was_outcome = False

    def render_plan(self, plan: InitPlan) -> None:
        owns_session = not self._session_open
        if owns_session:
            self.open_session()
        self._group_break()
        glyph = _GLYPH_COMPLETED[self._unicode]
        writes = sum(item.action in self._WRITE_ACTIONS for item in plan.file_actions)
        noun = "file" if writes == 1 else "files"
        self._content(glyph, f"Plan  {writes} {noun} to write", style="green")
        self._last_was_outcome = False

        if self._verbose:
            self._render_plan_verbose(plan)
        else:
            inline = self._inline_mutation_paths(plan)
            if inline:
                self._spine(inline)
            hidden = sum(
                item.action in {FileAction.PRESERVE, FileAction.SKIP}
                for item in plan.file_actions
            )
            if hidden:
                dash = "—" if self._unicode else "--"
                self._spine(
                    f"{hidden} unchanged hidden {dash} --verbose to see them",
                    style="dim",
                )
        if owns_session:
            self.close_session()

    def _inline_mutation_paths(self, plan: InitPlan) -> str:
        writes = [item for item in plan.file_actions if item.action in self._WRITE_ACTIONS]
        harness = [item for item in writes if ".harness" in item.path.parts]
        others = [item for item in writes if ".harness" not in item.path.parts]
        times = "×" if self._unicode else "x"  # noqa: RUF001 - intentional count glyph
        parts: list[str] = []
        if len(harness) > 1:
            parts.append(f".harness {times}{len(harness)}")
        elif harness:
            parts.append(harness[0].path.name)
        parts.extend(item.path.name for item in others)
        sep = " · " if self._unicode else " - "
        return sep.join(parts)

    def _render_plan_verbose(self, plan: InitPlan) -> None:
        sep = " · " if self._unicode else " - "
        for action in _FILE_ACTION_ORDER:
            count = sum(item.action is action for item in plan.file_actions)
            if count == 0:
                continue
            rows = _file_action_display_rows(plan, action, collapse_harness=False)
            self._spine(f"{_FILE_ACTION_LABELS[action]:<9} " + sep.join(rows))
        if plan.backup_paths:
            self._spine("Back up   " + sep.join(str(path) for path in plan.backup_paths))

    def render_validation(self, message: str) -> None:
        self._group_break()
        self._content(_GLYPH_WARNING[self._unicode], message, style="yellow")
        self._last_was_outcome = False

    def render_event(self, event: StepEventLike) -> None:
        state = event.state.value if isinstance(event.state, Enum) else str(event.state)
        label = _PUBLIC_STEP_LABELS.get(event.step_id, "Setup")
        if self._verbose and state in {
            StepRenderState.STARTED.value,
            StepRenderState.SUCCEEDED.value,
        }:
            glyph = (
                _GLYPH_STARTED[self._unicode]
                if state == StepRenderState.STARTED.value
                else _GLYPH_COMPLETED[self._unicode]
            )
            self._group_break()
            self._content(
                glyph,
                f"{label}: {event.detail}",
                style="dim" if state == StepRenderState.STARTED.value else "green",
            )
            self._last_was_outcome = False
            return
        if state == StepRenderState.STARTED.value:
            return
        if state == StepRenderState.SUCCEEDED.value:
            self._succeeded_steps.add(event.step_id)
            members = _PUBLIC_OUTCOME_MEMBERS.get(label)
            if (
                members is not None
                and members <= self._succeeded_steps
                and label not in self._rendered_outcomes
            ):
                # Consecutive outcomes share one group (no separator between them).
                if not self._last_was_outcome:
                    self._group_break()
                self._content(_GLYPH_COMPLETED[self._unicode], label, style="green")
                self._last_was_outcome = True
                self._rendered_outcomes.add(label)
            return
        # Warning or failure: its own group, actionable detail on the spine.
        self._group_break()
        if state == StepRenderState.WARNED.value:
            self._content(_GLYPH_WARNING[self._unicode], f"{label}: {event.detail}", style="yellow")
        else:
            self._content(_GLYPH_FAILED[self._unicode], f"{label}: {event.detail}", style="red")
        self._last_was_outcome = False

    def render_result(
        self,
        state: RailState,
        detail: str,
        *,
        secondary: str | None = None,
    ) -> None:
        if self._session_closed or self._terminal_result is not None:
            return
        if not self._session_open:
            self.open_session()
        self._terminal_result = (state, detail, secondary)


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
            integration_labels.get(integration, integration) for integration in plan.integrations
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
        if isinstance(self._renderer, GuidedAnswerRenderAdapter):
            self._renderer.render_answer("Workspace", str(request.workspace))
        else:
            self._renderer.render_stage(
                RailStage.PREFLIGHT,
                RailState.COMPLETED,
                f"Workspace  {request.workspace}",
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
        self._renderer.render_result(RailState.COMPLETED, "Setup cancelled")

    def render_cancelled(self) -> None:
        self._render_cancelled()

    def render_interrupted(self) -> None:
        self._renderer.render_result(RailState.FAILED, "Setup interrupted")

    def render_already_initialized(self, harness: object) -> None:
        del harness
        self._renderer.render_result(
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
            f"Setup complete in {elapsed}" if result.success else f"Setup failed after {elapsed}"
        )
        command = result.next_command if result.success else result.recovery_command
        command_label = "Next" if result.success else "Recovery"
        self._renderer.render_result(
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
