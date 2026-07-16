import subprocess
import sys

import click
from click.testing import CliRunner

from super_harness.cli import main

COMMAND_NAMES = [
    "adapter",
    "attest",
    "change",
    "decision",
    "doc",
    "done",
    "event",
    "gate",
    "implementation",
    "init",
    "observe",
    "on-merge",
    "plan",
    "pr",
    "report",
    "review",
    "sensor",
    "state",
    "status",
    "sync",
    "verification",
    "verify",
]

COMMAND_MODULES = {f"super_harness.cli.{name.replace('-', '_')}" for name in COMMAND_NAMES}

FORBIDDEN_INIT_IMPORTS = {
    "fcntl",
    "super_harness.core.writer",
    "super_harness.core.post_emit",
    "super_harness.daemon.server",
    "super_harness.daemon.supervisor",
}


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


def test_resolving_init_does_not_import_posix_lifecycle_modules() -> None:
    blocked = repr(FORBIDDEN_INIT_IMPORTS)
    code = f"""
import importlib.abc
import sys
import click

blocked = {blocked}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError(f"forbidden import: {{fullname}}")
        return None

sys.meta_path.insert(0, Blocker())
from super_harness.cli import main
assert main.get_command(click.Context(main), "init") is not None
"""

    subprocess.run([sys.executable, "-c", code], check=True)


def test_help_short_flag():
    result = CliRunner().invoke(main, ["-h"])
    assert result.exit_code == 0
    assert "super-harness" in result.output
    assert "--workspace" in result.output
    assert "--version" in result.output
