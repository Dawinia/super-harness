from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import FrozenInstanceError, replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from super_harness.adapters.agent._settings_merge import SettingsMergePlan
from super_harness.adapters.install import AgentIntegrationPlan
from super_harness.cli.init_models import ReviewerModelCandidate
from super_harness.cli.init_plan import (
    FileAction,
    GitHubDecision,
    GithubFileDecision,
    HarnessState,
    InitChoices,
    InitPlan,
    InitPreflight,
    InitRequest,
    InteractionMode,
    PlannedFileAction,
    ReviewWrite,
)
from super_harness.cli.init_ui import (
    ChoiceCollectionDecision,
    GuidedPromptOption,
    InteractiveInitUI,
    LineInitUI,
    NonInteractiveInitUI,
    QuestionaryPromptAdapter,
    RailStage,
    RailState,
    ReviewDecision,
    RichGuidedRenderer,
    StepRenderEvent,
    StepRenderState,
    TerminalCapabilities,
    WizardDecision,
    detect_runtime_terminal_capabilities,
    detect_terminal_capabilities,
)


def _preflight(
    *,
    detected_integrations: tuple[str, ...] = ("codex",),
    available_integrations: frozenset[str] = frozenset({"codex"}),
    detected_producers: tuple[str, ...] = ("codex-cli",),
    available_producers: frozenset[str] = frozenset({"codex-cli"}),
    reviewer_model_candidates: dict[str, tuple[ReviewerModelCandidate, ...]] | None = None,
    reviewer_model_errors: dict[str, str] | None = None,
    github_available: bool = False,
) -> InitPreflight:
    candidates = (
        {"codex": (ReviewerModelCandidate("codex", "gpt-5-codex", "Codex CLI config", 10),)}
        if reviewer_model_candidates is None
        else reviewer_model_candidates
    )
    return InitPreflight(
        harness_state=HarnessState.ABSENT,
        existing_file_bytes={},
        available_integrations=available_integrations,
        available_review_producers=available_producers,
        detected_integrations=detected_integrations,
        detected_review_producers=detected_producers,
        reviewer_model_candidates=candidates,
        reviewer_model_errors=({} if reviewer_model_errors is None else reviewer_model_errors),
        github_available=github_available,
    )


def _request(
    tmp_path: Path,
    *,
    integrations: tuple[str, ...] = (),
    producers: tuple[str, ...] = (),
    models: dict[str, str] | None = None,
) -> InitRequest:
    return InitRequest(
        workspace=tmp_path,
        interaction_mode=InteractionMode.LINE,
        integrations=integrations,
        review_producers=producers,
        review_models={} if models is None else models,
        review_flags_explicit=bool(producers or models),
    )


def _plan(tmp_path: Path) -> InitPlan:
    return InitPlan(
        harness_state=HarnessState.ABSENT,
        review_write=ReviewWrite.UPDATE,
        integrations=("codex",),
        review_producers=("codex-cli",),
        review_models={"codex": "gpt-5-codex"},
        github_decision=GitHubDecision.SKIP,
        file_actions=(
            PlannedFileAction(
                path=tmp_path / "a-very-long-project-name" / ".harness" / "state.yaml",
                action=FileAction.CREATE,
                content=b"version: 1\n",
            ),
        ),
    )


def _sequence_input(values: list[str]) -> tuple[Callable[[str], str], list[str]]:
    iterator: Iterator[str] = iter(values)
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(iterator)

    return read, prompts


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "term", "expected"),
    [
        (False, True, "xterm-256color", InteractionMode.NON_INTERACTIVE),
        (False, False, "dumb", InteractionMode.NON_INTERACTIVE),
        (True, True, "xterm-256color", InteractionMode.GUIDED),
        (True, False, "xterm-256color", InteractionMode.LINE),
        (True, True, "dumb", InteractionMode.LINE),
    ],
)
def test_capability_mode_matrix(
    stdin_tty: bool,
    stdout_tty: bool,
    term: str,
    expected: InteractionMode,
) -> None:
    capabilities = detect_terminal_capabilities(
        stdin_tty=stdin_tty,
        stdout_tty=stdout_tty,
        term=term,
        no_color=False,
        encoding="utf-8",
        width=80,
    )

    assert capabilities.mode is expected


def test_ci_forces_noninteractive_mode_even_when_both_streams_are_ttys() -> None:
    capabilities = detect_terminal_capabilities(
        stdin_tty=True,
        stdout_tty=True,
        term="xterm-256color",
        no_color=False,
        encoding="utf-8",
        width=80,
        ci=True,
    )

    assert capabilities.mode is InteractionMode.NON_INTERACTIVE


class _TTYProbe:
    def __init__(self, *, tty: bool | BaseException, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        if isinstance(self._tty, BaseException):
            raise self._tty
        return self._tty


@pytest.mark.parametrize(
    ("stdin_probe", "stdout_probe", "expected"),
    [
        (
            _TTYProbe(tty=OSError("stdin probe failed")),
            _TTYProbe(tty=True),
            InteractionMode.NON_INTERACTIVE,
        ),
        (_TTYProbe(tty=True), _TTYProbe(tty=OSError("stdout probe failed")), InteractionMode.LINE),
    ],
)
def test_runtime_terminal_probe_failures_fall_back_to_plain_modes(
    stdin_probe: _TTYProbe,
    stdout_probe: _TTYProbe,
    expected: InteractionMode,
) -> None:
    capabilities = detect_runtime_terminal_capabilities(  # type: ignore[arg-type]
        stdin_probe,
        stdout_probe,
        {"TERM": "xterm-256color"},
        width=80,
    )

    assert capabilities.mode is expected


def test_no_color_changes_only_color() -> None:
    colored = detect_terminal_capabilities(
        stdin_tty=True,
        stdout_tty=True,
        term="xterm-256color",
        no_color=False,
        encoding="utf-8",
        width=80,
    )
    uncolored = detect_terminal_capabilities(
        stdin_tty=True,
        stdout_tty=True,
        term="xterm-256color",
        no_color=True,
        encoding="utf-8",
        width=80,
    )

    assert colored.mode is uncolored.mode is InteractionMode.GUIDED
    assert colored.unicode is uncolored.unicode is True
    assert colored.color is True
    assert uncolored.color is False


def test_unsafe_encoding_changes_only_unicode() -> None:
    safe = detect_terminal_capabilities(
        stdin_tty=True,
        stdout_tty=True,
        term="xterm-256color",
        no_color=False,
        encoding="utf-8",
        width=80,
    )
    unsafe = detect_terminal_capabilities(
        stdin_tty=True,
        stdout_tty=True,
        term="xterm-256color",
        no_color=False,
        encoding="ascii",
        width=80,
    )

    assert safe.mode is unsafe.mode is InteractionMode.GUIDED
    assert safe.color is unsafe.color is True
    assert safe.unicode is True
    assert unsafe.unicode is False


@pytest.mark.parametrize(("raw_width", "expected"), [(0, 1), (-12, 1), (19, 19)])
def test_width_is_normalized_to_a_safe_positive_value(raw_width: int, expected: int) -> None:
    capabilities = detect_terminal_capabilities(
        stdin_tty=True,
        stdout_tty=False,
        term="xterm-256color",
        no_color=False,
        encoding="utf-8",
        width=raw_width,
    )

    assert capabilities.width == expected


def test_terminal_capabilities_are_immutable() -> None:
    capabilities = TerminalCapabilities(
        mode=InteractionMode.LINE,
        color=False,
        unicode=True,
        width=80,
    )

    with pytest.raises(FrozenInstanceError):
        capabilities.width = 10  # type: ignore[misc]


def test_line_collect_asks_one_yes_no_question_per_selectable_option(
    tmp_path: Path,
) -> None:
    read, prompts = _sequence_input(["", "y", ""])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight())

    assert result.decision is ChoiceCollectionDecision.REVIEW
    assert result.choices.integrations == ("codex", "claude-code")
    assert result.choices.review_producers == ("codex-cli",)
    assert dict(result.choices.review_models) == {"codex": "gpt-5-codex"}
    assert prompts == [
        "Select Codex integration? [Y/n] ",
        "Select Claude Code integration? [y/N] ",
        "Select Codex CLI review producer? [Y/n] ",
    ]
    assert all("," not in prompt for prompt in prompts)
    assert "comma" not in "\n".join(output).lower()


