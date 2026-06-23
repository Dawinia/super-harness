# tests/unit/core/test_review_bundle.py
"""Unit tests for core.review_bundle assembly."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from super_harness.core.review_bundle import (
    BundleError,
    assemble_bundle,
    load_base_branch,
)


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _change(ws: Path, declared: list[str]) -> str:
    """Seed a change in AWAITING_CODE_REVIEW with declared scope.files."""
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    seq = [
        ("intent_declared", {}),
        ("plan_ready", {"scope": {"files": declared}}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
    ]
    for t, payload in seq:
        EventWriter(events_path(ws)).emit(
            Event(
                event_id=new_event_id(), type=t, change_id="c",
                timestamp="2026-06-23T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain", payload=payload,
            )
        )
    refresh_state_after_emit(ws)
    return "c"


def _repo_with_change(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    (tmp_path / "other.py").write_text("o1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    (tmp_path / "other.py").write_text("o2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    return tmp_path


def test_assemble_bundle_happy(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["diff_in_scope"] == ["src/a.py"]
    assert b["out_of_scope"] == ["other.py"]
    assert b["checklist"] == ["spec-compliance", "scope-adherence", "code-quality", "edge-cases"]
    assert b["bundle_digest"]  # non-empty
    assert b["base"] == "main"


def test_assemble_bundle_rejects_dirty_in_scope_tree(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    (ws / "src" / "a.py").write_text("uncommitted\n")  # dirty in-scope file
    with pytest.raises(BundleError, match="commit"):
        assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")


def test_assemble_bundle_empty_scope_inert_digest(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, [])  # no declared scope
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["diff_in_scope"] == []
    # empty-scope digest is the constant empty-diff digest (freshness inert; documented)
    assert b["bundle_digest"]


def test_load_base_branch_default_and_override(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir(parents=True)
    assert load_base_branch(tmp_path) == "main"
    (tmp_path / ".harness" / "policy.yaml").write_text("review:\n  base_branch: develop\n")
    assert load_base_branch(tmp_path) == "develop"
