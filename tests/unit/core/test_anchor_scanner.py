"""Tests for the pure `@capability:<id>` sentinel scanner (Task 1.10 / B-5 fix).

Per super-harness v0.1 plan: the scanner is shared by Phase 8 baseline checks
(`anchor-sentinel-presence`) and Phase 11 ambient sensor
(`freshness-anchor-check`). This module exercises the pure function only —
sensor / baseline wiring lives in its respective phase.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from super_harness.core.anchor_scanner import scan_sentinel_locations, scan_sentinels


def test_scan_finds_capability_sentinels(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("# @capability:cap-foo\nprint('hi')\n")
    (tmp_path / "src" / "bar.ts").write_text("// @capability:cap-bar\n")
    (tmp_path / "docs.md").write_text("@capability:cap-docs in prose")
    found = scan_sentinels(tmp_path, file_globs=["**/*"])
    assert found == {"cap-foo", "cap-bar", "cap-docs"}


def test_scan_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("excluded/\n")
    (tmp_path / "excluded").mkdir()
    (tmp_path / "excluded" / "x.py").write_text("@capability:cap-hidden\n")
    (tmp_path / "kept.py").write_text("@capability:cap-kept\n")
    # v0.1: use `git ls-files` when in a git repo
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    found = scan_sentinels(tmp_path, file_globs=["**/*"])
    assert "cap-kept" in found
    assert "cap-hidden" not in found  # gitignored


def test_scan_works_outside_git_repo(tmp_path: Path) -> None:
    """Fallback to filesystem walk excludes dot-prefixed paths."""
    (tmp_path / "code.py").write_text("# @capability:cap-no-git\n")
    (tmp_path / ".hidden").write_text("@capability:cap-hidden\n")  # dotfile -> skipped
    found = scan_sentinels(tmp_path)
    assert "cap-no-git" in found
    assert "cap-hidden" not in found  # dot-prefixed path excluded by fallback walk


def test_scan_skips_binary_files(tmp_path: Path) -> None:
    """Binary content (UTF-8 decode failure) must not crash the scanner."""
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe@capability:cap-binary\n")
    (tmp_path / "text.py").write_text("@capability:cap-text\n")
    found = scan_sentinels(tmp_path)
    # The text file must be picked up; the binary file is silently skipped
    # (UnicodeDecodeError swallowed). The point: no crash, and text wins.
    assert "cap-text" in found
    assert "cap-binary" not in found


def test_scan_honors_specific_glob_filter(tmp_path: Path) -> None:
    """A specific glob (e.g. `*.py`) restricts which files contribute sentinels."""
    (tmp_path / "keep.py").write_text("@capability:cap-py\n")
    (tmp_path / "skip.md").write_text("@capability:cap-md\n")
    found = scan_sentinels(tmp_path, file_globs=["*.py"])
    assert "cap-py" in found
    assert "cap-md" not in found


# --------------------------------------------------------------------------- #
# scan_sentinel_locations tests (Task 11.2)
# --------------------------------------------------------------------------- #


def test_locations_returns_file_and_1based_line(tmp_path: Path) -> None:
    """Reports the exact file (repo-relative) and 1-based line for each match."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("# @capability:cap-foo\nprint('hi')\n")
    (tmp_path / "src" / "bar.ts").write_text("\n// @capability:cap-bar\nexport {};\n")
    locs = scan_sentinel_locations(tmp_path)
    assert ("src/foo.py", 1) in locs["cap-foo"]
    assert ("src/bar.ts", 2) in locs["cap-bar"]


def test_locations_skips_binary_files(tmp_path: Path) -> None:
    """Binary / non-UTF-8 files are silently skipped — no crash."""
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe@capability:cap-binary\n")
    (tmp_path / "text.py").write_text("@capability:cap-text\n")
    locs = scan_sentinel_locations(tmp_path)
    assert "cap-text" in locs
    assert "cap-binary" not in locs


def test_locations_empty_repo_returns_empty_dict(tmp_path: Path) -> None:
    """An empty directory produces an empty dict (no KeyError, no crash)."""
    locs = scan_sentinel_locations(tmp_path)
    assert locs == {}


def test_locations_same_anchor_aggregates_across_files_and_lines(tmp_path: Path) -> None:
    """Same anchor ID on multiple lines / files → all locations aggregated."""
    (tmp_path / "a.py").write_text("# @capability:shared\npass\n# @capability:shared\n")
    (tmp_path / "b.py").write_text("# @capability:shared\n")
    locs = scan_sentinel_locations(tmp_path)
    shared = locs["shared"]
    assert ("a.py", 1) in shared
    assert ("a.py", 3) in shared
    assert ("b.py", 1) in shared
    assert len(shared) == 3
