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

# Canonical paths the injector emits. Production source is the single
# `_CANONICAL_PATHS` constant in `gitignore_injector.py`; this test copy
# mirrors it so the integration assertions check exact-line equality (any
# drift fails `test_block_lines_match_canonical_paths`).
_CANONICAL_PATHS = (
    ".harness/state.yaml",
    ".harness/events.jsonl",
    ".harness/sensor-results/",
    ".harness/verification-results/",
    ".harness/operation-logs/",
    ".harness/pending-reviews/",
    ".harness/gate-disabled",
    ".harness/daemon.pid",
    ".harness/daemon.log",
    ".harness/.*.lock",
    ".claude/settings.local.json",
    ".claude/*.super-harness-backup.*",
    ".codex/hooks.json",
    ".codex/*.super-harness-backup.*",
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
    # All canonical paths present.
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
# Present + unbalanced markers (orphan begin or orphan end) -> fail loud
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("content", "expected_begin", "expected_end"),
    [
        # Orphan begin: 1 begin, 0 end. Without this guard, a subsequent run
        # would silently delete user content trapped between the orphan begin
        # and the new block's end (data-loss vector).
        (
            f"# User top\n*.pyc\n{GITIGNORE_BEGIN_MARKER}\n.harness/state.yaml\n"
            "# trailing user content with no end marker\n",
            1,
            0,
        ),
        # Orphan end: 0 begin, 1 end. Symmetric guard.
        (
            f"# User top\n*.pyc\n.harness/state.yaml\n{GITIGNORE_END_MARKER}\n"
            "# trailing user content\n",
            0,
            1,
        ),
    ],
    ids=["orphan-begin", "orphan-end"],
)
def test_existing_gitignore_unbalanced_markers_fails_loud(
    tmp_path: Path, content: str, expected_begin: int, expected_end: int
) -> None:
    """Unbalanced super-harness markers (orphan begin xor orphan end) →
    raise GitignoreInjectionError. Locks in the data-loss guard: without it,
    a subsequent run would splice over user content trapped between an
    orphan marker and the newly written block."""
    path = tmp_path / ".gitignore"
    path.write_text(content)
    before = path.read_text()
    with pytest.raises(GitignoreInjectionError) as excinfo:
        inject_gitignore_block(path)
    msg = str(excinfo.value)
    assert "unbalanced" in msg.lower(), msg
    # Confirm message includes the exact begin/end counts.
    assert f"{expected_begin} begin" in msg, msg
    assert f"{expected_end} end" in msg, msg
    # File left untouched (never spliced).
    assert path.read_text() == before


# --------------------------------------------------------------------------- #
# Present + non-UTF-8 bytes -> friendly GitignoreInjectionError
# --------------------------------------------------------------------------- #


def test_existing_gitignore_non_utf8_raises_friendly_error(tmp_path: Path) -> None:
    """A non-UTF-8 .gitignore raises GitignoreInjectionError (re-raised from
    the underlying UnicodeDecodeError via ``raise ... from e``). Locks in the
    friendly-error wrapping so callers' existing
    ``except (OSError, GitignoreInjectionError)`` envelope reports it cleanly."""
    path = tmp_path / ".gitignore"
    # Stray 0xff byte that cannot start a UTF-8 sequence.
    path.write_bytes(b"\xff\xfe not utf-8 \n")
    before = path.read_bytes()
    with pytest.raises(GitignoreInjectionError) as excinfo:
        inject_gitignore_block(path)
    msg = str(excinfo.value)
    assert "utf-8" in msg.lower(), msg
    # The original UnicodeDecodeError must be preserved on __cause__.
    assert isinstance(excinfo.value.__cause__, UnicodeDecodeError)
    # File left untouched (decoder failed before any write).
    assert path.read_bytes() == before


# --------------------------------------------------------------------------- #
# Body contents lock-in
# --------------------------------------------------------------------------- #


def test_block_contains_all_canonical_paths(tmp_path: Path) -> None:
    """The marker body contains exactly the canonical paths, in order."""
    path = tmp_path / ".gitignore"
    inject_gitignore_block(path)
    text = path.read_text()
    # Extract the body between markers.
    begin_idx = text.index(GITIGNORE_BEGIN_MARKER) + len(GITIGNORE_BEGIN_MARKER)
    end_idx = text.index(GITIGNORE_END_MARKER)
    body = text[begin_idx:end_idx].strip("\n")
    lines = [line for line in body.split("\n") if line.strip()]
    assert lines == list(_CANONICAL_PATHS), lines


def test_block_covers_daemon_runtime_files(tmp_path: Path) -> None:
    """Regression for the #64 dogfood pothole: the daemon runtime regular files
    `.harness/daemon.pid` and `.harness/daemon.log` must be ignored, or a
    `git add -A` during a lifecycle sweeps them into a commit (attest-verify
    then rejects the change as an undeclared file). The UDS socket
    `.harness/daemon.sock` is deliberately NOT listed — git never tracks a
    socket special file, so it cannot be swept.
    """
    path = tmp_path / ".gitignore"
    inject_gitignore_block(path)
    text = path.read_text()
    assert ".harness/daemon.pid" in text, text
    assert ".harness/daemon.log" in text, text
    assert ".harness/daemon.sock" not in text, "socket is not a git-trackable file"


