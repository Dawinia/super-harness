"""Direct verdict commands cannot create new-contract authority."""

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.exit_codes import EXIT_VALIDATION


@pytest.mark.parametrize("command", ["approve", "reject", "skip"])
def test_direct_verdict_commands_are_retired(command: str) -> None:
    result = CliRunner().invoke(main, ["review", command, "c"])

    assert result.exit_code == EXIT_VALIDATION
    assert "reviewer execution command was removed" in result.output
