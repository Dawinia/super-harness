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
