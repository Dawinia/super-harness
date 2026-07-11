# tests/unit/core/test_scope_match.py
"""Unit tests for core.scope_match (shared scope matcher + fail-closed git helpers)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from super_harness.core.scope_match import (
    GitScopeError,
    committed_scope_digest,
    covered_by_scope,
    is_ancestor,
    merge_base_commit,
    resolve_commit,
    scope_diff_argv,
    split_changed_by_scope,
    split_changed_by_scope_between,
    working_tree_dirty,
)


def _git(ws: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, text=True)


def _repo(ws: Path) -> None:
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")


def test_covered_by_scope_segment_aware() -> None:
    assert covered_by_scope("src/foo/x.py", ["src/foo/"]) is True
    assert covered_by_scope("src/foo/x.py", ["src/foo"]) is True
    assert covered_by_scope("src/foo.py", ["src/foo.py"]) is True
    # sibling sharing textual prefix is NOT covered
    assert covered_by_scope("src/foobar.py", ["src/foo"]) is False
    assert covered_by_scope("a.py", []) is False


def test_split_changed_by_scope(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "in.py").write_text("a\n")
    (tmp_path / "out.py").write_text("b\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "in.py").write_text("a2\n")
    (tmp_path / "out.py").write_text("b2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    in_scope, out_scope = split_changed_by_scope(tmp_path, base="main", declared=["src/"])
    assert in_scope == ["src/in.py"]
    assert out_scope == ["out.py"]


def test_resolve_commit_returns_full_sha(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")

    resolved = resolve_commit(tmp_path, "HEAD")

    assert len(resolved) == 40
    assert resolved == subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_is_ancestor_distinguishes_history_order(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = resolve_commit(tmp_path)
    (tmp_path / "f.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "next")
    head = resolve_commit(tmp_path)

    assert is_ancestor(tmp_path, base, head) is True
    assert is_ancestor(tmp_path, head, base) is False


def test_explicit_range_returns_scoped_files_and_ordered_diff_argv(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a1\n")
    (tmp_path / "src" / "b.py").write_text("b1\n")
    (tmp_path / "outside.py").write_text("o1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "src" / "a.py").write_text("a2\n")
    (tmp_path / "outside.py").write_text("o2\n")
    _git(tmp_path, "commit", "-aqm", "middle")
    middle = resolve_commit(tmp_path)
    (tmp_path / "src" / "b.py").write_text("b2\n")
    _git(tmp_path, "commit", "-aqm", "head")
    head = resolve_commit(tmp_path)

    in_scope, out_scope = split_changed_by_scope_between(
        tmp_path, base=middle, head=head, declared=["src/"]
    )

    assert in_scope == ["src/b.py"]
    assert out_scope == []
    assert scope_diff_argv(middle, head, ["src/b.py", "src/a.py"]) == [
        "git", "diff", f"{middle}..{head}", "--", "src/a.py", "src/b.py"
    ]


def test_merge_base_excludes_changes_added_only_to_base_branch(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("base\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    shared = resolve_commit(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "f.py").write_text("feature\n")
    _git(tmp_path, "commit", "-aqm", "feature")
    feature = resolve_commit(tmp_path)
    _git(tmp_path, "checkout", "main")
    (tmp_path / "main-only.py").write_text("main\n")
    _git(tmp_path, "add", "main-only.py")
    _git(tmp_path, "commit", "-qm", "main moves")

    assert merge_base_commit(tmp_path, "main", feature) == shared


def test_committed_scope_digest_stable_and_changes(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "f.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "w1")
    d1 = committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    d1_again = committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    assert d1 == d1_again and d1  # stable, non-empty
    (tmp_path / "f.py").write_text("v3\n")
    _git(tmp_path, "commit", "-aqm", "w2")
    d2 = committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    assert d2 != d1  # committed change moves the digest


def test_committed_scope_digest_empty_scope_is_constant(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    assert committed_scope_digest(tmp_path, base="main", in_scope=[]) == committed_scope_digest(
        tmp_path, base="main", in_scope=[]
    )


def test_working_tree_dirty(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    assert working_tree_dirty(tmp_path, ["f.py"]) is False
    (tmp_path / "f.py").write_text("dirty\n")
    assert working_tree_dirty(tmp_path, ["f.py"]) is True


def test_git_error_fails_closed(tmp_path: Path) -> None:
    # not a git repo → fail closed (raise), NOT silent pass
    with pytest.raises(GitScopeError):
        committed_scope_digest(tmp_path, base="main", in_scope=["f.py"])
    with pytest.raises(GitScopeError):
        split_changed_by_scope(tmp_path, base="main", declared=["src/"])
