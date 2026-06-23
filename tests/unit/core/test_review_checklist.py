# tests/unit/core/test_review_checklist.py
"""Unit tests for core.review_checklist resolution (config override + default)."""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.core.review_checklist import (
    DEFAULT_CHECKLISTS,
    ReviewChecklistError,
    resolve_checklist,
)


def _harness(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_default_when_no_config(tmp_path: Path) -> None:
    _harness(tmp_path)
    assert resolve_checklist(tmp_path, "code-reviewer") == DEFAULT_CHECKLISTS["code-reviewer"]


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
