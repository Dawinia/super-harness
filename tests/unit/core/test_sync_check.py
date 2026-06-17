"""Unit tests for `run_sync_check` — the `sync --check` drift engine.

It renders what `sync` WOULD write into a throwaway temp copy (reusing the exact
init/sync render path) and diffs against the on-disk file, reporting drift without
writing anything.
"""

from __future__ import annotations

from pathlib import Path

from super_harness.core.sync_check import run_sync_check
from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.engineering.gitignore_injector import inject_gitignore_block


def test_freshly_rendered_agents_md_is_in_sync(tmp_path: Path) -> None:
    """A repo whose AGENTS.md was just rendered shows NO AGENTS.md drift."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert result.in_sync
    assert result.drift == []


def test_hand_mutated_agents_md_section_is_drift(tmp_path: Path) -> None:
    """Editing inside the managed section (the DO NOT EDIT block) is reported as
    drift, with a diff, and the file on disk is NOT modified by the check."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    original = agents.read_text()
    agents.write_text(original.replace("### Branch naming", "### Branch naming EDITED"))
    mutated = agents.read_text()

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert not result.in_sync
    assert len(result.drift) == 1
    assert result.drift[0].name == "AGENTS.md"
    assert "Branch naming" in result.drift[0].diff
    # --check never writes.
    assert agents.read_text() == mutated


def test_stale_version_stamp_is_drift(tmp_path: Path) -> None:
    """If AGENTS.md was rendered at an OLD version, checking at a NEW version
    reports drift (the begin-marker version stamp differs)."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.0.9")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert not result.in_sync
    assert result.drift[0].name == "AGENTS.md"


def test_content_outside_markers_is_not_drift(tmp_path: Path) -> None:
    """The managed-only guarantee: user content OUTSIDE the super-harness markers
    is never inspected, so adding it does not register as drift."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    agents.write_text("# My project notes\n\n" + agents.read_text() + "\nFooter.\n")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert result.in_sync


def test_absent_agents_md_is_drift(tmp_path: Path) -> None:
    """A repo with NO AGENTS.md is drifted from what `sync` would write (it would
    create the section), so --check reports drift rather than silently passing."""
    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert not result.in_sync
    assert result.drift[0].name == "AGENTS.md"


def test_freshly_injected_gitignore_is_in_sync(tmp_path: Path) -> None:
    """A .gitignore whose block was just injected shows NO .gitignore drift."""
    inject_gitignore_block(tmp_path / ".gitignore")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=False, check_gitignore=True
    )

    assert result.in_sync


def test_mutated_gitignore_block_is_drift(tmp_path: Path) -> None:
    """Editing inside the managed .gitignore block is reported as drift; the file
    on disk is NOT modified by the check."""
    gi = tmp_path / ".gitignore"
    inject_gitignore_block(gi)
    original = gi.read_text()
    # Drop a real canonical path line from inside the managed block.
    # `.harness/state.yaml` IS in `_CANONICAL_PATHS` (verified); the socket path
    # is NOT, so do not use it here.
    mutated = "\n".join(
        line for line in original.splitlines() if line != ".harness/state.yaml"
    ) + "\n"
    assert mutated != original, "test bug: nothing removed — path not in block"
    gi.write_text(mutated)

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=False, check_gitignore=True
    )

    assert not result.in_sync
    assert result.drift[0].name == ".gitignore"
    assert gi.read_text() == mutated


def test_both_artifacts_checked_together(tmp_path: Path) -> None:
    """With both legs enabled and both freshly rendered, the repo is in sync."""
    render_super_harness_section(tmp_path, tmp_path / "AGENTS.md", "0.1.0")
    inject_gitignore_block(tmp_path / ".gitignore")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=True
    )

    assert result.in_sync
