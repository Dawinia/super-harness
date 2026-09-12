"""The old review packet command is retained only as a fail-loud namespace."""

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.exit_codes import EXIT_VALIDATION


def test_prepare_is_retired() -> None:
    result = CliRunner().invoke(main, ["review", "prepare", "c"])

    assert result.exit_code == EXIT_VALIDATION
    assert "reviewer execution command was removed" in result.output
