"""Slug validation for change identifiers.

Per cli-command-surface §2.3 (`change start <slug>`): a `<slug>` is the
human-chosen suffix of a `change_id` (e.g. `add-foo` → `ch_2026-05-27-add-foo`).
Slugs are surfaced in filenames, branch names, log lines, and URLs, so they
must be portable across filesystems + shells.

Rules (§2.3 "slug syntax"):
- length 3-80 (inclusive)
- kebab-case alphanumeric: `^[a-z0-9]+(-[a-z0-9]+)*$` (no consecutive dashes)
- no leading / trailing dash, no uppercase, no underscore, no whitespace, no
  non-ASCII, no punctuation, no consecutive dashes (matches npm / Cargo /
  Go-module slug conventions).

Called by Task 2.3 (`change start`) before emitting `intent_declared`.
"""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MIN, _MAX = 3, 80


class SlugError(ValueError):
    """Raised when a slug violates length or character rules (cli-command-surface §2.3)."""


def validate_slug(slug: str) -> None:
    """Validate `slug` per cli-command-surface §2.3; raise SlugError if invalid."""
    if not isinstance(slug, str) or not (_MIN <= len(slug) <= _MAX):
        raise SlugError(f"slug length must be {_MIN}-{_MAX}, got {len(slug)}")
    if not _SLUG_RE.match(slug):
        raise SlugError(f"slug must be kebab-case alphanumeric: {slug!r}")
