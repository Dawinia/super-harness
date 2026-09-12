"""The old reviewer-run namespace is retained only as fail-loud history."""

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.exit_codes import EXIT_VALIDATION


def test_reviewer_run_failure_command_is_retired() -> None:
    result = CliRunner().invoke(main, ["review", "run", "fail", "c"])

    assert result.exit_code == EXIT_VALIDATION
    assert "reviewer execution command was removed" in result.output
