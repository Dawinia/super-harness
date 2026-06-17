"""Unit tests for `super-harness sync` error envelopes (Phase 9 batch late).

Focused on the failure paths the integration suite does not exercise: a corrupt
``.harness/adapters.yaml`` on the ``--adapter`` leg (EXIT_NO_CONFIG) and an
AGENTS.md write failure (EXIT_GENERIC via ``format_error``, never a traceback).
Drives the root ``main`` group via Click's ``CliRunner`` so ``ctx.obj`` global
flags resolve exactly as in production.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.engineering.gitignore_injector import inject_gitignore_block
from super_harness.version import __version__


def test_sync_adapter_corrupt_adapters_yaml_exits_no_config(tmp_path: Path) -> None:
    """`sync --adapter X` with a syntactically-broken adapters.yaml → EXIT_NO_CONFIG
    (3) with a clear message (mirrors adapter.py's corrupt-yaml handling)."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "adapters.yaml").write_text("{ this is: not: valid: yaml\n")

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 3, r.output
    assert "adapters.yaml is corrupt or unreadable" in r.stderr
    assert "Traceback" not in r.stderr


def test_sync_adapter_wrongshape_adapters_yaml_exits_no_config(tmp_path: Path) -> None:
    """A wrong-shape (valid YAML but `adapters` not a list) adapters.yaml raises
    ValueError inside load_adapters → still EXIT_NO_CONFIG (3), no traceback."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "adapters.yaml").write_text("adapters: not-a-list\n")

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 3, r.output
    assert "adapters.yaml is corrupt or unreadable" in r.stderr
    assert "Traceback" not in r.stderr


def test_sync_full_agents_md_write_failure_exits_generic(tmp_path: Path) -> None:
    """If the AGENTS.md write raises OSError, full sync surfaces a clean
    format_error (exit 1, no traceback). We force a portable OSError by placing a
    DIRECTORY at the AGENTS.md path: the injector's read raises IsADirectoryError
    (an OSError subclass) on every platform."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / "AGENTS.md").mkdir()

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "super-harness sync:" in r.stderr
    assert "failed to update AGENTS.md" in r.stderr
    assert "Hint:" in r.stderr


def test_sync_adapter_write_failure_exits_generic(tmp_path: Path) -> None:
    """An OSError on the `--adapter` inject leg routes through the same
    format_error envelope (exit 1, no traceback). adapters.yaml lists claude-code
    so the adapter resolves; the AGENTS.md-as-directory forces the write failure."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "adapters.yaml").write_text(
        "adapters:\n"
        "  - {name: claude-code, type: agent, builtin: true, version: 0.1.0, enabled: true}\n"
    )
    (tmp_path / "AGENTS.md").mkdir()

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "failed to update AGENTS.md" in r.stderr


def test_sync_gitignore_write_failure_exits_generic(tmp_path: Path) -> None:
    """If the `.gitignore` write fails, `sync --gitignore` surfaces a clean
    format_error (exit 1, no traceback). Force a portable OSError by placing a
    DIRECTORY at the `.gitignore` path: the injector's read raises
    IsADirectoryError (an OSError subclass) on every platform."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".gitignore").mkdir()

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--quiet", "sync", "--gitignore"]
    )
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "super-harness sync:" in r.stderr
    assert "failed to update .gitignore" in r.stderr
    assert "Hint:" in r.stderr


def test_sync_full_gitignore_leg_failure_after_agents_md_exits_generic(
    tmp_path: Path,
) -> None:
    """In the full (no-arg) path, an AGENTS.md render that SUCCEEDS followed by a
    failing `.gitignore` write exits 1 (no traceback) — AGENTS.md is already
    written (half-success), but each write is atomic and `sync` is idempotent, so
    a re-run self-heals. Force the gitignore failure with a DIRECTORY at its path;
    AGENTS.md is absent so its render succeeds (no overwrite, no prompt)."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".gitignore").mkdir()

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "failed to update .gitignore" in r.stderr
    # The AGENTS.md leg ran first and succeeded (file now exists).
    assert (tmp_path / "AGENTS.md").is_file()


def _init_harness(root: Path) -> None:
    (root / ".harness").mkdir()


def test_sync_check_clean_repo_exits_ok(tmp_path: Path) -> None:
    """`sync --check` on a freshly-rendered repo → exit 0, no diff written."""
    _init_harness(tmp_path)
    render_super_harness_section(tmp_path, tmp_path / "AGENTS.md", __version__)
    inject_gitignore_block(tmp_path / ".gitignore")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--check"])
    assert r.exit_code == 0, r.output


def test_sync_check_drifted_agents_md_exits_validation(tmp_path: Path) -> None:
    """A hand-mutated AGENTS.md managed section → exit 2 (EXIT_VALIDATION) + diff,
    file unchanged."""
    _init_harness(tmp_path)
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, __version__)
    mutated = agents.read_text().replace("### File scope", "### File scope EDITED")
    agents.write_text(mutated)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--check"])
    assert r.exit_code == 2, r.output
    assert "AGENTS.md" in r.stderr
    assert agents.read_text() == mutated  # never written


def test_sync_check_with_adapter_is_rejected(tmp_path: Path) -> None:
    """`--adapter` + `--check` is rejected (the --agents-md check already covers
    adapter subsections); exit is NOT 2 (that means drift), so use EXIT_GENERIC."""
    _init_harness(tmp_path)

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "sync", "--check", "--adapter", "claude-code"],
    )
    assert r.exit_code == 1, r.output
    assert "does not support `--adapter`" in r.stderr
    assert "Traceback" not in r.stderr


def test_sync_check_agents_only_scope(tmp_path: Path) -> None:
    """`sync --agents-md --check` checks ONLY AGENTS.md: a drifted .gitignore does
    not fail the agents-only check."""
    _init_harness(tmp_path)
    render_super_harness_section(tmp_path, tmp_path / "AGENTS.md", __version__)
    # .gitignore intentionally absent → would drift if checked, but scope excludes it.

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "sync", "--agents-md", "--check"]
    )
    assert r.exit_code == 0, r.output
