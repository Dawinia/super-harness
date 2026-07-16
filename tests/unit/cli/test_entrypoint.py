import sys

import click
from click.testing import CliRunner

from super_harness.cli import main

COMMAND_NAMES = [
    "state",
    "event",
    "init",
    "change",
    "status",
    "report",
    "sensor",
    "gate",
    "observe",
    "adapter",
    "verify",
    "done",
    "sync",
    "verification",
    "pr",
    "review",
    "plan",
    "implementation",
    "on-merge",
    "attest",
    "decision",
    "doc",
]

COMMAND_MODULES = {f"super_harness.cli.{name.replace('-', '_')}" for name in COMMAND_NAMES}


def test_version_flag():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_help_flag():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "super-harness" in result.output
    assert "--workspace" in result.output  # actual option must appear
    assert "--version" in result.output  # actual option must appear


def test_root_command_order_is_stable() -> None:
    assert main.list_commands(click.Context(main)) == COMMAND_NAMES


def test_root_help_lists_commands_without_importing_command_modules() -> None:
    for module_name in COMMAND_MODULES:
        sys.modules.pop(module_name, None)

    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    positions = [result.output.index(f"  {name}") for name in COMMAND_NAMES]
    assert positions == sorted(positions)
    assert COMMAND_MODULES.isdisjoint(sys.modules)


def test_help_short_flag():
    result = CliRunner().invoke(main, ["-h"])
    assert result.exit_code == 0
    assert "super-harness" in result.output
    assert "--workspace" in result.output
    assert "--version" in result.output
