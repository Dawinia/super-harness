"""Contract tests for the retired reviewer orchestration surface."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.review import _model_contradicts
from super_harness.exit_codes import EXIT_VALIDATION


@pytest.mark.parametrize(
    "args",
    [
        ("approve", "c"),
        ("reject", "c"),
        ("skip", "c"),
        ("prepare", "c"),
        ("begin", "c"),
        ("authorize", "c"),
        ("result", "import", "c"),
        ("run", "fail", "c"),
        ("human", "inspect", "c"),
        ("human", "draft", "c"),
        ("human", "confirm", "c"),
    ],
)
def test_retired_review_commands_fail_loudly(args: tuple[str, ...]) -> None:
    result = CliRunner().invoke(main, ["review", *args])

    assert result.exit_code == EXIT_VALIDATION
    assert "reviewer execution command was removed" in result.output
    assert "review import" in result.output


def test_review_help_exposes_external_import_only() -> None:
    result = CliRunner().invoke(main, ["review", "--help"])

    assert result.exit_code == 0
    assert "import" in result.output
    assert "prepare" in result.output  # historical command remains fail-loud and discoverable


@pytest.mark.parametrize(
    ("requested", "actual", "contradicts"),
    [
        ("gpt-5 [fast]", "gpt-5 [fast]", False),
        ("gpt-5", "gpt-5 [fast]", False),
        ("gpt-5 [fast]", "gpt-5 [slow]", True),
        ("gpt-5", "claude", True),
        ("", "gpt-5", False),
    ],
)
def test_historical_model_qualifier_predicate(
    requested: str, actual: str, contradicts: bool
) -> None:
    assert _model_contradicts(requested, actual) is contradicts
