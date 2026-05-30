"""Unit tests for _l1_helpers private module.

TDD order: tests written before the implementation.

generate_l1_stubs tests (pure filesystem, no git/subprocess):
1.  test_generate_creates_files_for_each_anchor
2.  test_generate_writes_expected_stub_body
3.  test_generate_skips_existing_unchanged
4.  test_generate_overwrites_existing_different
5.  test_generate_empty_anchors_returns_empty
6.  test_generate_creates_parent_dirs
7.  test_generate_mixed_skipped_and_written

git_branch_commit_push tests (real local git, isolated tmp repo):
8.  test_branch_commit_push_creates_branch_and_commit_skip_push
9.  test_branch_commit_push_invokes_push_when_not_skipped
10. test_branch_commit_push_raises_on_git_failure
11. test_branch_commit_push_adds_multiple_files
12. test_branch_commit_push_uses_relative_paths_for_add

build_l1_pr_body tests (pure string, no I/O):
13. test_pr_body_contains_change_id
14. test_pr_body_lists_repo_relative_paths
15. test_pr_body_preserves_input_order
16. test_pr_body_deterministic
17. test_pr_body_empty_files_no_bullet_list
18. test_pr_body_mentions_auto_merge_or_labels
19. test_pr_body_handles_bare_name_path_gracefully

pr_num_from_url tests (pure string, no I/O):
20. test_pr_num_parses_standard_https_url
21. test_pr_num_parses_with_trailing_slash
22. test_pr_num_parses_with_fragment_or_query
23. test_pr_num_parses_ssh_style_url
24. test_pr_num_raises_on_no_pull_segment
25. test_pr_num_raises_on_non_numeric
26. test_pr_num_handles_large_number
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from super_harness.sensors._l1_helpers import (
    build_l1_pr_body,
    generate_l1_stubs,
    git_branch_commit_push,
    pr_num_from_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STUB_TEMPLATE = (
    "# {aid}\n\n"
    "<!-- L1 capability stub auto-written by super-harness l1-updater. -->\n"
    "<!-- Real generation is v0.2+; this file marks the placeholder location. -->\n"
)


def _stub_body(anchor_id: str) -> str:
    return _STUB_TEMPLATE.format(aid=anchor_id)


def _init_repo(root: Path) -> None:
    """Initialise a throw-away local git repo on the `main` branch.

    `git init -b main` requires git >= 2.28. We try that first; if it fails
    (older git), we init without -b and then rename the default branch to main
    after the seed commit.
    """
    result = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        capture_output=True,
    )
    if result.returncode != 0:
        # Fallback for older git: init + rename branch after first commit.
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)

    subprocess.run(
        ["git", "config", "user.email", "test@super-harness.local"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=root,
        check=True,
    )
    (root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    # If we used the fallback path, rename to main now.
    if result.returncode != 0:
        subprocess.run(
            ["git", "checkout", "-b", "main"],
            cwd=root,
            check=True,
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# generate_l1_stubs tests
# ---------------------------------------------------------------------------


def test_generate_creates_files_for_each_anchor(tmp_path: Path) -> None:
    written = generate_l1_stubs(tmp_path, ["cap-foo", "cap-bar"])
    out_dir = tmp_path / "docs" / "reference" / "capabilities"
    assert (out_dir / "cap-foo.md").exists()
    assert (out_dir / "cap-bar.md").exists()
    assert len(written) == 2
    assert written[0] == (out_dir / "cap-foo.md").resolve()
    assert written[1] == (out_dir / "cap-bar.md").resolve()


def test_generate_writes_expected_stub_body(tmp_path: Path) -> None:
    generate_l1_stubs(tmp_path, ["cap-x"])
    out_file = tmp_path / "docs" / "reference" / "capabilities" / "cap-x.md"
    expected = _stub_body("cap-x")
    assert out_file.read_text() == expected


def test_generate_skips_existing_unchanged(tmp_path: Path) -> None:
    out_dir = tmp_path / "docs" / "reference" / "capabilities"
    out_dir.mkdir(parents=True)
    target = out_dir / "cap-y.md"
    body = _stub_body("cap-y")
    target.write_text(body)

    mtime_before = os.path.getmtime(target)
    written = generate_l1_stubs(tmp_path, ["cap-y"])
    mtime_after = os.path.getmtime(target)

    assert written == []
    assert target.read_text() == body
    assert mtime_before == mtime_after


def test_generate_overwrites_existing_different(tmp_path: Path) -> None:
    out_dir = tmp_path / "docs" / "reference" / "capabilities"
    out_dir.mkdir(parents=True)
    target = out_dir / "cap-z.md"
    target.write_text("# cap-z\n\nold body\n")

    written = generate_l1_stubs(tmp_path, ["cap-z"])
    expected = _stub_body("cap-z")

    assert target.read_text() == expected
    assert len(written) == 1
    assert written[0] == target.resolve()


def test_generate_empty_anchors_returns_empty(tmp_path: Path) -> None:
    result = generate_l1_stubs(tmp_path, [])
    assert result == []
    caps_dir = tmp_path / "docs" / "reference" / "capabilities"
    # Directory need not exist — no anchors means no side-effects required.
    # Accept either: dir absent or dir empty.
    if caps_dir.exists():
        assert list(caps_dir.iterdir()) == []


def test_generate_creates_parent_dirs(tmp_path: Path) -> None:
    assert not (tmp_path / "docs").exists()
    generate_l1_stubs(tmp_path, ["cap-alpha"])
    assert (tmp_path / "docs" / "reference" / "capabilities").is_dir()


def test_generate_mixed_skipped_and_written(tmp_path: Path) -> None:
    out_dir = tmp_path / "docs" / "reference" / "capabilities"
    out_dir.mkdir(parents=True)

    # Pre-create: unchanged, different, absent
    (out_dir / "cap-unchanged.md").write_text(_stub_body("cap-unchanged"))
    (out_dir / "cap-different.md").write_text("# cap-different\n\nstale\n")
    # cap-new.md is absent

    anchors = ["cap-unchanged", "cap-different", "cap-new"]
    written = generate_l1_stubs(tmp_path, anchors)

    # Only the two that needed writing are returned, in input order.
    assert len(written) == 2
    rel = [p.name for p in written]
    assert rel == ["cap-different.md", "cap-new.md"]
    # Unchanged file is not in the list.
    assert all(p.name != "cap-unchanged.md" for p in written)
    # Verify contents.
    assert (out_dir / "cap-different.md").read_text() == _stub_body("cap-different")
    assert (out_dir / "cap-new.md").read_text() == _stub_body("cap-new")


# ---------------------------------------------------------------------------
# git_branch_commit_push tests
# ---------------------------------------------------------------------------


def test_branch_commit_push_creates_branch_and_commit_skip_push(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    f1 = tmp_path / "cap-alpha.md"
    f1.write_text(_stub_body("cap-alpha"))

    git_branch_commit_push(tmp_path, "harness/test-1", [f1], "msg", skip_push=True)

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "harness/test-1"

    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subject == "msg"

    tree_files = subprocess.run(
        ["git", "ls-tree", "HEAD", "--name-only"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().split("\n")
    assert "cap-alpha.md" in tree_files


def test_branch_commit_push_invokes_push_when_not_skipped(tmp_path: Path) -> None:
    """Mock subprocess.run entirely and verify all 4 calls in order."""
    with patch("super_harness.sensors._l1_helpers.subprocess.run") as mock_run:
        mock_run.return_value = None  # each call returns None; check=True won't raise

        f_abs = tmp_path / "some.md"
        git_branch_commit_push(
            tmp_path, "harness/test-2", [f_abs], "add stubs"
        )

        assert mock_run.call_count == 4

        calls = mock_run.call_args_list
        # Step 1: checkout -b
        assert calls[0] == call(
            ["git", "checkout", "-b", "harness/test-2", "main"],
            cwd=tmp_path,
            check=True,
        )
        # Step 2: git add (relative path)
        rel = str(f_abs.relative_to(tmp_path))
        assert calls[1] == call(
            ["git", "add", rel],
            cwd=tmp_path,
            check=True,
        )
        # Step 3: commit
        assert calls[2] == call(
            ["git", "commit", "-m", "add stubs"],
            cwd=tmp_path,
            check=True,
        )
        # Step 4: push
        assert calls[3] == call(
            ["git", "push", "origin", "harness/test-2"],
            cwd=tmp_path,
            check=True,
        )


def test_branch_commit_push_raises_on_git_failure(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    # `git checkout -b main main` will fail because branch `main` already exists.
    with pytest.raises(subprocess.CalledProcessError):
        git_branch_commit_push(
            tmp_path, "main", [], "will-fail", skip_push=True
        )


def test_branch_commit_push_adds_multiple_files(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    files = []
    for name in ["alpha.md", "beta.md", "gamma.md"]:
        f = tmp_path / name
        f.write_text(f"# {name}\n")
        files.append(f)

    git_branch_commit_push(tmp_path, "harness/multi", files, "add three", skip_push=True)

    tree_files = subprocess.run(
        ["git", "ls-tree", "HEAD", "--name-only"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().split("\n")
    assert "alpha.md" in tree_files
    assert "beta.md" in tree_files
    assert "gamma.md" in tree_files


def test_branch_commit_push_uses_relative_paths_for_add(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    deep = tmp_path / "deep" / "dir"
    deep.mkdir(parents=True)
    x = deep / "x.md"
    x.write_text("# x\n")

    git_branch_commit_push(tmp_path, "harness/deep", [x], "add deep file", skip_push=True)

    tree_files = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().split("\n")
    assert "deep/dir/x.md" in tree_files


# ---------------------------------------------------------------------------
# build_l1_pr_body tests
# ---------------------------------------------------------------------------


def test_pr_body_contains_change_id() -> None:
    body = build_l1_pr_body("abc-123", [])
    assert "abc-123" in body


def test_pr_body_lists_repo_relative_paths() -> None:
    files = [
        Path("/tmp/x/docs/reference/capabilities/cap-foo.md"),
        Path("/tmp/x/docs/reference/capabilities/cap-bar.md"),
    ]
    body = build_l1_pr_body("chg-001", files)
    assert "`docs/reference/capabilities/cap-foo.md`" in body
    assert "`docs/reference/capabilities/cap-bar.md`" in body


def test_pr_body_preserves_input_order() -> None:
    files = [
        Path("/tmp/x/docs/reference/capabilities/cap-z.md"),
        Path("/tmp/x/docs/reference/capabilities/cap-a.md"),
        Path("/tmp/x/docs/reference/capabilities/cap-m.md"),
    ]
    body = build_l1_pr_body("chg-002", files)
    idx_z = body.index("cap-z.md")
    idx_a = body.index("cap-a.md")
    idx_m = body.index("cap-m.md")
    assert idx_z < idx_a < idx_m


def test_pr_body_deterministic() -> None:
    files = [
        Path("/tmp/x/docs/reference/capabilities/cap-alpha.md"),
        Path("/tmp/x/docs/reference/capabilities/cap-beta.md"),
    ]
    first = build_l1_pr_body("chg-det", files)
    second = build_l1_pr_body("chg-det", files)
    assert first == second


def test_pr_body_empty_files_no_bullet_list() -> None:
    body = build_l1_pr_body("chg-empty", [])
    assert "chg-empty" in body
    # Must contain an explanatory message about no files.
    assert "No files" in body or "no files" in body or "already current" in body
    # Must not contain a bullet item with a backtick (empty bullet list).
    assert "- `" not in body


def test_pr_body_mentions_auto_merge_or_labels() -> None:
    body = build_l1_pr_body("chg-labels", [])
    assert "harness-auto" in body or "no-human-review" in body


def test_pr_body_handles_bare_name_path_gracefully() -> None:
    # Path with no docs/reference/capabilities prefix — fallback to .name.
    body = build_l1_pr_body("chg-bare", [Path("cap-foo.md")])
    assert "cap-foo.md" in body


# ---------------------------------------------------------------------------
# pr_num_from_url tests
# ---------------------------------------------------------------------------


def test_pr_num_parses_standard_https_url() -> None:
    assert pr_num_from_url("https://github.com/owner/repo/pull/123") == 123


def test_pr_num_parses_with_trailing_slash() -> None:
    assert pr_num_from_url("https://github.com/owner/repo/pull/45/") == 45


def test_pr_num_parses_with_fragment_or_query() -> None:
    assert pr_num_from_url("https://github.com/owner/repo/pull/7#discussion_r1") == 7
    assert pr_num_from_url("https://github.com/owner/repo/pull/9?diff=split") == 9


def test_pr_num_parses_ssh_style_url() -> None:
    assert pr_num_from_url("git@github.com:owner/repo/pull/1024") == 1024


def test_pr_num_raises_on_no_pull_segment() -> None:
    url = "https://github.com/owner/repo"
    with pytest.raises(ValueError, match=r"https://github\.com/owner/repo"):
        pr_num_from_url(url)


def test_pr_num_raises_on_non_numeric() -> None:
    with pytest.raises(ValueError):
        pr_num_from_url("https://github.com/owner/repo/pull/abc")


def test_pr_num_handles_large_number() -> None:
    assert pr_num_from_url("https://github.com/owner/repo/pull/999999") == 999999
