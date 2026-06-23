# src/super_harness/core/review_checklist.py
"""Per-reviewer review checklist resolution.

Resolution order (mirrors engineering.reviewer_policy tolerance):
1. `.harness/review-checklists.yaml` → `checklists.<reviewer>` (a non-empty list);
2. else the built-in default for that reviewer.

Absent / corrupt YAML → default. A PRESENT-but-empty list is a config error
(the author meant to configure a checklist but emptied it) → raise.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CHECKLISTS: dict[str, list[str]] = {
    "code-reviewer": [
        "spec-compliance",
        "scope-adherence",
        "code-quality",
        "edge-cases",
    ],
    "plan-reviewer": [
        "spec-coverage",
        "design-soundness",
        "scope-declared",
    ],
}


class ReviewChecklistError(ValueError):
    """`.harness/review-checklists.yaml` is present but a reviewer's list is malformed."""


def _checklists_file(root: Path) -> Path:
    return root / ".harness" / "review-checklists.yaml"


def resolve_checklist(root: Path, reviewer: str) -> list[str]:
    """Return the resolved checklist item ids for `reviewer`."""
    default = list(DEFAULT_CHECKLISTS.get(reviewer, []))
    f = _checklists_file(root)
    if not f.is_file():
        return default
    try:
        parsed: Any = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return default
    if not isinstance(parsed, dict):
        return default
    checklists = parsed.get("checklists")
    if not isinstance(checklists, dict) or reviewer not in checklists:
        return default
    items = checklists[reviewer]
    if not isinstance(items, list) or any(not isinstance(i, str) for i in items):
        raise ReviewChecklistError(
            f"checklists.{reviewer} must be a list of strings, got {items!r}"
        )
    if not items:
        raise ReviewChecklistError(
            f"checklists.{reviewer} is an empty list — remove the key to use the default"
        )
    return list(items)
