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
        "doc-impact",
    ],
    "plan-reviewer": [
        "architecture",
        "tech-choices",
        "conventions",
        "spec-coverage",
    ],
}

# Definitions live HERE, keyed by item id, and never inside the resolved list.
# `resolve_checklist` returns `list[str]` because that list is spliced straight
# into the frozen verdict schema's `enum` (core/review_verdict.py), compared as a
# set of strings by `resolve_source_baseline`, and hashed into `bundle_digest`.
# Enriching it into id+definition objects would break all three at once.
#
# An id with no entry here is not an error: a checklist configured through
# `.harness/review-checklists.yaml` renders as bare ids and still works.
#
# The wording is a measured artefact, not a paraphrase — plan review yield was
# replayed against these exact four definitions. Reword them only with evidence.
CHECKLIST_DEFINITIONS: dict[str, str] = {
    "architecture": (
        "does the design hold up? Layer ownership, dependency direction, state "
        "and who owns it, failure paths. Test: would a system built to this "
        "design be wrong, deadlock, or silently deliver the wrong value?"
    ),
    "tech-choices": (
        "are the chosen libraries, mechanisms and data structures able to carry "
        "the responsibilities assigned to them, and do they conflict with "
        "choices already made in this repository?"
    ),
    "conventions": (
        "does this conform to the norms, ratified decisions and established "
        "practice of THIS repository?"
    ),
    "spec-coverage": (
        "is everything the spec/requirement asks for actually covered by this "
        "plan, and do the acceptance criteria match the body?"
    ),
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
