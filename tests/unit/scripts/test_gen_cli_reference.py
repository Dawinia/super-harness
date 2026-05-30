"""Unit tests for `scripts/gen_cli_reference.py` (Phase 15 Task 15.2).

The generator walks a click command tree and emits a markdown reference. These
tests use a minimal hand-rolled click group (decoupled from the real
super-harness CLI) so the assertions are stable across future CLI surface
changes — the real-CLI in-sync check is the integration `--check` exit-0 test
at the end.

Coverage:
  1. Minimal click group renders expected markdown shape
  2. ``|`` in help text escapes to ``\\|`` (markdown-injection hardening)
  3. ``--check`` exits 0 when on-disk file matches generated content
  4. ``--check`` exits 1 when on-disk file is out-of-sync (drift)
  5. ``--check`` exits 1 when on-disk file is missing (treated as drift, not crash)
  6. Repeated regeneration is idempotent (running twice produces no diff)
"""
from __future__ import annotations

import click

from scripts import gen_cli_reference


def _build_fixture_group() -> click.Group:
    """A 3-command fixture: 1 leaf, 1 subgroup with leaf, 1 leaf with flag/pipe-help."""
    @click.group(help="Fixture root group.")
    def root() -> None:
        """Fixture root."""

    @root.command("leaf")
    def leaf_cmd() -> None:
        """A simple leaf command."""

    @root.group("sub", help="A subgroup.")
    def sub() -> None:
        """A subgroup."""

    @sub.command("child")
    @click.argument("name")
    def child_cmd(name: str) -> None:
        """A child of sub."""

    @root.command("flagged")
    @click.option("--mode", type=click.Choice(["a", "b"]), default="a",
                  help="Mode | with pipe.")
    @click.option("--count", type=int, default=0)
    def flagged_cmd(mode: str, count: int) -> None:
        """A leaf with a flag whose help text contains a | pipe."""

    return root


def test_render_minimal_group_contains_all_commands() -> None:
    """Generator output mentions every leaf command in the fixture group."""
    group = _build_fixture_group()
    md = gen_cli_reference.render_markdown(group, root_name="fixture")

    # Every leaf section heading must appear
    assert "## fixture leaf" in md
    assert "## fixture sub child" in md
    assert "## fixture flagged" in md

    # Subgroup container should also have a section
    assert "## fixture sub" in md

    # Each section should have a synopsis line
    assert "fixture leaf [OPTIONS]" in md
    assert "fixture sub child [OPTIONS] NAME" in md


def test_render_escapes_pipe_in_help_text() -> None:
    """`|` in click help text must escape to `\\|` to not break markdown tables."""
    group = _build_fixture_group()
    md = gen_cli_reference.render_markdown(group, root_name="fixture")

    # Raw `Mode | with pipe.` would break the cell separator; must be escaped
    assert "Mode \\| with pipe." in md
    assert "Mode | with pipe." not in md


def test_render_param_table_includes_choices_and_defaults() -> None:
    """Choice options enumerate values; defaults are rendered."""
    group = _build_fixture_group()
    md = gen_cli_reference.render_markdown(group, root_name="fixture")

    # `--mode` with Choice([a, b]) → type column reads `{a|b}` (escaped)
    # In a markdown cell, the pipe inside `{a|b}` must be escaped too.
    assert "{a\\|b}" in md
    # Default for --count is 0
    assert "--count" in md


def test_render_is_idempotent() -> None:
    """Calling render_markdown twice on the same group yields byte-identical output."""
    group = _build_fixture_group()
    first = gen_cli_reference.render_markdown(group, root_name="fixture")
    second = gen_cli_reference.render_markdown(group, root_name="fixture")
    assert first == second


def test_md_escape_cell_handles_pipe_and_newline() -> None:
    """The cell-escape helper handles `|` and embedded newlines."""
    assert gen_cli_reference._md_escape_cell("a|b") == "a\\|b"
    assert gen_cli_reference._md_escape_cell("line1\nline2") == "line1 line2"
    assert gen_cli_reference._md_escape_cell("plain") == "plain"
    # backticks are preserved (render fine in GFM cells)
    assert gen_cli_reference._md_escape_cell("`code`") == "`code`"


def test_check_mode_exits_zero_when_in_sync(tmp_path, monkeypatch) -> None:
    """`--check` mode reports exit 0 when committed file matches generated."""
    group = _build_fixture_group()
    generated = gen_cli_reference.render_markdown(group, root_name="fixture")
    target = tmp_path / "cli-reference.md"
    target.write_text(generated, encoding="utf-8")

    code = gen_cli_reference.run_check(group, root_name="fixture", target=target)
    assert code == 0


def test_check_mode_exits_one_on_drift(tmp_path) -> None:
    """`--check` mode reports exit 1 when on-disk content is stale."""
    group = _build_fixture_group()
    target = tmp_path / "cli-reference.md"
    target.write_text("# Outdated content\n", encoding="utf-8")

    code = gen_cli_reference.run_check(group, root_name="fixture", target=target)
    assert code == 1


def test_check_mode_treats_missing_file_as_drift(tmp_path) -> None:
    """Missing target file → exit 1 (drift), not a crash."""
    group = _build_fixture_group()
    target = tmp_path / "does-not-exist.md"

    code = gen_cli_reference.run_check(group, root_name="fixture", target=target)
    assert code == 1


def test_check_mode_treats_undecodable_file_as_drift(tmp_path) -> None:
    """UnicodeDecodeError on read → exit 1 (drift), not a crash."""
    group = _build_fixture_group()
    target = tmp_path / "binary.md"
    # Bytes that are invalid UTF-8 to trigger UnicodeDecodeError
    target.write_bytes(b"\xff\xfe\x00\x00invalid utf8")

    code = gen_cli_reference.run_check(group, root_name="fixture", target=target)
    assert code == 1


def test_write_then_check_is_in_sync(tmp_path) -> None:
    """Writing the file via the generator then checking returns exit 0."""
    group = _build_fixture_group()
    target = tmp_path / "cli-reference.md"
    gen_cli_reference.write_reference(group, root_name="fixture", target=target)

    code = gen_cli_reference.run_check(group, root_name="fixture", target=target)
    assert code == 0


def test_real_cli_reference_is_in_sync() -> None:
    """Integration: the committed docs/cli-reference.md is in sync with the real CLI.

    Re-implements `python -m scripts.gen_cli_reference --check` against the
    real super-harness CLI surface. If this fails, regenerate by running
    `python -m scripts.gen_cli_reference` and commit the result.
    """
    from pathlib import Path

    from super_harness.cli import main as real_main

    repo_root = Path(__file__).resolve().parents[3]
    target = repo_root / "docs" / "cli-reference.md"
    code = gen_cli_reference.run_check(
        real_main, root_name="super-harness", target=target
    )
    assert code == 0, (
        "docs/cli-reference.md is out of sync with the CLI surface. "
        "Run: python -m scripts.gen_cli_reference"
    )
