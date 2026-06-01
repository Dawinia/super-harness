"""Tests for `GroupAwareCommand` — top-level-flag misposition redirect.

Covers OPEN-ITEMS #6 S8-misleading: when the user puts a top-level flag
(`--json` / `--quiet` / `-q` / `--verbose` / `-v` / `--workspace`) at a
subcommand position, Click's default "Try '<subcommand> --help' for help"
hint is misleading because the rejected option is group-level and will
not appear in the subcommand's `--help`.

After Phase-fixup S8-misleading, the CLI catches this specific class of
`NoSuchOption` and emits a corrective hint pointing the user at the
correct slot:

    Error: '--json' is a top-level flag of `super-harness`, not a
           subcommand flag. Try: `super-harness --json verify ...`
"""
from __future__ import annotations

from click.testing import CliRunner

from super_harness.cli import main


def _invoke(args: list[str]):
    """Invoke `main` with stable prog_name so command_path renders correctly."""
    return CliRunner().invoke(main, args, prog_name="super-harness")


def test_json_at_subcommand_position_emits_redirect_hint() -> None:
    result = _invoke(["verify", "--json"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "--json" in result.output
    # The corrected position should be shown.
    assert "super-harness --json verify" in result.output


def test_quiet_at_subcommand_position_emits_redirect_hint() -> None:
    result = _invoke(["done", "--quiet"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "--quiet" in result.output
    assert "super-harness --quiet done" in result.output


def test_workspace_at_subcommand_position_emits_redirect_hint() -> None:
    result = _invoke(["verify", "--workspace", "/tmp"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "--workspace" in result.output
    assert "super-harness --workspace verify" in result.output


def test_verbose_at_subcommand_position_emits_redirect_hint() -> None:
    result = _invoke(["verify", "--verbose"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "--verbose" in result.output
    assert "super-harness --verbose verify" in result.output


def test_short_quiet_at_subcommand_position_emits_redirect_hint() -> None:
    """Short alias `-q` for `--quiet` is also a top-level flag."""
    result = _invoke(["verify", "-q"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "-q" in result.output
    assert "super-harness -q verify" in result.output


def test_short_verbose_at_subcommand_position_emits_redirect_hint() -> None:
    """Short alias `-v` for `--verbose` is also a top-level flag."""
    result = _invoke(["verify", "-v"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "-v" in result.output
    assert "super-harness -v verify" in result.output


def test_version_at_subcommand_position_emits_redirect_hint() -> None:
    """`--version` is an eager top-level flag — mispositioning it should
    still get the redirect (the example "Try: super-harness --version verify"
    is slightly imperfect for eager flags, but points the user in the right
    direction)."""
    result = _invoke(["verify", "--version"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "--version" in result.output
    assert "super-harness --version verify" in result.output


def test_short_version_at_subcommand_position_emits_redirect_hint() -> None:
    """Short alias `-V` for `--version` is also a top-level flag."""
    result = _invoke(["done", "-V"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "-V" in result.output
    assert "super-harness -V done" in result.output


def test_nested_subgroup_redirect_includes_full_command_chain() -> None:
    """`change start --json some-slug` should redirect with full chain."""
    result = _invoke(["change", "start", "--json", "some-slug"])
    assert result.exit_code == 2
    assert "top-level flag" in result.output
    assert "--json" in result.output
    # Hint must preserve the full nested chain so user knows where to put
    # the moved flag.
    assert "super-harness --json change start" in result.output


def test_unknown_subcommand_flag_keeps_original_click_message() -> None:
    """Non-top-level rejected options must preserve original Click error.

    We assert that the redirect hint text does NOT appear, and that
    Click's default "No such option" wording is still present.
    """
    result = _invoke(["verify", "--not-a-real-flag"])
    assert result.exit_code == 2
    assert "top-level flag" not in result.output
    assert "No such option" in result.output
    assert "--not-a-real-flag" in result.output


def test_typo_of_top_level_flag_keeps_original_click_behavior() -> None:
    """`--jsom` is a typo, NOT a top-level flag — preserve Click's suggestion.

    Click 8.x includes a "Did you mean" suggestion for close typos. The
    custom handler must not interfere because the option isn't in the
    top-level set.
    """
    result = _invoke(["verify", "--jsom"])
    assert result.exit_code == 2
    assert "top-level flag" not in result.output
    assert "No such option" in result.output
    assert "--jsom" in result.output


def test_correctly_positioned_top_level_flag_is_unaffected() -> None:
    """`super-harness --help` should succeed normally — the custom Command
    class must not interfere with valid invocations."""
    result = _invoke(["--help"])
    assert result.exit_code == 0
    assert "super-harness" in result.output


def test_correctly_positioned_json_then_subcommand_help_unaffected() -> None:
    """`super-harness --json verify --help` flows normally; the override
    must not regress correct usage."""
    result = _invoke(["--json", "verify", "--help"])
    assert result.exit_code == 0
    assert "verify" in result.output.lower()
