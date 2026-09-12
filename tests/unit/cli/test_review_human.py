"""The old TTY review namespace is retained only as fail-loud history."""

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.exit_codes import EXIT_VALIDATION


@pytest.mark.parametrize("command", ["inspect", "draft", "confirm"])
def test_human_review_commands_are_retired(command: str) -> None:
    result = CliRunner().invoke(main, ["review", "human", command, "c"])

    assert result.exit_code == EXIT_VALIDATION
    assert "reviewer execution command was removed" in result.output
