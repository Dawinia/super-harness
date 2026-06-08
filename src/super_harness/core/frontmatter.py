"""Shared leading-YAML-frontmatter splitter (single source, no drift).

``--- … ---`` block at the top of a file → ``(mapping, body)``. Returns None
when there is no opening fence, no closing fence, a YAML parse error, or the
frontmatter is not a mapping. Callers pick the policy: the read-only adapter
scan maps None→{}; decision-record loading raises (fail-closed).
"""
from __future__ import annotations

import yaml


def split_frontmatter(text: str) -> tuple[dict, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return None
            if not isinstance(data, dict):
                return None
            return data, "\n".join(lines[i + 1 :]).strip()
    return None
