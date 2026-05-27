from click.testing import CliRunner

from super_harness.cli import main


def test_version_flag():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_help_flag():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "super-harness" in result.output


def test_help_short_flag():
    result = CliRunner().invoke(main, ["-h"])
    assert result.exit_code == 0
    assert "super-harness" in result.output
