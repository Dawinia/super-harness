"""Integration: SuperpowersAdapter is a registered builtin and installable."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from super_harness.adapters.framework.superpowers import SuperpowersAdapter
from super_harness.adapters.registry import get_builtin, list_builtins
from super_harness.cli import main


def test_superpowers_is_registered_builtin() -> None:
    assert "superpowers" in list_builtins()
    assert get_builtin("superpowers") is SuperpowersAdapter


def test_install_superpowers_then_listed(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["--workspace", str(tmp_path), "init"]).exit_code == 0
    r = runner.invoke(main, ["--workspace", str(tmp_path), "adapter", "install", "superpowers"])
    assert r.exit_code == 0, r.output
    listed = runner.invoke(main, ["--workspace", str(tmp_path), "adapter", "list"])
    assert "superpowers" in listed.output, listed.output
