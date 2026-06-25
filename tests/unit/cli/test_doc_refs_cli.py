"""CLI tests for `super-harness doc refs` graded exit codes + the `done` warn helper."""
from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main  # root click group lives in cli/__init__.py


def _repo_with_dead_ref(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("old `_format_rows` is gone\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=tmp_path, check=True,
    )
    return tmp_path


def test_doc_refs_default_warns_exit_0(tmp_path: Path) -> None:
    root = _repo_with_dead_ref(tmp_path)
    res = CliRunner().invoke(main, ["--workspace", str(root), "doc", "refs"])
    assert res.exit_code == 0
    assert "_format_rows" in res.output


def test_doc_refs_gate_blocks_exit_2(tmp_path: Path) -> None:
    root = _repo_with_dead_ref(tmp_path)
    res = CliRunner().invoke(main, ["--workspace", str(root), "doc", "refs", "--gate"])
    assert res.exit_code == 2
    assert "_format_rows" in res.output


def test_doc_refs_gate_clean_exit_0(tmp_path: Path) -> None:
    root = _repo_with_dead_ref(tmp_path)
    (root / "docs" / "guide.md").write_text("call `_render`\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fix"],
        cwd=root, check=True,
    )
    res = CliRunner().invoke(main, ["--workspace", str(root), "doc", "refs", "--gate"])
    assert res.exit_code == 0
