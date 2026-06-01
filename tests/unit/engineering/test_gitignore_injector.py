"""Tests for the .gitignore marker-bounded injector (S2 fix).

`super-harness init` writes a marker-bounded block listing the canonical
`.harness/` auto-generated paths into the repo-root `.gitignore`. The injector
follows the same marker-discipline contract as the AGENTS.md injector
(Phase 7/9/12 lesson): exactly one block expected; multiple blocks → fail loud
(never splice — data-loss guard).

All tests use real file I/O via `tmp_path` (no mocking), per the project's
adapter-test idiom.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.engineering.gitignore_injector import (
    GITIGNORE_BEGIN_MARKER,
    GITIGNORE_END_MARKER,
    GitignoreInjectionError,
    inject_gitignore_block,
)

# The 8 canonical .harness/ runtime/derived paths (NOT version-control candidates).
_CANONICAL_PATHS = (
    ".harness/state.yaml",
    ".harness/events.jsonl",
    ".harness/sensor-results/",
    ".harness/verification-results/",
    ".harness/operation-logs/",
    ".harness/anchors/index.yaml",
    ".harness/pending-l1-updates/",
    ".harness/pending-reviews/",
)


# --------------------------------------------------------------------------- #
# Marker grammar
# --------------------------------------------------------------------------- #


def test_marker_strings_are_exact() -> None:
    """Lock in the exact marker text so a regression renaming them is caught."""
    assert GITIGNORE_BEGIN_MARKER == (
        "# >>> super-harness gitignore (do not edit between markers)"
    )
    assert GITIGNORE_END_MARKER == "# <<< super-harness gitignore"


# --------------------------------------------------------------------------- #
# Absent .gitignore -> write fresh
# --------------------------------------------------------------------------- #


def test_absent_gitignore_writes_marker_block_only(tmp_path: Path) -> None:
    """When .gitignore is absent, the injector writes a file containing
    ONLY the marker block (no other content)."""
    path = tmp_path / ".gitignore"
    assert not path.exists()
    inject_gitignore_block(path)
    assert path.exists()
    text = path.read_text()
    assert text.startswith(GITIGNORE_BEGIN_MARKER + "\n")
    assert text.rstrip("\n").endswith(GITIGNORE_END_MARKER)
    for p in _CANONICAL_PATHS:
        assert p in text, f"missing canonical path: {p}"


# --------------------------------------------------------------------------- #
# Present + 0 marker blocks -> append (preserve user content)
# --------------------------------------------------------------------------- #


def test_existing_gitignore_zero_blocks_appends_block(tmp_path: Path) -> None:
    """When .gitignore has user content but no super-harness block, the
    injector appends a marker block while preserving user content verbatim."""
    path = tmp_path / ".gitignore"
    user_content = "# User-written gitignore\n*.pyc\nnode_modules/\n.env\n"
    path.write_text(user_content)
    inject_gitignore_block(path)
    text = path.read_text()
    # User content preserved verbatim (modulo trailing newline normalization).
    assert "# User-written gitignore" in text
    assert "*.pyc" in text
    assert "node_modules/" in text
    assert ".env" in text
    # Super-harness block appended after user content.
    user_idx = text.index("node_modules/")
    block_idx = text.index(GITIGNORE_BEGIN_MARKER)
    assert user_idx < block_idx
    # All 8 canonical paths present.
    for p in _CANONICAL_PATHS:
        assert p in text, f"missing canonical path: {p}"


# --------------------------------------------------------------------------- #
# Present + 1 block (canonical body) -> idempotent no-op
# --------------------------------------------------------------------------- #


def test_existing_gitignore_one_canonical_block_is_byte_identical_noop(
    tmp_path: Path,
) -> None:
    """A second injector run on the canonical state produces a byte-identical
    file (idempotent no-op)."""
    path = tmp_path / ".gitignore"
    user_content = "# User\n*.pyc\n"
    path.write_text(user_content)
    inject_gitignore_block(path)
    after_first = path.read_text()
    inject_gitignore_block(path)
    after_second = path.read_text()
    assert after_first == after_second


# --------------------------------------------------------------------------- #
# Present + 1 block (different body) -> replace body between markers
# --------------------------------------------------------------------------- #


def test_existing_gitignore_one_block_different_body_is_replaced(tmp_path: Path) -> None:
    """Re-init: if the marker block body drifted (e.g. older version's paths),
    the injector replaces only the BODY between the markers, leaving the user's
    surrounding content untouched."""
    path = tmp_path / ".gitignore"
    stale = (
        "# User content top\n"
        "*.pyc\n"
        "\n"
        f"{GITIGNORE_BEGIN_MARKER}\n"
        ".harness/old-path.yaml\n"
        ".harness/legacy-dir/\n"
        f"{GITIGNORE_END_MARKER}\n"
        "\n"
        "# User content bottom\n"
        "*.log\n"
    )
    path.write_text(stale)
    inject_gitignore_block(path)
    text = path.read_text()
    # Stale paths gone.
    assert ".harness/old-path.yaml" not in text
    assert ".harness/legacy-dir/" not in text
    # Canonical paths present.
    for p in _CANONICAL_PATHS:
        assert p in text, f"missing canonical path: {p}"
    # User content outside markers preserved.
    assert "# User content top" in text
    assert "*.pyc" in text
    assert "# User content bottom" in text
    assert "*.log" in text
    # Exactly one block.
    assert text.count(GITIGNORE_BEGIN_MARKER) == 1
    assert text.count(GITIGNORE_END_MARKER) == 1


# --------------------------------------------------------------------------- #
# Present + ≥2 blocks -> fail loud
# --------------------------------------------------------------------------- #


def test_existing_gitignore_two_blocks_fails_loud(tmp_path: Path) -> None:
    """Two marker blocks → raise GitignoreInjectionError. Never splice (Phase
    7/9/12 marker-discipline lesson — data-loss guard)."""
    path = tmp_path / ".gitignore"
    bad = (
        f"{GITIGNORE_BEGIN_MARKER}\n"
        ".harness/state.yaml\n"
        f"{GITIGNORE_END_MARKER}\n"
        "\n"
        f"{GITIGNORE_BEGIN_MARKER}\n"
        ".harness/events.jsonl\n"
        f"{GITIGNORE_END_MARKER}\n"
    )
    path.write_text(bad)
    before = path.read_text()
    with pytest.raises(GitignoreInjectionError) as excinfo:
        inject_gitignore_block(path)
    assert "2" in str(excinfo.value) or "more than" in str(excinfo.value).lower()
    # File left untouched (never spliced).
    assert path.read_text() == before


# --------------------------------------------------------------------------- #
# Body contents lock-in
# --------------------------------------------------------------------------- #


def test_block_contains_all_8_canonical_paths(tmp_path: Path) -> None:
    """The marker body contains exactly the 8 canonical paths, in order."""
    path = tmp_path / ".gitignore"
    inject_gitignore_block(path)
    text = path.read_text()
    # Extract the body between markers.
    begin_idx = text.index(GITIGNORE_BEGIN_MARKER) + len(GITIGNORE_BEGIN_MARKER)
    end_idx = text.index(GITIGNORE_END_MARKER)
    body = text[begin_idx:end_idx].strip("\n")
    lines = [line for line in body.split("\n") if line.strip()]
    assert lines == list(_CANONICAL_PATHS), lines
