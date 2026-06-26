"""Per-workspace language tuning for the dead-doc-reference gate (design 2026-06-26).

Today the ONLY governed-project language coupling in super-harness is the
code-identifier recognizer in ``core/doc_refs.py``. This loader externalizes its
identifier pattern so a non-C-family project (e.g. Ruby ``?``/``!`` methods) can tune
it via ``.harness/language.yaml``:

    doc_refs:
      identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'

Tolerant, fail-safe to the C-family default (mirrors ``core/source_scope.py``): a
missing / unreadable / non-dict / missing-key / empty / un-compilable pattern all
return the default. doc_refs is fail-open, so a bad config never bricks the gate.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Today's behavior, byte-for-byte. The doc-span matcher anchors this (``^{p}$``);
# the source tokenizer wraps it (``(?<!\w){p}(?!\w)``) — both lookarounds reproduce
# the old ``\b...\b`` exactly, including Unicode adjacency. See doc_refs + design §3.3.
IDENTIFIER_PATTERN_DEFAULT = r"[A-Za-z_][A-Za-z0-9_]*"


def language_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "language.yaml"


def load_identifier_pattern(workspace_root: Path) -> str:
    """Return the doc_refs identifier pattern for this workspace, or the C-family
    default. NEVER raises: any problem falls back to the default."""
    f = language_file(workspace_root)
    if not f.is_file():
        return IDENTIFIER_PATTERN_DEFAULT
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return IDENTIFIER_PATTERN_DEFAULT
    dr = data.get("doc_refs") if isinstance(data, dict) else None
    pat = dr.get("identifier_pattern") if isinstance(dr, dict) else None
    if not isinstance(pat, str) or not pat:
        return IDENTIFIER_PATTERN_DEFAULT
    try:
        re.compile(pat)
    except re.error:
        return IDENTIFIER_PATTERN_DEFAULT
    return pat
