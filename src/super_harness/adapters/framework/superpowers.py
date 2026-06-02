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
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import (
    Actor,
    Event,
    EventSchemaError,
    Framework,
    parse_event_line,
)
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id

_ACTOR = Actor(type="adapter", identifier="superpowers-adapter")
_FRAMEWORK: Framework = "superpowers"

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


def _iter_marked(workspace: Path) -> Iterator[tuple[Path, dict[str, Any], str]]:
    """Yield `(path, frontmatter, text)` for every marked artifact under candidate dirs.

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
                yield p, fm, text


def _first_heading(text: str) -> str | None:
    """Return the first `# ` heading's text, or None."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def _seen_from_events(workspace: Path) -> set[tuple[str, str]]:
    """Read `(change_id, type)` pairs already in events.jsonl (absent → empty).

    Malformed lines are skipped (reducer warn+skip policy, lifecycle §3.8.1).
    Mirrors OpenSpecAdapter's dedup so re-scans yield only unseen events.
    """
    path = events_path(workspace)
    if not path.exists():
        return set()
    seen: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            continue
        seen.add((ev.change_id, ev.type))
    return seen


def _plan_payload(fm: dict[str, Any]) -> dict[str, Any]:
    """Build the plan_ready payload from a plan artifact's frontmatter.

    Only the lifecycle-event-model §3.2 keys the reducer consumes are carried,
    and only when present + non-null. Absent → empty payload (HG-05 intent).
    """
    payload: dict[str, Any] = {}
    for key in ("affected_anchors", "scope", "tier_hint"):
        value = fm.get(key)
        if value is not None:
            payload[key] = value
    return payload


def scan_artifacts(workspace: Path, seen: set[tuple[str, str]]) -> list[Event]:
    """Pure parse-and-emit core: group marked artifacts by `change:` slug and
    return unseen `intent_declared` / `plan_ready` events in dependency order.

    Per-slug semantics (design doc Decision 3):
    - `stage: design` → contributes intent only.
    - `stage: plan` or omitted → contributes intent (if new) + plan_ready.
    - A slug with only a plan still gets a synthesized intent_declared first, so
      emit-time validation (intent must precede plan_ready) holds.
    `seen` suppresses re-emission (multi-emit is legal but noisy).
    """
    groups: dict[str, dict[str, Any]] = {}
    for _path, fm, text in _iter_marked(workspace):
        slug = fm["change"]
        g = groups.setdefault(slug, {"description": None, "has_plan": False, "plan_fm": None})
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc:
            desc = _first_heading(text)
        if g["description"] is None and desc:
            g["description"] = desc
        if fm.get("stage") != "design":  # plan or omitted
            g["has_plan"] = True
            if g["plan_fm"] is None:
                g["plan_fm"] = fm

    events: list[Event] = []
    for slug in sorted(groups):
        g = groups[slug]
        if (slug, "intent_declared") not in seen:
            events.append(
                Event(
                    event_id=new_event_id(),
                    type="intent_declared",
                    change_id=slug,
                    timestamp=utc_now_iso(),
                    actor=_ACTOR,
                    framework=_FRAMEWORK,
                    payload={"description": g["description"] or slug},
                )
            )
        if g["has_plan"] and (slug, "plan_ready") not in seen:
            events.append(
                Event(
                    event_id=new_event_id(),
                    type="plan_ready",
                    change_id=slug,
                    timestamp=utc_now_iso(),
                    actor=_ACTOR,
                    framework=_FRAMEWORK,
                    payload=_plan_payload(g["plan_fm"] or {}),
                )
            )
    return events


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
        """One-shot scan: yield unseen lifecycle events for `workspace`.

        Read-only — only YIELDS events; the EventWriter.emit + state refresh
        happens at callers (mirrors OpenSpecAdapter / PlainAdapter).
        """
        yield from scan_artifacts(workspace, _seen_from_events(workspace))

    def get_state(self, change_id: str) -> dict[str, Any] | None:
        """Derived presence state for `change_id`, or None if no marked artifact.

        Raises RuntimeError if the adapter was registry-built (no workspace) —
        resolving against cwd would silently mis-answer under the daemon (cwd=/).
        """
        if self._workspace is None:
            raise RuntimeError(
                "SuperpowersAdapter.get_state requires a workspace; construct "
                "SuperpowersAdapter(workspace=root) (registry-built instances have none)"
            )
        design_path, plan_path = self._artifact_paths(self._workspace, change_id)
        if design_path is None and plan_path is None:
            return None
        return {
            "change_id": change_id,
            "design": design_path is not None,
            "design_path": str(design_path) if design_path else None,
            "plan": plan_path is not None,
            "plan_path": str(plan_path) if plan_path else None,
        }

    def spec_paths(self, workspace: Path, change_id: str) -> dict[str, str]:
        """Best-effort resolved design/plan paths for ${SPEC_PATH}/${PLAN_PATH}.

        superpowers filenames are not derivable from the slug (the slug is the
        frontmatter marker, not the filename), so this scans for the marked
        artifacts. Returns only the keys actually found.
        """
        design_path, plan_path = self._artifact_paths(workspace, change_id)
        result: dict[str, str] = {}
        if design_path is not None:
            result["spec"] = str(design_path)
        if plan_path is not None:
            result["plan"] = str(plan_path)
        return result

    @staticmethod
    def _artifact_paths(
        workspace: Path, change_id: str
    ) -> tuple[Path | None, Path | None]:
        """Find the (design, plan) artifact paths for `change_id` (None if absent)."""
        design_path: Path | None = None
        plan_path: Path | None = None
        for path, fm, _text in _iter_marked(workspace):
            if fm["change"] != change_id:
                continue
            if fm.get("stage") == "design":
                design_path = path
            else:
                plan_path = path
        return design_path, plan_path

    def watch_paths(self, workspace: Path) -> list[Path]:
        return [workspace / rel for rel in _CANDIDATE_DIRS if (workspace / rel).is_dir()]

    def verification_checks(self) -> list[dict[str, Any]]:
        # superpowers ships no native validate command (unlike `openspec validate`).
        return []

    def agents_md_subsection(self) -> str:
        return (
            "<!-- super-harness framework: superpowers -->\n"
            "- Drive the lifecycle with the superpowers skills "
            "(brainstorming → writing-plans → TDD). Plans live under `docs/plans/`.\n"
            "- Mark an artifact for super-harness with YAML frontmatter: "
            "`change: <slug>` (identity) plus optional `stage: design|plan`.\n"
            "  - `stage: design` declares intent; `stage: plan` (or omitted) means "
            "plan ready. A plan may also carry `affected_anchors` / `scope` / `tier_hint`.\n"
            "- Branch naming is yours — the slug travels in the `change:` frontmatter "
            "(and PR metadata), not the branch name.\n"
            "<!-- /super-harness framework: superpowers -->\n"
        )
