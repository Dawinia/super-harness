"""canonical_relpath — the shared repo-relative resolver used by the gate carve-out.

Both the recorded plan-artifact paths and the gate's incoming-file matching must
canonicalize the SAME way, so a symlinked/`..`-laden/absolute path can't slip a
source file past the marked-`.md` checks. Returns None for anything that resolves
outside the repo root; never raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from super_harness.core.paths import canonical_relpath


def test_absolute_under_root(tmp_path: Path) -> None:
    (tmp_path / "docs/plans").mkdir(parents=True)
    f = tmp_path / "docs/plans/c.md"
    f.write_text("x")
    assert canonical_relpath(tmp_path, str(f)) == "docs/plans/c.md"


def test_relative_rooted(tmp_path: Path) -> None:
    assert canonical_relpath(tmp_path, "docs/plans/c.md") == "docs/plans/c.md"


def test_inside_root_traversal_is_kept(tmp_path: Path) -> None:
    # resolves to <root>/etc/passwd — still INSIDE root, so it is returned (relpath),
    # NOT None. (It won't match any recorded .md artifact → BLOCK downstream.)
    assert canonical_relpath(tmp_path, "docs/plans/../../etc/passwd") == "etc/passwd"


def test_true_escape_is_none(tmp_path: Path) -> None:
    deep = "../" * 40 + "etc/passwd"
    assert canonical_relpath(tmp_path, deep) is None


def test_absolute_outside_is_none(tmp_path: Path) -> None:
    assert canonical_relpath(tmp_path, "/etc/passwd") is None


def test_none_input(tmp_path: Path) -> None:
    assert canonical_relpath(tmp_path, None) is None


def test_empty_input(tmp_path: Path) -> None:
    assert canonical_relpath(tmp_path, "") is None


@pytest.mark.skipif(sys.platform == "win32", reason="symlink perms differ on Windows")
def test_symlink_resolved_to_target(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "src/x.py").write_text("x")
    link = tmp_path / "docs/plans/c.md"
    link.symlink_to(tmp_path / "src/x.py")
    # canonical form FOLLOWS the symlink to its target — this is exactly why the
    # caller must re-check `.md` AFTER canonicalization.
    assert canonical_relpath(tmp_path, str(link)) == "src/x.py"
