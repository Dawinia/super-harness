# L1 anchor (HG-D self-host) — @capability:capability-framework-adapter-builtin
"""SuperpowersAdapter — FrameworkAdapter for workspaces driven by superpowers.

Discovery is anchored on a super-harness-owned frontmatter marker (`change:` /
`stage:`), NOT on superpowers' version-specific artifact paths or filenames —
those moved between superpowers versions and the installed version is not
detectable from the workspace. See
docs/plans/2026-06-02-superpowers-framework-adapter-design.md for the rationale.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import yaml

from super_harness.adapters import FrameworkAdapter
from super_harness.core.events import Event

# Candidate artifact dirs spanning known superpowers eras. Discovery filters
# these by the `change:` frontmatter marker, so the location a given superpowers
# version chose does not matter — the marker is the anchor. A `.harness`-config
# override is deferred to v0.2 (YAGNI).
_CANDIDATE_DIRS: tuple[str, ...] = (
    "docs/plans",
    "docs/superpowers/plans",
    "docs/superpowers/specs",
)


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


def _iter_marked(workspace: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield `(path, frontmatter)` for every marked artifact under candidate dirs.

    "Marked" = a `.md` whose frontmatter carries a non-empty string `change:`.
    Dirs are walked in `_CANDIDATE_DIRS` order, files sorted within each, so the
    scan is deterministic. Unreadable/binary files are skipped (never raises).
    """
    for rel in _CANDIDATE_DIRS:
        d = workspace / rel
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = _parse_frontmatter(text)
            change = fm.get("change")
            if isinstance(change, str) and change:
                yield p, fm


class SuperpowersAdapter(FrameworkAdapter):
    """FrameworkAdapter for projects driven by superpowers.

    Identity comes from a `change:` frontmatter marker (not the git branch or
    superpowers' version-specific paths). The registry instantiates with `cls()`
    (workspace=None); `get_state` requires an explicit workspace and raises
    otherwise, mirroring OpenSpecAdapter.
    """

    name: ClassVar[str] = "superpowers"
    version: ClassVar[str] = "0.1.0"
    is_fallback: ClassVar[bool] = False

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace

    def detect(self, workspace: Path) -> bool:
        return any(_iter_marked(workspace))

    def observe(self, workspace: Path) -> Iterator[Event]:
        raise NotImplementedError  # Task 3

    def get_state(self, change_id: str) -> dict[str, Any] | None:
        raise NotImplementedError  # Task 5

    def verification_checks(self) -> list[dict[str, Any]]:
        raise NotImplementedError  # Task 5

    def agents_md_subsection(self) -> str:
        raise NotImplementedError  # Task 5