def test_block_covers_flock_lock_sentinels(tmp_path: Path) -> None:
    """F4: the managed block ignores transient flock sentinels via the
    `.harness/.*.lock` glob (product behavior, not a per-repo hand-edit), so
    downstream `super-harness init` repos never commit `.state.lock` /
    `.events.lock`. Assert the glob is emitted and matches both sentinels.
    """
    import fnmatch

    path = tmp_path / ".gitignore"
    inject_gitignore_block(path)
    text = path.read_text()
    assert ".harness/.*.lock" in text, text
    for sentinel in (".harness/.state.lock", ".harness/.events.lock"):
        assert fnmatch.fnmatch(sentinel, ".harness/.*.lock"), sentinel


def test_committed_gitignore_has_no_standalone_state_lock() -> None:
    """F4 removal guard: once `.harness/.*.lock` lives INSIDE the managed block,
    the old hand-written `.harness/.state.lock` line outside the markers is
    redundant and must be gone. The drift guard only compares the marker-bounded
    block, so a leftover hand line would slip past it and `sync --check` — this
    asserts there is no `.harness/.state.lock` line outside the managed markers.
    """
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".gitignore").read_text(encoding="utf-8")

    begin = text.index(GITIGNORE_BEGIN_MARKER)
    end = text.index(GITIGNORE_END_MARKER) + len(GITIGNORE_END_MARKER)
    outside = text[:begin] + text[end:]
    for line in outside.splitlines():
        assert line.strip() != ".harness/.state.lock", (
            "redundant hand-written .harness/.state.lock outside the managed "
            "block — the `.harness/.*.lock` glob inside the block now covers it"
        )


def test_block_covers_claude_settings_backup_filenames(tmp_path: Path) -> None:
    """Regression for S13: the `.claude/*.super-harness-backup.*` pattern is
    present in the canonical list so users do not accidentally commit the
    timestamped backup files created by `adapter install claude-code`.

    `_settings_merge.py` writes backups as
    ``.claude/settings.json.super-harness-backup.<time_ns>`` — assert that
    pattern (and a couple of realistic example filenames it must match) is
    inside the emitted block. Smoke walkthrough v3 caught this regression
    when the backup file landed in `git add -A` and was pushed to a PR.
    """
    import fnmatch

    path = tmp_path / ".gitignore"
    inject_gitignore_block(path)
    text = path.read_text()
    # The literal pattern is present in the written block.
    assert ".claude/*.super-harness-backup.*" in text, text
    # The pattern matches realistic backup filenames produced by Phase 5
    # `_settings_merge.write_with_backup`.
    pattern = ".claude/*.super-harness-backup.*"
    for sample in (
        ".claude/settings.json.super-harness-backup.1780305201614632000",
        ".claude/settings.json.super-harness-backup.0",
    ):
        assert fnmatch.fnmatch(sample, pattern), sample


def test_committed_repo_gitignore_block_matches_injector() -> None:
    """Dogfood drift-guard: this repo's committed root `.gitignore` super-harness
    block is byte-identical to what `inject_gitignore_block` would render today.

    Guards against `_CANONICAL_PATHS` drifting from the committed block (the gap
    that masked PR #34 review I-1). If this fails, run
    `super-harness sync --gitignore` and commit the updated `.gitignore`.
    """
    from super_harness.engineering.gitignore_injector import _render_block

    repo_root = Path(__file__).resolve().parents[3]
    gitignore = repo_root / ".gitignore"
    assert gitignore.exists(), f"{gitignore} missing"

    text = gitignore.read_text(encoding="utf-8")
    assert text.count(GITIGNORE_BEGIN_MARKER) == 1, "expected exactly one block"
    assert text.count(GITIGNORE_END_MARKER) == 1, "expected exactly one block"

    begin = text.index(GITIGNORE_BEGIN_MARKER)
    end = text.index(GITIGNORE_END_MARKER) + len(GITIGNORE_END_MARKER)
    committed_block = text[begin:end]

    # `_render_block()` ends with a trailing LF; the in-file block does not carry
    # its own trailing LF inside the [begin, end] slice — strip for comparison.
    assert committed_block == _render_block().rstrip("\n"), (
        "Committed .gitignore super-harness block has drifted from "
        "_CANONICAL_PATHS. Run `super-harness sync --gitignore` and commit."
    )


def test_canonical_block_covers_codex_hook_config():
    from super_harness.engineering.gitignore_injector import _render_block
    body = _render_block()
    assert ".codex/hooks.json" in body
    assert ".codex/*.super-harness-backup.*" in body
