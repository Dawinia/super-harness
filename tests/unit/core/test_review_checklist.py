# tests/unit/core/test_review_checklist.py
"""Unit tests for core.review_checklist resolution (config override + default)."""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.core.review_checklist import (
    CHECKLIST_DEFINITIONS,
    DEFAULT_CHECKLISTS,
    ReviewChecklistError,
    resolve_checklist,
)


def _harness(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_default_when_no_config(tmp_path: Path) -> None:
    _harness(tmp_path)
    items = resolve_checklist(tmp_path, "code-reviewer")
    assert items == DEFAULT_CHECKLISTS["code-reviewer"]
    assert "doc-impact" in items  # C-layer: semantic doc-impact must be disposed


def test_config_override(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text(
        "checklists:\n  code-reviewer:\n    - custom-a\n    - custom-b\n"
    )
    assert resolve_checklist(root, "code-reviewer") == ["custom-a", "custom-b"]


def test_corrupt_config_falls_back_to_default(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text("checklists: [unbalanced\n")
    assert resolve_checklist(root, "code-reviewer") == DEFAULT_CHECKLISTS["code-reviewer"]


def test_empty_override_list_is_rejected(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text(
        "checklists:\n  code-reviewer: []\n"
    )
    with pytest.raises(ReviewChecklistError):
        resolve_checklist(root, "code-reviewer")


def test_plan_reviewer_default_is_the_four_defined_items(tmp_path: Path) -> None:
    """The plan checklist names what a human asks of a plan, not opaque ids.

    `design-soundness` / `scope-declared` were undefined anywhere in the code,
    docs or config; `design-soundness` alone carried roughly half of all recorded
    plan rejections. `scope-declared` is gone because two mechanical gates already
    decide it (`sensors/verification_runner.py`, `engineering/attestation.py`).
    """
    _harness(tmp_path)
    assert resolve_checklist(tmp_path, "plan-reviewer") == [
        "architecture",
        "tech-choices",
        "conventions",
        "spec-coverage",
    ]


def test_every_default_plan_item_has_a_definition() -> None:
    """An undefined checklist id is the defect this change exists to remove."""
    for item in DEFAULT_CHECKLISTS["plan-reviewer"]:
        assert CHECKLIST_DEFINITIONS.get(item), item


def test_resolution_stays_a_list_of_plain_strings(tmp_path: Path) -> None:
    """Load-bearing: the resolved list is spliced into the frozen verdict schema's
    `enum`, compared as a set of strings by `resolve_source_baseline`, and hashed
    into `bundle_digest`. Definitions must never enter it."""
    _harness(tmp_path)
    for reviewer in ("plan-reviewer", "code-reviewer"):
        items = resolve_checklist(tmp_path, reviewer)
        assert all(isinstance(i, str) for i in items)


def test_configured_ids_without_definitions_still_resolve(tmp_path: Path) -> None:
    """An adopter's own checklist keeps working; definitions are optional."""
    root = _harness(tmp_path)
    (root / ".harness" / "review-checklists.yaml").write_text(
        "checklists:\n  plan-reviewer:\n    - house-style\n"
    )
    assert resolve_checklist(root, "plan-reviewer") == ["house-style"]
    assert "house-style" not in CHECKLIST_DEFINITIONS
