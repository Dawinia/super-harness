"""Loader for ``.harness/plan-paths.yaml`` — the owner-controlled patterns naming
where a change's plan documents live (design 2026-07-29).

Fail-CLOSED, deliberately unlike `core/source_scope.py`: this list *grants* a gate
allowance, so a corrupt or malformed file yields `[]` (nothing allowed → the state
table blocks, today's behaviour) rather than falling back to a permissive default
the owner may have narrowed away. A **missing** file is not corruption — it means
"never configured" — and gets the built-in default.

Two guard rails are enforced here, not at the gate, so an invalid pattern can never
reach the decision path:

1. every pattern must contain the literal ``{slug}`` — this is what binds the
   allowance to the active change; without it a pattern could name `AGENTS.md`,
   `README.md`, or `**/*.md`;
2. every pattern must end in ``.md`` (case-insensitive) and be a relative path with
   no ``..`` segment.

The gate re-checks the `.md` suffix on the *resolved* path afterwards — the pattern
check here cannot see through a symlink.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

# Matches this repo's own convention: `<date>-<slug>-<suffix>.md`, and one change
# routinely has both a `-design.md` and an `-implementation.md`, so the slug sits in
# the middle and an exact `{slug}.md` would match none of them.
DEFAULT_PLAN_PATHS: tuple[str, ...] = ("docs/plans/*{slug}*.md",)

SLUG_PLACEHOLDER = "{slug}"


def plan_paths_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "plan-paths.yaml"


def _is_valid_pattern(pattern: object) -> bool:
    if not isinstance(pattern, str) or not pattern:
        return False
    if SLUG_PLACEHOLDER not in pattern:
        return False
    if not pattern.lower().endswith(".md"):
        return False
    pp = PurePosixPath(pattern)
    return not pp.is_absolute() and ".." not in pp.parts


def load_plan_paths(workspace_root: Path) -> list[str]:
    """Return the validated plan-path patterns. NEVER raises.

    Missing file → built-in default. Anything else that is not a well-formed list of
    valid patterns → `[]` (fail-closed).
    """
    f = plan_paths_file(workspace_root)
    try:
        if not f.is_file():
            return list(DEFAULT_PLAN_PATHS)
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("plan_paths")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if _is_valid_pattern(p)]
