# L1 anchor (HG-D self-host) — @capability:capability-framework-adapter-builtin
"""SuperpowersAdapter — FrameworkAdapter for workspaces driven by superpowers.

Discovery is anchored on a super-harness-owned frontmatter marker (`change:` /
`stage:`), NOT on superpowers' version-specific artifact paths or filenames —
those moved between superpowers versions and the installed version is not
detectable from the workspace. See
docs/plans/2026-06-02-superpowers-framework-adapter-design.md for the rationale.
"""
from __future__ import annotations

from typing import Any

import yaml


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse a leading YAML frontmatter block (`--- … ---`) into a mapping.

    Returns `{}` for: no leading `---` fence, an unclosed block, a YAML parse
    error, or frontmatter that is not a mapping (e.g. a list/scalar). Never
    raises — a malformed artifact must not crash the read-only scan.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            block = "\n".join(lines[1:i])
            break
    else:
        return {}  # no closing fence
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
