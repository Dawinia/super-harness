"""OpenSpecAdapter — FrameworkAdapter for workspaces using the OpenSpec framework."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

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

# Adapter-emitted events carry a synthetic actor (verified against the CLI emit
# sites in cli/change.py: human emits use Actor(type="human", identifier="cli");
# adapter-observed events use the adapter identifier so the audit trail records
# "this event was inferred from on-disk artifacts, not user-initiated").
_ACTOR = Actor(type="adapter", identifier="openspec-adapter")
_FRAMEWORK: Framework = "openspec"


def _change_state(change_dir: Path) -> dict[str, Any] | None:
    """Derive framework state for a single change dir from file presence.

    Returns None when the change dir is absent (per get_state's contract). Does
    NOT read frontmatter — the dict is purely presence flags + resolved paths so
    it can populate Event.framework_state cheaply and deterministically.
    """
    if not change_dir.is_dir():
        return None
    proposal = change_dir / "proposal.md"
    tasks = change_dir / "tasks.md"
    specs_delta_dir = change_dir / "specs"
    return {
        "change_id": change_dir.name,
        "change_dir": str(change_dir),
        "proposal": proposal.is_file(),
        "proposal_path": str(proposal),
        "tasks": tasks.is_file(),
        "tasks_path": str(tasks),
        "specs_delta": specs_delta_dir.is_dir(),
    }


def _description_from_proposal(proposal: Path, fallback: str) -> str:
    """Extract a one-line description from proposal.md, else `fallback`.

    proposal.md is plain markdown (no frontmatter). Heuristic (verified
    openspec@1.3.1 proposal layout): the first non-empty prose line under a
    `## Why` heading. If there is no `## Why` heading, or it has no prose before
    the next heading / EOF, fall back to the directory name.
    """
    try:
        text = proposal.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fallback
    in_why = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            # A heading boundary: enter the Why section, or leave it (any other
            # heading ends a Why section we were already inside).
            heading = line.lstrip("#").strip().lower()
            in_why = heading == "why"
            continue
        if in_why and line:
            return line
    return fallback


def scan_changes(workspace: Path, seen: set[tuple[str, str]]) -> list[Event]:
    """Pure parse-and-emit core: scan openspec changes, return unseen events.

    `seen` is the set of already-emitted `(change_id, type)` pairs (computed by
    `observe` from events.jsonl). Re-emitting is itself LEGAL under the lifecycle
    model (multi-emit is allowed), so this dedup honours the "yield only unseen"
    contract + reduces noise — it is NOT a correctness gate.

    Semantics (verified openspec@1.3.1):
    - Iterate `openspec/changes/<slug>/`; SKIP the `changes/archive/` subdir
      (archived changes must not re-emit).
    - `proposal.md` present -> `intent_declared`.
    - `tasks.md` present -> `plan_ready`. scope.files is deliberately EMPTY:
      tasks.md is a TASK checklist, not a file list (mining scope out of it is a
      category error); drift detection uses the source-paths.yaml fallback.
    - When a change has BOTH, emit `intent_declared` BEFORE `plan_ready`
      (emit-time validation requires intent_declared to precede plan_ready).
    """
    changes_dir = workspace / "openspec" / "changes"
    if not changes_dir.is_dir():
        return []

    events: list[Event] = []
    # Deterministic order across changes so multi-change scans are reproducible.
    for change_dir in sorted(changes_dir.iterdir()):
        if not change_dir.is_dir():
            continue
        # Skip the archive subdir wholesale — archived changes must not re-emit.
        if change_dir.name == "archive":
            continue
        slug = change_dir.name
        proposal = change_dir / "proposal.md"
        tasks = change_dir / "tasks.md"

        # framework_state is shared with get_state's derivation (single source).
        fw_state = _change_state(change_dir)

        # Ordering: intent_declared must precede plan_ready for the same change.
        if proposal.is_file() and (slug, "intent_declared") not in seen:
            events.append(
                Event(
                    event_id=new_event_id(),
                    type="intent_declared",
                    change_id=slug,
                    timestamp=utc_now_iso(),
                    actor=_ACTOR,
                    framework=_FRAMEWORK,
                    framework_state=fw_state,
                    payload={
                        "description": _description_from_proposal(proposal, slug)
                    },
                )
            )
        if tasks.is_file() and (slug, "plan_ready") not in seen:
            events.append(
                Event(
                    event_id=new_event_id(),
                    type="plan_ready",
                    change_id=slug,
                    timestamp=utc_now_iso(),
                    actor=_ACTOR,
                    framework=_FRAMEWORK,
                    framework_state=fw_state,
                    # NB: no `scope` key — see docstring (category error to mine
                    # scope.files from a task checklist; reducer leaves cs.scope
                    # at its default and drift uses source-paths.yaml).
                    payload={},
                )
            )
    return events


def _seen_from_events(workspace: Path) -> set[tuple[str, str]]:
    """Read `(change_id, type)` pairs already in the workspace's events.jsonl.

    Absent file -> empty set. Malformed lines are skipped (reducer warn+skip
    policy, lifecycle §3.8.1): a corrupt line must not crash the read-only scan.
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


class OpenSpecAdapter(FrameworkAdapter):
    """FrameworkAdapter for projects using OpenSpec.

    Detection heuristic: openspec@1.3.1 `init` creates openspec/changes/ and
    openspec/specs/ but no config file (project.yaml never existed;
    config.yaml is interactive-config-only). Both dirs must be present.

    The registry instantiates adapters with `cls()` (no args), so `workspace`
    defaults to None. Registry-built instances support `detect`, `observe`, and
    `watch_paths` (all take an explicit workspace arg). `get_state` is NOT
    available on registry-built instances — it raises RuntimeError — because
    falling back to cwd would silently return None for every change (the daemon
    chdir's to "/"). Callers that need `get_state` must construct
    `OpenSpecAdapter(workspace=root)` explicitly.
    """

    name: ClassVar[str] = "openspec"
    version: ClassVar[str] = "0.1.0"
    is_fallback: ClassVar[bool] = False

    def __init__(self, workspace: Path | None = None) -> None:
        # Stored for get_state (which has no workspace param per the ABC). None
        # means the adapter was registry-built (cls()); get_state raises on None
        # rather than falling back to cwd (silent-wrong when daemon chdir's to /).
        self._workspace = workspace

    def detect(self, workspace: Path) -> bool:
        # verified openspec@1.3.1: `init` creates changes/ + specs/ but NO config
        # file (project.yaml never existed; config.yaml is interactive-config-only).
        return (
            (workspace / "openspec" / "changes").is_dir()
            and (workspace / "openspec" / "specs").is_dir()
        )

    def observe(self, workspace: Path) -> Iterator[Event]:
        """Synchronous scan: yield unseen lifecycle events for `workspace`.

        Mirrors PlainAdapter.observe's iterator contract — this is a one-shot
        scan, NOT a long-running loop. Computes `seen` from events.jsonl (absent
        -> empty) and delegates to the pure `scan_changes`. Read-only: it only
        YIELDS events; writing (EventWriter.emit + refresh) happens at callers.
        """
        seen = _seen_from_events(workspace)
        yield from scan_changes(workspace, seen)

    def get_state(self, change_id: str) -> dict[str, Any] | None:
        """Return derived framework state for `change_id`, or None if absent.

        Derives from file presence under `openspec/changes/<change_id>/`. Shares
        `_change_state` with the scan so emitted events and queried state agree.

        Raises:
            RuntimeError: the adapter was built without a workspace (registry
                builds via `cls()`). Resolving against cwd would silently return
                None for every change (the daemon chdir's to "/"), so fail loud —
                construct `OpenSpecAdapter(workspace=root)` to use get_state.
        """
        if self._workspace is None:
            raise RuntimeError(
                "OpenSpecAdapter.get_state requires a workspace; construct "
                "OpenSpecAdapter(workspace=root) (registry-built instances have none)"
            )
        return _change_state(self._workspace / "openspec" / "changes" / change_id)

    def watch_paths(self, workspace: Path) -> list[Path]:
        # The daemon watches the changes dir so it knows when to re-run observe.
        return [workspace / "openspec" / "changes"]

    def spec_paths(self, workspace: Path, change_id: str) -> dict[str, str]:
        # Pure path derivation (HG-01) — mirrors the join in `_change_state` but does
        # NO I/O, so it is daemon-safe (cwd=`/`) and never raises like `get_state`.
        base = workspace / "openspec" / "changes" / change_id
        return {"spec": str(base / "proposal.md"), "plan": str(base / "tasks.md")}

    def verification_checks(self) -> list[dict[str, Any]]:
        return [{
            "id": "openspec-validate",
            # verified openspec@1.3.1: change-id is a POSITIONAL arg, not --change.
            "command": "openspec validate ${SLUG} --strict --json",
            "must_pass": True,
            "provided_by": "openspec-adapter",
        }]

    def agents_md_subsection(self) -> str:
        # Content mirrors engineering-integration §2.2 framework subsection
        # (cross-referenced to avoid drift). Commands verified openspec@1.3.1.
        return (
            "<!-- super-harness framework: openspec -->\n"
            "- OpenSpec change lives in `openspec/changes/<slug>/` "
            "(proposal.md / tasks.md / specs/ deltas).\n"
            "- Validate before push: `openspec validate <slug> --strict`.\n"
            "- After merge, fold spec deltas into `openspec/specs/`: "
            "`openspec archive <slug>`.\n"
            "<!-- /super-harness framework: openspec -->\n"
        )
