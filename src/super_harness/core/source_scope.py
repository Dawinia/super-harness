"""Loader for ``.harness/source-paths.yaml`` (include/exclude glob lists).

Keys are nested under a top-level ``source_paths:`` mapping. Missing file or
key → defaults. Corrupt YAML → defaults (a source-paths typo must not brick the
gate). See design §3.2.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_INCLUDE: list[str] = ["**/*"]
DEFAULT_EXCLUDE: list[str] = ["docs/**"]


def source_paths_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "source-paths.yaml"


def load_source_scope(workspace_root: Path) -> tuple[list[str], list[str]]:
    f = source_paths_file(workspace_root)
    if not f.is_file():
        return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
    sp = data.get("source_paths") if isinstance(data, dict) else None
    if not isinstance(sp, dict):
        return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
    include = sp.get("include")
    if not isinstance(include, list) or not include:
        include = DEFAULT_INCLUDE
    exclude = sp.get("exclude")
    if not isinstance(exclude, list) or not exclude:
        exclude = DEFAULT_EXCLUDE
    return list(include), list(exclude)
