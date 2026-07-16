from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from super_harness.cli.group_options import GroupAwareCommand, GroupAwareGroup
from super_harness.cli.lazy_group import CommandSpec, LazyGroup


def _write_command_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    package_name = f"lazy_fixture_{tmp_path.name}"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "alpha.py").write_text(
        "import click\n\n@click.command()\ndef alpha_cmd():\n    pass\n",
        encoding="utf-8",
    )
    (package / "beta.py").write_text(
        "import click\n\n@click.command()\ndef beta_cmd():\n    pass\n",
        encoding="utf-8",
    )
    (package / "tree.py").write_text(
        "import click\n\n"
        "@click.group()\n"
        "def tree_group():\n"
        "    pass\n\n"
        "@tree_group.group('branch')\n"
        "def branch_group():\n"
        "    pass\n\n"
        "@branch_group.command('leaf')\n"
        "def leaf_cmd():\n"
        "    pass\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return package_name


def test_list_commands_does_not_import_registered_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _write_command_package(tmp_path, monkeypatch)
    group = LazyGroup(
        name="root",
        command_specs={
            "alpha": CommandSpec(f"{package}.alpha:alpha_cmd", "Alpha command."),
            "beta": CommandSpec(f"{package}.beta:beta_cmd", "Beta command."),
        },
    )

    assert group.list_commands(click.Context(group)) == ["alpha", "beta"]
    assert f"{package}.alpha" not in sys.modules
    assert f"{package}.beta" not in sys.modules


def test_get_command_imports_only_requested_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _write_command_package(tmp_path, monkeypatch)
    group = LazyGroup(
        name="root",
        command_specs={
            "alpha": CommandSpec(f"{package}.alpha:alpha_cmd", "Alpha command."),
            "beta": CommandSpec(f"{package}.beta:beta_cmd", "Beta command."),
        },
    )

    command = group.get_command(click.Context(group), "alpha")

    assert command is not None
    assert command.__class__ is GroupAwareCommand
    assert f"{package}.alpha" in sys.modules
    assert f"{package}.beta" not in sys.modules


def test_get_command_rewraps_group_root_and_every_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _write_command_package(tmp_path, monkeypatch)
    group = LazyGroup(
        name="root",
        command_specs={
            "tree": CommandSpec(f"{package}.tree:tree_group", "Tree command."),
        },
    )

    command = group.get_command(click.Context(group), "tree")

    assert command is not None
    assert command.__class__ is GroupAwareGroup
    branch = command.commands["branch"]
    assert branch.__class__ is GroupAwareGroup
    assert branch.commands["leaf"].__class__ is GroupAwareCommand


def test_real_init_leaf_is_rewrapped_without_walking_a_subtree() -> None:
    from super_harness.cli import main

    with patch("super_harness.cli.lazy_group.rewrap_subtree") as rewrap:
        command = main.get_command(click.Context(main), "init")

    assert command is not None
    assert command.__class__ is GroupAwareCommand
    rewrap.assert_not_called()


def test_real_subgroup_root_and_descendants_are_group_aware() -> None:
    from super_harness.cli import main

    command = main.get_command(click.Context(main), "state")

    assert command is not None
    assert command.__class__ is GroupAwareGroup
    assert command.commands
    assert all(child.__class__ is GroupAwareCommand for child in command.commands.values())


def test_unknown_command_keeps_click_lookup_behavior() -> None:
    from super_harness.cli import main

    assert main.get_command(click.Context(main), "does-not-exist") is None