def test_line_collect_rejects_numeric_and_comma_answers_instead_of_parsing_them(
    tmp_path: Path,
) -> None:
    read, prompts = _sequence_input(["1,2", "1", "n", "n", "n"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(
        _request(tmp_path),
        _preflight(
            detected_integrations=(),
            available_integrations=frozenset(),
            detected_producers=(),
            available_producers=frozenset(),
        ),
    )

    assert result.choices.integrations == ()
    assert len([prompt for prompt in prompts if prompt.startswith("Select Codex ")]) == 3
    assert output.count("Please answer yes or no.") == 2


def test_unavailable_integrations_remain_selectable_but_default_false(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "", "n"])
    ui = LineInitUI(input_fn=read, output_fn=lambda _: None, unicode=False, width=80)

    result = ui.collect(
        _request(tmp_path),
        _preflight(
            detected_integrations=(),
            available_integrations=frozenset(),
            detected_producers=(),
            available_producers=frozenset({"codex-cli"}),
        ),
    )

    assert result.choices.integrations == ()
    assert "[y/N]" in prompts[0]
    assert "[y/N]" in prompts[1]


def test_detected_and_unavailable_defaults_are_explained(tmp_path: Path) -> None:
    read, _ = _sequence_input(["n", "n", "n"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    ui.collect(
        _request(tmp_path),
        _preflight(
            detected_integrations=("codex",),
            available_integrations=frozenset({"codex"}),
            detected_producers=("codex-cli",),
            available_producers=frozenset({"codex-cli"}),
        ),
    )

    assert "Codex integration detected (recommended)." in output
    assert "Claude Code integration not detected (still selectable)." in output
    assert "Codex CLI review producer detected (recommended)." in output


def test_unavailable_producers_are_not_prompted_or_defaulted(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(
        _request(tmp_path),
        _preflight(
            detected_integrations=(),
            available_integrations=frozenset(),
            detected_producers=(),
            available_producers=frozenset(),
        ),
    )

    assert result.choices.review_producers == ()
    assert all("review producer" not in prompt for prompt in prompts)
    assert "Codex CLI review producer unavailable (executable not found)." in output
    assert "Claude CLI review producer unavailable (executable not found)." in output


def test_line_auto_selects_the_only_configured_model(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n", ""])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight())

    assert dict(result.choices.review_models) == {"codex": "gpt-5-codex"}
    assert all(not prompt.startswith("Model") for prompt in prompts)
    assert "Codex CLI reviewer model: gpt-5-codex (Codex CLI config)." in output


def test_line_selects_configured_model_by_number_without_accepting_raw_text(
    tmp_path: Path,
) -> None:
    read, prompts = _sequence_input(["n", "n", "", "gpt-fast", "2"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)
    preflight = _preflight(
        reviewer_model_candidates={
            "codex": (
                ReviewerModelCandidate("codex", "gpt-workspace", "workspace", 0),
                ReviewerModelCandidate("codex", "gpt-fast", "profile fast", 20),
            )
        }
    )

    result = ui.collect(_request(tmp_path), preflight)

    assert dict(result.choices.review_models) == {"codex": "gpt-fast"}
    assert prompts[-2:] == ["Model [1]: ", "Model [1]: "]
    assert "Choose a number from 1 to 2." in output
    assert "  1. gpt-workspace (workspace)" in output
    assert "  2. gpt-fast (profile fast)" in output


def test_explicit_invalid_producer_is_left_for_plan_validation(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n"])
    ui = LineInitUI(input_fn=read, output_fn=lambda _: None, unicode=False, width=80)

    result = ui.collect(
        _request(
            tmp_path,
            producers=("codex-cli",),
            models={"codex": "gpt-explicit"},
        ),
        _preflight(
            available_producers=frozenset(),
            reviewer_model_candidates={},
        ),
    )

    assert result.choices.review_producers is None
    assert dict(result.choices.review_models) == {"codex": "gpt-explicit"}
    assert all(not prompt.startswith("Model") for prompt in prompts)


def test_line_disables_reviewer_without_a_configured_model(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(
        _request(tmp_path),
        _preflight(reviewer_model_candidates={}),
    )

    assert result.choices.review_producers == ()
    assert dict(result.choices.review_models) == {}
    assert all("review producer" not in prompt for prompt in prompts)
    assert "Codex CLI reviewer unavailable (model not configured)." in output


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("", ReviewDecision.CONFIRM),
        ("review", ReviewDecision.CONFIRM),
        ("back", ReviewDecision.BACK),
        ("cancel", ReviewDecision.CANCEL),
    ],
)
def test_line_review_returns_closed_decisions(
    tmp_path: Path,
    answer: str,
    expected: ReviewDecision,
) -> None:
    read, prompts = _sequence_input([answer])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    decision = ui.review(_plan(tmp_path))

    assert decision is expected
    assert prompts == ["Apply this plan? [Y/back/cancel] "]


def test_assume_yes_skips_only_final_review_input(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n", ""])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight())
    decision = ui.review(_plan(tmp_path), assume_yes=True)

    assert result.choices.integrations == ()
    assert dict(result.choices.review_models) == {"codex": "gpt-5-codex"}
    assert decision is ReviewDecision.CONFIRM
    assert prompts[-1] == "Select Codex CLI review producer? [Y/n] "


def test_line_collect_returns_closed_cancel_result(tmp_path: Path) -> None:
    read, _ = _sequence_input(["cancel"])
    initial = InitChoices(review_models={"codex": "kept"})
    ui = LineInitUI(input_fn=read, output_fn=lambda _: None, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight(), initial_choices=initial)

    assert result.decision is ChoiceCollectionDecision.CANCEL
    assert result.choices is initial


def test_line_github_setup_defaults_off_and_can_be_enabled(tmp_path: Path) -> None:
    default_read, default_prompts = _sequence_input([""])
    enabled_read, enabled_prompts = _sequence_input(["y"])
    default_ui = LineInitUI(
        input_fn=default_read, output_fn=lambda _: None, unicode=False, width=80
    )
    enabled_ui = LineInitUI(
        input_fn=enabled_read, output_fn=lambda _: None, unicode=False, width=80
    )
    request = _request(tmp_path)
    preflight = _preflight(github_available=True)

    assert default_ui.collect_github_setup(request, preflight) is GitHubDecision.SKIP
    assert enabled_ui.collect_github_setup(request, preflight) is GitHubDecision.CREATE
    assert default_prompts == ["Configure GitHub files and repository settings? [y/N] "]
    assert enabled_prompts == ["Configure GitHub files and repository settings? [y/N] "]


def test_explicit_setup_github_never_prompts(tmp_path: Path) -> None:
    request = replace(_request(tmp_path), setup_github=True)
    preflight = _preflight(github_available=False)
    line = LineInitUI(
        input_fn=lambda _: pytest.fail("line GitHub prompt called"),
        output_fn=lambda _: None,
        unicode=False,
        width=80,
    )
    guided, _ = _guided_ui(_FakePromptAdapter())

    assert line.collect_github_setup(request, preflight) is GitHubDecision.CREATE
    assert guided.collect_github_setup(request, preflight) is GitHubDecision.CREATE


@pytest.mark.parametrize("prompt_name", ["checkbox", "text", "select"])
def test_questionary_prompt_adapter_propagates_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, prompt_name: str
) -> None:
    import questionary

    question = questionary.text("unused")

    monkeypatch.setenv("PROMPT_TOOLKIT_NO_CPR", "previous")

    def interrupt(_patch_stdout: bool = False) -> None:
        assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "1"
        raise KeyboardInterrupt

    monkeypatch.setattr(question, "unsafe_ask", interrupt)
    monkeypatch.setattr(questionary, prompt_name, lambda *_args, **_kwargs: question)
    adapter = QuestionaryPromptAdapter()

    with pytest.raises(KeyboardInterrupt):
        if prompt_name == "checkbox":
            adapter.checkbox("Choose", (GuidedPromptOption("one", "One"),))
        elif prompt_name == "text":
            adapter.text("Model")
        else:
            adapter.select("Apply", (GuidedPromptOption("yes", "Yes"),))

    assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "previous"


def test_questionary_prompts_use_compact_native_chrome_and_restore_cpr_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import questionary

    calls: list[tuple[str, str, dict[str, Any]]] = []

    class _Question:
        def __init__(self, result: object) -> None:
            self._result = result

        def unsafe_ask(self) -> object:
            assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "1"
            return self._result

    def factory(name: str, result: object) -> Callable[..., _Question]:
        def create(message: str, **kwargs: Any) -> _Question:
            calls.append((name, message, kwargs))
            return _Question(result)

        return create

    monkeypatch.delenv("PROMPT_TOOLKIT_NO_CPR", raising=False)
    monkeypatch.setattr(questionary, "checkbox", factory("checkbox", []))
    monkeypatch.setattr(questionary, "select", factory("select", "confirm"))
    monkeypatch.setattr(questionary, "text", factory("text", "model"))
    adapter = QuestionaryPromptAdapter()

    adapter.checkbox("Integrations", (GuidedPromptOption("codex", "Codex"),))
    adapter.select("GitHub setup", (GuidedPromptOption("confirm", "Confirm"),))
    adapter.text("Model")

    checkbox = calls[0][2]
    select = calls[1][2]
    text = calls[2][2]
    assert checkbox["qmark"] == "◆"
    assert checkbox["pointer"] == "›"  # noqa: RUF001 - intentional pointer glyph
    assert checkbox["instruction"] == "(↑/↓ move · space select · enter confirm)"
    assert checkbox["choices"][0].title == [("class:choice", "Codex")]
    assert select["qmark"] == "◆"
    assert select["pointer"] == "›"  # noqa: RUF001 - intentional pointer glyph
    assert select["instruction"] == "(↑/↓ move · enter confirm)"
    assert text["qmark"] == "◆"
    assert "PROMPT_TOOLKIT_NO_CPR" not in os.environ


@pytest.mark.parametrize("prompt_name", ["checkbox", "text", "select"])
@pytest.mark.parametrize("raises", [False, True])
def test_questionary_cpr_guard_covers_prompt_construction_and_ask(
    monkeypatch: pytest.MonkeyPatch,
    prompt_name: str,
    raises: bool,
) -> None:
    import questionary

    class _Question:
        def unsafe_ask(self) -> object:
            assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "1"
            if raises:
                raise RuntimeError("prompt failed")
            return [] if prompt_name == "checkbox" else "answer"

    def construct(*_args: object, **_kwargs: object) -> _Question:
        assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "1"
        return _Question()

    monkeypatch.setenv("PROMPT_TOOLKIT_NO_CPR", "previous")
    monkeypatch.setattr(questionary, prompt_name, construct)
    adapter = QuestionaryPromptAdapter()

    def ask() -> object:
        if prompt_name == "checkbox":
            return adapter.checkbox("Integrations", (GuidedPromptOption("codex", "Codex"),))
        if prompt_name == "text":
            return adapter.text("Model")
        return adapter.select("GitHub setup", (GuidedPromptOption("skip", "Skip"),))

    if raises:
        with pytest.raises(RuntimeError, match="prompt failed"):
            ask()
    else:
        ask()

    assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "previous"


def test_questionary_prompt_chrome_has_an_ascii_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import questionary

    captured: dict[str, Any] = {}

    class _Question:
        def unsafe_ask(self) -> list[str]:
            return []

    def checkbox(_message: str, **kwargs: Any) -> _Question:
        captured.update(kwargs)
        return _Question()

    monkeypatch.setattr(questionary, "checkbox", checkbox)

    QuestionaryPromptAdapter(unicode=False).checkbox(
        "Integrations", (GuidedPromptOption("codex", "Codex"),)
    )

    assert captured["qmark"] == "?"
    assert captured["pointer"] == ">"
    assert captured["instruction"] == "(up/down move, space select, enter confirm)"
    captured["instruction"].encode("ascii")


@pytest.mark.parametrize(
    ("color", "expected_foreground"),
    [(True, "ansigreen"), (False, "")],
)
def test_questionary_checkbox_uses_color_aware_selection_style(
    monkeypatch: pytest.MonkeyPatch,
    color: bool,
    expected_foreground: str,
) -> None:
    from prompt_toolkit.styles import default_ui_style, merge_styles
    from questionary.question import Question

    effective_styles: list[Any] = []

    def capture_style(question: Question, _patch_stdout: bool = False) -> list[str]:
        application_style = question.application.style
        assert application_style is not None
        effective = merge_styles([default_ui_style(), application_style])
        effective_styles.append(effective)
        return []

    monkeypatch.setattr(Question, "unsafe_ask", capture_style)

    QuestionaryPromptAdapter(color=color).checkbox(
        "Choose",
        (GuidedPromptOption("codex", "Codex", checked=True),),
    )

    assert len(effective_styles) == 1
    selected = effective_styles[0].get_attrs_for_style_str("class:selected")
    unselected = effective_styles[0].get_attrs_for_style_str("class:text")
    choice = effective_styles[0].get_attrs_for_style_str("class:choice")
    assert selected.color == expected_foreground
    assert selected.reverse is False
    assert selected.bgcolor == ""
    assert unselected.dim is True
    assert unselected.reverse is False
    assert choice.color == ""
    assert choice.bgcolor == ""
    assert choice.reverse is False


def test_interactive_ui_passes_color_capability_to_questionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[tuple[bool, bool]] = []

    class CapturingPromptAdapter(_FakePromptAdapter):
        def __init__(self, *, color: bool = True, unicode: bool = True) -> None:
            received.append((color, unicode))
            super().__init__()

    monkeypatch.setattr(
        "super_harness.cli.init_ui.QuestionaryPromptAdapter",
        CapturingPromptAdapter,
    )

    InteractiveInitUI(color=False, unicode=False, renderer=_FakeGuidedRenderer())

    assert received == [(False, False)]


def test_keyboard_interrupt_from_line_input_propagates(tmp_path: Path) -> None:
    def interrupt(_: str) -> str:
        raise KeyboardInterrupt

    ui = LineInitUI(input_fn=interrupt, output_fn=lambda _: None, unicode=False, width=80)

    with pytest.raises(KeyboardInterrupt):
        ui.collect(_request(tmp_path), _preflight())


def test_plain_plan_and_event_output_contains_no_ansi_sequences(tmp_path: Path) -> None:
    output: list[str] = []
    ui = LineInitUI(
        input_fn=lambda _: "cancel",
        output_fn=output.append,
        unicode=True,
        width=80,
    )

    ui.render_plan(_plan(tmp_path))
    ui.render_event(
        StepRenderEvent(
            step_id="scaffold",
            state=StepRenderState.SUCCEEDED,
            detail="Created .harness/",
        )
    )

    assert "\x1b[" not in "\n".join(output)


def test_plan_hints_distinguish_writes_from_preserve_and_skip(tmp_path: Path) -> None:
    output: list[str] = []
    plan = replace(
        _plan(tmp_path),
        file_actions=(
            PlannedFileAction(Path("create.txt"), FileAction.CREATE),
            PlannedFileAction(Path("preserve.txt"), FileAction.PRESERVE),
            PlannedFileAction(Path("skip.txt"), FileAction.SKIP),
        ),
    )
    ui = NonInteractiveInitUI(
        output_fn=output.append,
        input_fn=lambda _: pytest.fail("input called"),
        unicode=False,
        width=80,
    )

    ui.render_plan(plan)

    text = "\n".join(output)
    assert "File create: create.txt\n  hint: will be written during apply" in text
    assert "File preserve: preserve.txt\n  hint: will be left unchanged" in text
    assert "File skip: skip.txt\n  hint: not part of this run" in text


def test_unicode_and_ascii_glyph_selection_is_independent_from_color() -> None:
    unicode_output: list[str] = []
    ascii_output: list[str] = []
    unicode_ui = NonInteractiveInitUI(
        output_fn=unicode_output.append,
        input_fn=lambda _: pytest.fail("input called"),
        unicode=True,
        color=False,
        width=80,
    )
    ascii_ui = NonInteractiveInitUI(
        output_fn=ascii_output.append,
        input_fn=lambda _: pytest.fail("input called"),
        unicode=False,
        color=True,
        width=80,
    )
    event = StepRenderEvent("scaffold", StepRenderState.SUCCEEDED, "Created")

    unicode_ui.render_event(event)
    ascii_ui.render_event(event)

    assert unicode_output == ["✓ scaffold: Created"]
    assert ascii_output == ["OK scaffold: Created"]
    assert "\x1b[" not in ascii_output[0]


def test_narrow_output_omits_secondary_hints_but_never_truncates_paths(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    plan = _plan(tmp_path)
    ui = NonInteractiveInitUI(
        output_fn=output.append,
        input_fn=lambda _: pytest.fail("input called"),
        unicode=False,
        width=20,
    )

    ui.render_plan(plan)

    text = "\n".join(output)
    path = str(plan.file_actions[0].path)
    assert path in text
    assert "gpt-5-codex" in text
    assert "will be written during apply" not in text
    assert "..." not in path


def test_noninteractive_collect_never_calls_input_or_derives_choices(tmp_path: Path) -> None:
    ui = NonInteractiveInitUI(
        output_fn=lambda _: None,
        input_fn=lambda _: pytest.fail("input called"),
        unicode=False,
        width=80,
    )

    result = ui.collect(
        _request(
            tmp_path,
            integrations=("codex",),
            producers=("codex-cli",),
            models={"codex": "gpt-explicit"},
        ),
        _preflight(),
    )

    assert result.decision is ChoiceCollectionDecision.REVIEW
    assert result.choices == InitChoices()


def test_noninteractive_review_never_calls_input(tmp_path: Path) -> None:
    output: list[str] = []
    ui = NonInteractiveInitUI(
        output_fn=output.append,
        input_fn=lambda _: pytest.fail("input called"),
        unicode=False,
        width=80,
    )

    decision = ui.review(_plan(tmp_path), assume_yes=False)

    assert decision is ReviewDecision.CONFIRM
    assert "\x1b[" not in "\n".join(output)


def test_nested_collection_results_are_immutable(tmp_path: Path) -> None:
    read, _ = _sequence_input(["n", "n"])
    ui = LineInitUI(input_fn=read, output_fn=lambda _: None, unicode=False, width=80)
    result = ui.collect(
        _request(tmp_path),
        _preflight(
            detected_integrations=(),
            available_integrations=frozenset(),
            detected_producers=(),
            available_producers=frozenset(),
        ),
    )

    with pytest.raises(FrozenInstanceError):
        result.decision = ChoiceCollectionDecision.CANCEL  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.choices.review_models["codex"] = "mutated"  # type: ignore[index]


def test_step_render_events_are_immutable() -> None:
    event = StepRenderEvent("scaffold", StepRenderState.STARTED, "Creating files")

    with pytest.raises(FrozenInstanceError):
        event.detail = "changed"  # type: ignore[misc]


class _FakeGuidedRenderer:
    def __init__(self) -> None:
        self.live_depth = 0
        self.stages: list[tuple[RailStage, RailState, str, str | None]] = []
        self.plans: list[InitPlan] = []
        self.validations: list[str] = []
        self.events: list[Any] = []

    def open_session(self) -> None:
        return None

    def close_session(self) -> None:
        return None

    def render_stage(
        self,
        stage: RailStage,
        state: RailState,
        detail: str,
        *,
        secondary: str | None = None,
    ) -> None:
        assert self.live_depth == 0
        self.stages.append((stage, state, detail, secondary))

    def render_plan(self, plan: InitPlan) -> None:
        assert self.live_depth == 0
        self.plans.append(plan)

    def render_validation(self, message: str) -> None:
        assert self.live_depth == 0
        self.validations.append(message)

    def render_event(self, event: Any) -> None:
        assert self.live_depth == 0
        self.events.append(event)


class _FakePromptAdapter:
    def __init__(
        self,
        *,
        checkboxes: Sequence[tuple[str, ...] | None | BaseException] = (),
        texts: Sequence[str | None | BaseException] = (),
        selects: Sequence[str | None | BaseException] = (),
        before_prompt: Callable[[], None] | None = None,
    ) -> None:
        self._checkboxes = iter(checkboxes)
        self._texts = iter(texts)
        self._selects = iter(selects)
        self._before_prompt = before_prompt or (lambda: None)
        self.checkbox_calls: list[tuple[str, tuple[GuidedPromptOption, ...]]] = []
        self.text_calls: list[tuple[str, str | None]] = []
        self.select_calls: list[tuple[str, tuple[GuidedPromptOption, ...], str | None]] = []
        self.calls: list[str] = []

    @staticmethod
    def _answer(value: Any) -> Any:
        if isinstance(value, BaseException):
            raise value
        return value

    def checkbox(
        self,
        message: str,
        choices: Sequence[GuidedPromptOption],
    ) -> tuple[str, ...] | None:
        self._before_prompt()
        self.calls.append(message)
        self.checkbox_calls.append((message, tuple(choices)))
        return self._answer(next(self._checkboxes))

    def text(self, message: str, *, default: str | None = None) -> str | None:
        self._before_prompt()
        self.calls.append(message)
        self.text_calls.append((message, default))
        return self._answer(next(self._texts))

    def select(
        self,
        message: str,
        choices: Sequence[GuidedPromptOption],
        *,
        default: str | None = None,
    ) -> str | None:
        self._before_prompt()
        self.calls.append(message)
        self.select_calls.append((message, tuple(choices), default))
        return self._answer(next(self._selects))


def _guided_ui(
    prompts: _FakePromptAdapter,
    renderer: _FakeGuidedRenderer | None = None,
) -> tuple[InteractiveInitUI, _FakeGuidedRenderer]:
    selected_renderer = renderer or _FakeGuidedRenderer()
    return (
        InteractiveInitUI(prompt_adapter=prompts, renderer=selected_renderer),
        selected_renderer,
    )


def test_guided_preselects_and_labels_detected_options_and_disables_missing_producer(
    tmp_path: Path,
) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[("codex", "claude-code"), ("codex-cli",)],
        texts=["gpt-5-codex"],
    )
    ui, _ = _guided_ui(prompts)

    result = ui.collect(
        _request(tmp_path),
        _preflight(
            available_integrations=frozenset({"codex"}),
            available_producers=frozenset({"codex-cli"}),
        ),
    )

    integrations = prompts.checkbox_calls[0][1]
    producers = prompts.checkbox_calls[1][1]
    assert [message for message, _ in prompts.checkbox_calls] == [
        "Integrations",
        "Automated reviewers",
    ]
    assert integrations[0].checked is True
    assert "detected · recommended" in integrations[0].title
    assert integrations[1].checked is False
    assert integrations[1].disabled is None
    assert "not detected" in integrations[1].title
    assert producers[0].checked is True
    assert producers[0].title == ("Codex reviewer — runs via Codex CLI  detected · recommended")
    assert producers[1].title == ("Claude reviewer — runs via Claude CLI  executable not found")
    assert producers[1].disabled == "executable not found"
    assert result.choices.integrations == ("codex", "claude-code")


def test_guided_skips_producer_checkbox_when_every_producer_is_unavailable(
    tmp_path: Path,
) -> None:
    prompts = _FakePromptAdapter(checkboxes=[()])
    ui, renderer = _guided_ui(prompts)

    result = ui.collect(
        _request(tmp_path),
        _preflight(detected_producers=(), available_producers=frozenset()),
    )

    assert [message for message, _ in prompts.checkbox_calls] == ["Integrations"]
    assert result.choices.review_producers == ()
    assert renderer.validations == [
        "No automated reviewers are ready; install a CLI and configure its model."
    ]


def test_guided_auto_selects_the_only_configured_model(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[(), ("codex-cli",)],
    )
    ui, _ = _guided_ui(prompts)

    result = ui.collect(_request(tmp_path), _preflight())

    assert dict(result.choices.review_models) == {"codex": "gpt-5-codex"}
    assert prompts.text_calls == []
    assert prompts.select_calls == []


def test_guided_github_setup_is_a_default_off_choice(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(selects=["create"])
    ui, _ = _guided_ui(prompts)

    decision = ui.collect_github_setup(
        _request(tmp_path),
        _preflight(github_available=True),
    )

    assert decision is GitHubDecision.CREATE
    message, choices, default = prompts.select_calls[0]
    assert message == "GitHub setup"
    assert [choice.value for choice in choices] == ["skip", "create"]
    assert default == "skip"


def test_guided_collects_github_after_integrations_and_reviewers(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[(), ("codex-cli",)],
        selects=["create"],
    )
    ui, _ = _guided_ui(prompts)

    result = ui.collect(
        _request(tmp_path),
        _preflight(github_available=True),
    )

    assert prompts.calls == [
        "Integrations",
        "Automated reviewers",
        "GitHub setup",
    ]
    assert result.choices.github_decision is GitHubDecision.CREATE


def test_guided_completes_configuration_after_github_resolution(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(selects=["create", "confirm"])
    ui, renderer = _guided_ui(prompts)
    request = replace(_request(tmp_path), no_agent=True)
    preflight = _preflight(
        detected_producers=(),
        available_producers=frozenset(),
        github_available=True,
    )

    def resolve_github() -> Any:
        assert not any(
            stage is RailStage.CONFIGURATION and state is RailState.COMPLETED
            for stage, state, _, _ in renderer.stages
        )
        return SimpleNamespace(
            root=tmp_path,
            pr_template=SimpleNamespace(
                inspection=SimpleNamespace(path=tmp_path / ".github" / "pull_request_template.md"),
                decision=GithubFileDecision.CREATE,
            ),
            workflow=SimpleNamespace(
                inspection=SimpleNamespace(
                    path=tmp_path / ".github" / "workflows" / "super-harness.yml"
                ),
                decision=GithubFileDecision.CREATE,
            ),
        )

    ui.prepare_plan(request, preflight, github_resolver=resolve_github)

    completed_index = next(
        index
        for index, (stage, state, _, _) in enumerate(renderer.stages)
        if stage is RailStage.CONFIGURATION and state is RailState.COMPLETED
    )
    review_index = next(
        index
        for index, (stage, state, _, _) in enumerate(renderer.stages)
        if stage is RailStage.REVIEW and state is RailState.CURRENT
    )
    assert completed_index < review_index


def test_guided_selects_from_multiple_configured_models(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[(), ("codex-cli",)],
        selects=["gpt-fast"],
    )
    ui, _ = _guided_ui(prompts)
    preflight = _preflight(
        reviewer_model_candidates={
            "codex": (
                ReviewerModelCandidate("codex", "gpt-workspace", "existing workspace profile", 0),
                ReviewerModelCandidate("codex", "gpt-fast", "Codex CLI profile fast", 20),
            )
        }
    )

    result = ui.collect(_request(tmp_path), preflight)

    message, choices, default = prompts.select_calls[0]
    assert message == "Model for Codex reviewer"
    assert [choice.value for choice in choices] == ["gpt-workspace", "gpt-fast"]
    assert [choice.title for choice in choices] == [
        "gpt-workspace  existing workspace profile",
        "gpt-fast  Codex CLI profile fast",
    ]
    assert default == "gpt-workspace"
    assert dict(result.choices.review_models) == {"codex": "gpt-fast"}
    assert prompts.text_calls == []


def test_guided_uses_provider_error_only_for_the_affected_reviewer(
    tmp_path: Path,
) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[(), ("claude-cli",)],
    )
    ui, _ = _guided_ui(prompts)
    preflight = _preflight(
        detected_producers=("codex-cli", "claude-cli"),
        available_producers=frozenset({"codex-cli", "claude-cli"}),
        reviewer_model_candidates={
            "claude": (
                ReviewerModelCandidate("claude", "opus-configured", "Claude CLI config", 10),
            )
        },
        reviewer_model_errors={"codex": "Codex CLI config is not valid TOML"},
    )

    result = ui.collect(_request(tmp_path), preflight)

    producer_options = prompts.checkbox_calls[1][1]
    assert producer_options[0].disabled == "Codex CLI config is not valid TOML"
    assert producer_options[1].disabled is None
    assert dict(result.choices.review_models) == {"claude": "opus-configured"}


def test_guided_explicit_model_bypasses_candidate_selection(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(checkboxes=[()])
    ui, _ = _guided_ui(prompts)

    result = ui.collect(
        _request(
            tmp_path,
            producers=("codex-cli",),
            models={"codex": "gpt-explicit"},
        ),
        _preflight(reviewer_model_candidates={}),
    )

    assert dict(result.choices.review_models) == {"codex": "gpt-explicit"}
    assert prompts.text_calls == []
    assert prompts.select_calls == []


def test_guided_cancel_from_model_selection_cancels_configuration(
    tmp_path: Path,
) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[(), ("codex-cli",)],
        selects=[None],
    )
    ui, _ = _guided_ui(prompts)
    preflight = _preflight(
        reviewer_model_candidates={
            "codex": (
                ReviewerModelCandidate("codex", "gpt-one", "one", 10),
                ReviewerModelCandidate("codex", "gpt-two", "two", 20),
            )
        }
    )

    result = ui.collect(_request(tmp_path), preflight)

    assert result.decision is ChoiceCollectionDecision.CANCEL
    assert prompts.text_calls == []


def test_guided_run_back_reuses_choices_then_returns_revised_plan(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[
            ("codex",),
            ("codex-cli",),
            ("claude-code",),
            ("codex-cli",),
        ],
        texts=["gpt-5-codex"],
        selects=["back", "confirm"],
    )
    ui, renderer = _guided_ui(prompts)

    result = ui.run(_request(tmp_path), _preflight())

    assert result.decision is WizardDecision.CONFIRM
    assert result.plan is not None
    assert result.plan.integrations == ("claude-code",)
    second_integrations = prompts.checkbox_calls[2][1]
    assert [option.checked for option in second_integrations] == [True, False]
    assert prompts.text_calls == []
    assert len(renderer.plans) == 2


def test_guided_confirm_returns_an_immutable_result(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[("codex",), ("codex-cli",)],
        texts=["gpt-5-codex"],
        selects=["confirm"],
    )
    ui, _ = _guided_ui(prompts)

    result = ui.run(_request(tmp_path), _preflight())

    assert result.decision is WizardDecision.CONFIRM
    assert result.plan is not None
    with pytest.raises(FrozenInstanceError):
        result.plan = None  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.plan.review_models["codex"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize("cancel_at", ["configuration", "review"])
def test_guided_none_maps_to_explicit_cancel_with_no_plan(
    tmp_path: Path,
    cancel_at: str,
) -> None:
    if cancel_at == "configuration":
        prompts = _FakePromptAdapter(checkboxes=[None])
    else:
        prompts = _FakePromptAdapter(
            checkboxes=[("codex",), ("codex-cli",)],
            texts=["gpt-5-codex"],
            selects=[None],
        )
    ui, _ = _guided_ui(prompts)

    result = ui.run(_request(tmp_path), _preflight())

    assert result.decision is WizardDecision.CANCEL
    assert result.plan is None


def test_guided_explicit_cancel_has_no_plan(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[("codex",), ("codex-cli",)],
        texts=["gpt-5-codex"],
        selects=["cancel"],
    )
    ui, _ = _guided_ui(prompts)

    result = ui.run(_request(tmp_path), _preflight())

    assert result.decision is WizardDecision.CANCEL
    assert result.plan is None


def test_guided_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(checkboxes=[KeyboardInterrupt()])
    ui, _ = _guided_ui(prompts)

    with pytest.raises(KeyboardInterrupt):
        ui.run(_request(tmp_path), _preflight())


def test_guided_assume_yes_skips_only_review(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[("codex",), ("codex-cli",)],
    )
    ui, _ = _guided_ui(prompts)

    result = ui.run(_request(tmp_path), _preflight(), assume_yes=True)

    assert result.decision is WizardDecision.CONFIRM
    assert result.plan is not None
    assert dict(result.plan.review_models) == {"codex": "gpt-5-codex"}
    assert len(prompts.checkbox_calls) == 2
    assert prompts.text_calls == []
    assert prompts.select_calls == []


def test_guided_never_has_a_live_renderer_while_any_prompt_owns_input(
    tmp_path: Path,
) -> None:
    renderer = _FakeGuidedRenderer()
    prompts = _FakePromptAdapter(
        checkboxes=[("codex",), ("codex-cli",)],
        texts=["gpt-model"],
        selects=["confirm"],
        before_prompt=lambda: (
            renderer.live_depth == 0 or pytest.fail("Rich live display active during prompt")
        ),
    )
    ui, _ = _guided_ui(prompts, renderer)

    ui.run(_request(tmp_path), _preflight())

    assert len(prompts.checkbox_calls) + len(prompts.text_calls) + len(prompts.select_calls) == 3


def test_guided_completed_rail_order_and_five_stage_visibility(tmp_path: Path) -> None:
    prompts = _FakePromptAdapter(
        checkboxes=[("codex",), ("codex-cli",)],
        texts=["gpt-model"],
        selects=["confirm"],
    )
    ui, renderer = _guided_ui(prompts)

    ui.run(_request(tmp_path), _preflight())

    completed = [stage for stage, state, _, _ in renderer.stages if state is RailState.COMPLETED]
    visible = {stage for stage, _, _, _ in renderer.stages}
    assert completed == [RailStage.PREFLIGHT, RailStage.CONFIGURATION, RailStage.REVIEW]
    assert visible == {RailStage.PREFLIGHT, RailStage.CONFIGURATION, RailStage.REVIEW}


def test_rich_guided_glyphs_are_independent_of_color() -> None:
    unicode_buffer = StringIO()
    ascii_buffer = StringIO()
    unicode_renderer = RichGuidedRenderer(
        console=Console(file=unicode_buffer, width=80, color_system=None),
        unicode=True,
        color=False,
        width=80,
    )
    ascii_renderer = RichGuidedRenderer(
        console=Console(file=ascii_buffer, width=80, color_system="standard"),
        unicode=False,
        color=True,
        width=80,
    )

    unicode_renderer.render_stage(RailStage.PREFLIGHT, RailState.CURRENT, "Inspecting")
    ascii_renderer.render_stage(RailStage.PREFLIGHT, RailState.CURRENT, "Inspecting")
    ascii_renderer.render_stage(RailStage.APPLY, RailState.COMPLETED, "Applied")
    ascii_renderer.render_stage(RailStage.OUTCOME, RailState.FAILED, "Failed")

    assert "◆  preflight: Inspecting" in unicode_buffer.getvalue()
    ascii_text = ascii_buffer.getvalue()
    assert "+  preflight: Inspecting" in ascii_text
    assert "*  apply: Applied" in ascii_text
    assert "x  outcome: Failed" in ascii_text


def test_rich_guided_review_is_compact_and_groups_file_actions(tmp_path: Path) -> None:
    buffer = StringIO()
    renderer = RichGuidedRenderer(
        console=Console(file=buffer, width=120, color_system=None),
        unicode=True,
        color=False,
        width=120,
    )
    plan = replace(
        _plan(tmp_path),
        integrations=("codex", "claude-code"),
        review_producers=("codex-cli", "claude-cli"),
        review_models={"codex": "gpt-5.6-sol", "claude": "opus[1m]"},
        github_decision=GitHubDecision.CREATE,
        integration_plans={
            "codex": AgentIntegrationPlan(
                "codex",
                "/usr/local/bin/super-harness-hook",
                "/usr/local/bin/super-harness",
                SettingsMergePlan(
                    path=tmp_path / ".codex" / "hooks.json",
                    original_bytes=b"{}\n",
                    desired_bytes=b'{"hooks": {}}\n',
                    changed=True,
                    backup_required=True,
                ),
            ),
            "claude-code": AgentIntegrationPlan(
                "claude-code",
                "/usr/local/bin/super-harness-hook",
                "/usr/local/bin/super-harness",
                SettingsMergePlan(
                    path=tmp_path / ".claude" / "settings.local.json",
                    original_bytes=b"{}\n",
                    desired_bytes=b'{"hooks": {}}\n',
                    changed=True,
                    backup_required=True,
                ),
            ),
        },
        file_actions=(
            PlannedFileAction(tmp_path / ".harness" / "state.yaml", FileAction.UPDATE),
            PlannedFileAction(tmp_path / ".harness" / "sensors.yaml", FileAction.UPDATE),
            PlannedFileAction(tmp_path / "AGENTS.md", FileAction.UPDATE),
            PlannedFileAction(tmp_path / ".codex" / "hooks.json", FileAction.UPDATE),
            PlannedFileAction(
                tmp_path / ".github" / "workflows" / "super-harness.yml",
                FileAction.CREATE,
            ),
            PlannedFileAction(tmp_path / ".harness" / "events.jsonl", FileAction.PRESERVE),
            PlannedFileAction(tmp_path / ".harness" / "state.yaml", FileAction.SKIP),
        ),
    )

    renderer.render_plan(plan)

    text = buffer.getvalue()
    lines = text.splitlines()
    compact = "".join(line.lstrip("│|").strip() for line in text.splitlines())
    assert lines[0] == "┌ super-harness init"
    assert all(line.startswith("│") for line in lines[1:-1])
    assert lines[-1] == "└"
    assert text.count("└") == 1
    assert "Integrations" in text
    assert "Codex" in text
    assert "Claude Code" in text
    assert "Automated reviewers" in text
    assert "Codex  gpt-5.6-sol" in text
    assert "Claude  opus[1m]" in text
    assert "GitHub" in text
    assert "Ensure workflow and PR template" in text
    assert "Files" in text
    assert "Update    4 files" in text
    assert "Create    1 file" in text
    assert "Preserve  1 file" in text
    assert "Skip      1 file" in text
    assert "Back up   2 settings files" in text
    assert ".harness configuration (2 files)" in text
    assert str(tmp_path / "AGENTS.md") in compact
    assert str(tmp_path / ".codex" / "hooks.json") in compact
    assert str(tmp_path / ".claude" / "settings.local.json") in compact
    assert str(tmp_path / ".harness" / "events.jsonl") in compact
    assert str(tmp_path / ".harness" / "state.yaml") not in compact
    assert "hint:" not in text
    assert "will be written during apply" not in text
    assert "File update:" not in text


def test_rich_guided_explicit_session_keeps_review_open_until_final_outcome(
    tmp_path: Path,
) -> None:
    buffer = StringIO()
    renderer = RichGuidedRenderer(
        console=Console(file=buffer, width=100, color_system=None),
        unicode=True,
        color=False,
        width=100,
    )

    renderer.open_session()
    renderer.render_plan(_plan(tmp_path))
    assert "└" not in buffer.getvalue()
    renderer.render_stage(
        RailStage.OUTCOME,
        RailState.COMPLETED,
        "Setup complete in 152ms",
        secondary="Next: super-harness status",
    )
    renderer.close_session()
    renderer.close_session()

    text = buffer.getvalue()
    assert text.count("┌ super-harness init") == 1
    assert text.count("└") == 1
    assert text.index("┌ super-harness init") < text.index("Setup complete in 152ms")
    assert text.index("Setup complete in 152ms") < text.index("└")


def test_rich_guided_narrow_output_drops_hints_and_wraps_paths(tmp_path: Path) -> None:
    buffer = StringIO()
    renderer = RichGuidedRenderer(
        console=Console(file=buffer, width=24, color_system=None),
        unicode=False,
        color=False,
        width=24,
    )
    plan = _plan(tmp_path)

    renderer.render_plan(plan)

    text = buffer.getvalue()
    compact = "".join(line.lstrip("│|").strip() for line in text.splitlines())
    assert str(plan.file_actions[0].path) in compact
    assert "will be written during apply" not in text
    assert "..." not in text
    assert len(text.splitlines()) > len(plan.file_actions)


def test_rich_guided_cjk_windows_path_wraps_inside_every_review_rail_line(
    tmp_path: Path,
) -> None:
    buffer = StringIO()
    renderer = RichGuidedRenderer(
        console=Console(file=buffer, width=22, color_system=None),
        unicode=True,
        color=False,
        width=22,
    )
    windows_path = Path(r"C:\项目\非常长的配置目录\设置文件.yaml")
    plan = replace(
        _plan(tmp_path),
        file_actions=(PlannedFileAction(windows_path, FileAction.CREATE),),
    )

    renderer.render_plan(plan)

    lines = buffer.getvalue().splitlines()
    assert lines[0] == "┌ super-harness init"
    assert lines[-1] == "└"
    assert all(line.startswith("│") for line in lines[1:-1])
    assert str(windows_path) in "".join(line.lstrip("│").strip() for line in lines)


def test_guided_step_rendering_accepts_plain_structural_event() -> None:
    renderer = _FakeGuidedRenderer()
    ui, _ = _guided_ui(_FakePromptAdapter(), renderer)
    event = StepRenderEvent("scaffold", StepRenderState.SUCCEEDED, "Created")

    ui.render_event(event)

    assert renderer.events == [event]


def test_rich_guided_apply_groups_successes_without_internal_step_ids() -> None:
    buffer = StringIO()
    renderer = RichGuidedRenderer(
        console=Console(file=buffer, width=100, color_system=None),
        unicode=True,
        color=False,
        width=100,
    )
    events = (
        StepRenderEvent("scaffold", StepRenderState.STARTED, "Scaffolding .harness."),
        StepRenderEvent("scaffold", StepRenderState.SUCCEEDED, "Scaffolded .harness."),
        StepRenderEvent(
            "skeleton_config", StepRenderState.SUCCEEDED, "Wrote skeleton configuration."
        ),
        StepRenderEvent(
            "review_config", StepRenderState.SUCCEEDED, "Configured review configuration."
        ),
        StepRenderEvent(
            "agent_integrations",
            StepRenderState.SUCCEEDED,
            "Codex and Claude Code integrations configured.",
        ),
        StepRenderEvent("agents_md", StepRenderState.SUCCEEDED, "Updated AGENTS.md."),
        StepRenderEvent("gitignore", StepRenderState.SUCCEEDED, "Updated .gitignore."),
        StepRenderEvent("github", StepRenderState.SUCCEEDED, "GitHub files ensured."),
    )

    for event in events:
        renderer.render_event(event)

    text = buffer.getvalue()
    assert text.count("apply: Applying setup") == 1
    assert "Harness configuration ready" in text
    assert "Codex and Claude Code integrations configured" in text
    assert "AGENTS.md and .gitignore updated" in text
    assert "GitHub files ensured" in text
    for internal_id in (
        "scaffold:",
        "skeleton_config",
        "review_config",
        "agent_integrations",
        "agents_md",
        "gitignore:",
        "github:",
    ):
        assert internal_id not in text


def test_rich_guided_apply_keeps_github_warning_actionable() -> None:
    buffer = StringIO()
    renderer = RichGuidedRenderer(
        console=Console(file=buffer, width=100, color_system=None),
        unicode=True,
        color=False,
        width=100,
    )

    renderer.render_event(
        StepRenderEvent(
            "github",
            StepRenderState.WARNED,
            "GitHub repository settings need manual confirmation. "
            "Settings -> General -> Pull Requests.",
        )
    )

    text = buffer.getvalue()
    compact = " ".join(text.split())
    assert "GitHub setup" in text
    assert "Settings -> General -> Pull Requests" in compact
    assert "github:" not in text


@pytest.mark.parametrize(
    ("success", "message", "next_command", "recovery_command", "expected_secondary"),
    [
        (True, None, "super-harness status", None, "Next: super-harness status"),
        (
            False,
            "GitHub setup failed",
            None,
            "gh auth login && super-harness init --force",
            "Recovery: gh auth login && super-harness init --force",
        ),
    ],
)
def test_guided_outcome_includes_the_next_or_recovery_command(
    success: bool,
    message: str | None,
    next_command: str | None,
    recovery_command: str | None,
    expected_secondary: str,
) -> None:
    renderer = _FakeGuidedRenderer()
    ui, _ = _guided_ui(_FakePromptAdapter(), renderer)
    result = SimpleNamespace(
        success=success,
        message=message,
        next_command=next_command,
        recovery_command=recovery_command,
        elapsed_ms=0,
    )

    ui.render_outcome(result)

    assert renderer.stages[-1][3] == expected_secondary


@pytest.mark.parametrize(
    ("success", "message", "elapsed_ms", "expected_detail"),
    [
        (True, None, 152, "Setup complete in 152ms"),
        (True, None, 1_200, "Setup complete in 1.2s"),
        (False, "Setup failed", 1_200, "Setup failed after 1.2s"),
    ],
)
def test_guided_outcome_formats_truthful_elapsed_time(
    success: bool,
    message: str | None,
    elapsed_ms: int,
    expected_detail: str,
) -> None:
    renderer = _FakeGuidedRenderer()
    ui, _ = _guided_ui(_FakePromptAdapter(), renderer)
    result = SimpleNamespace(
        success=success,
        message=message,
        next_command="super-harness status" if success else None,
        recovery_command=None if success else "super-harness init --force",
        elapsed_ms=elapsed_ms,
    )

    ui.render_outcome(result)

    assert renderer.stages == [
        (
            RailStage.OUTCOME,
            RailState.COMPLETED if success else RailState.FAILED,
            expected_detail,
            (
                "Next: super-harness status"
                if success
                else "Recovery: super-harness init --force"
            ),
        )
    ]


def test_guided_already_initialized_is_one_status_first_recovery_block(
    tmp_path: Path,
) -> None:
    renderer = _FakeGuidedRenderer()
    ui, _ = _guided_ui(_FakePromptAdapter(), renderer)

    ui.render_already_initialized(tmp_path / ".harness")

    assert renderer.stages == [
        (
            RailStage.OUTCOME,
            RailState.COMPLETED,
            "Already initialized",
            (
                "Next: super-harness status; "
                "Review/reconfigure: super-harness init --force"
            ),
        )
    ]


def test_guided_prompt_interruption_renders_a_terminal_outcome() -> None:
    renderer = _FakeGuidedRenderer()
    ui, _ = _guided_ui(_FakePromptAdapter(), renderer)

    ui.render_interrupted()

    assert renderer.stages == [
        (RailStage.APPLY, RailState.PENDING, "No writes started", None),
        (RailStage.OUTCOME, RailState.FAILED, "Setup interrupted", None),
    ]
