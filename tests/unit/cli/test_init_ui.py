from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from super_harness.cli.init_plan import (
    FileAction,
    GitHubDecision,
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
    LineInitUI,
    NonInteractiveInitUI,
    ReviewDecision,
    StepRenderEvent,
    StepRenderState,
    TerminalCapabilities,
    detect_terminal_capabilities,
)


def _preflight(
    *,
    detected_integrations: tuple[str, ...] = ("codex",),
    available_integrations: frozenset[str] = frozenset({"codex"}),
    detected_producers: tuple[str, ...] = ("codex-cli",),
    available_producers: frozenset[str] = frozenset({"codex-cli"}),
) -> InitPreflight:
    return InitPreflight(
        harness_state=HarnessState.ABSENT,
        existing_file_bytes={},
        available_integrations=available_integrations,
        available_review_producers=available_producers,
        detected_integrations=detected_integrations,
        detected_review_producers=detected_producers,
        github_available=False,
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
    read, prompts = _sequence_input(["", "y", "", "gpt-5-codex"])
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
        "Model for Codex CLI: ",
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


def test_blank_model_input_repeats_until_non_empty(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n", "", "", "  ", "gpt-5-codex"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight())

    assert dict(result.choices.review_models) == {"codex": "gpt-5-codex"}
    assert prompts.count("Model for Codex CLI: ") == 3
    assert output.count("A model is required.") == 2


def test_explicit_invalid_producer_is_left_for_plan_validation(tmp_path: Path) -> None:
    read, prompts = _sequence_input(["n", "n", "gpt-explicit"])
    ui = LineInitUI(input_fn=read, output_fn=lambda _: None, unicode=False, width=80)

    result = ui.collect(
        _request(tmp_path, producers=("codex-cli",)),
        _preflight(available_producers=frozenset()),
    )

    assert result.choices.review_producers is None
    assert dict(result.choices.review_models) == {"codex": "gpt-explicit"}
    assert prompts[-1] == "Model for Codex CLI: "


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
    read, prompts = _sequence_input(["n", "n", "", "gpt-model"])
    output: list[str] = []
    ui = LineInitUI(input_fn=read, output_fn=output.append, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight())
    decision = ui.review(_plan(tmp_path), assume_yes=True)

    assert result.choices.integrations == ()
    assert dict(result.choices.review_models) == {"codex": "gpt-model"}
    assert decision is ReviewDecision.CONFIRM
    assert prompts[-1] == "Model for Codex CLI: "


def test_line_collect_returns_closed_cancel_result(tmp_path: Path) -> None:
    read, _ = _sequence_input(["cancel"])
    initial = InitChoices(review_models={"codex": "kept"})
    ui = LineInitUI(input_fn=read, output_fn=lambda _: None, unicode=False, width=80)

    result = ui.collect(_request(tmp_path), _preflight(), initial_choices=initial)

    assert result.decision is ChoiceCollectionDecision.CANCEL
    assert result.choices is initial


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
