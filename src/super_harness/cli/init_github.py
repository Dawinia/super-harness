"""Read-only GitHub file inspection and prompt-free application for ``init``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from super_harness.cli.init_plan import GithubFileDecision
from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    parse_metadata_block,
)

PR_TEMPLATE_PATH = Path(".github/pull_request_template.md")
WORKFLOW_PATH = Path(".github/workflows/super-harness.yml")

GithubFileOutcome = Literal["wrote", "kept-existing", "declined", "skipped"]


class GithubFileKind(str, Enum):
    """The two GitHub files managed by ``init --setup-github``."""

    PR_TEMPLATE = "pr-template"
    WORKFLOW = "workflow"


class GithubExistingState(str, Enum):
    """Validated state observed without mutating the workspace."""

    MISSING = "missing"
    IDENTICAL = "identical"
    CONFIGURED = "configured"
    CONFLICT = "conflict"


class GithubKeepReason(str, Enum):
    """Why a resolved KEEP should be rendered as a particular advisory."""

    UNCHANGED = "unchanged"
    DECLINED = "declined"
    NON_INTERACTIVE = "non-interactive"


class GithubFileError(RuntimeError):
    """An existing GitHub file cannot be safely inspected or applied."""

    def __init__(self, message: str, *, path: Path, hint: str) -> None:
        super().__init__(message)
        self.path = path
        self.hint = hint


@dataclass(frozen=True)
class GithubFileInspection:
    """Frozen read-only facts and bundled bytes for one managed file."""

    kind: GithubFileKind
    path: Path
    state: GithubExistingState
    existing_content: bytes | None
    desired_content: bytes
    decision: GithubFileDecision | None


@dataclass(frozen=True)
class GithubFilesInspection:
    """Read-only inspection of both managed GitHub files."""

    root: Path
    pr_template: GithubFileInspection
    workflow: GithubFileInspection


@dataclass(frozen=True)
class GithubFilePlan:
    """One fully resolved, prompt-free GitHub file operation."""

    inspection: GithubFileInspection
    decision: GithubFileDecision
    keep_reason: GithubKeepReason = GithubKeepReason.UNCHANGED


@dataclass(frozen=True)
class GithubPlan:
    """Both GitHub file operations with no unresolved decisions."""

    root: Path
    pr_template: GithubFilePlan
    workflow: GithubFilePlan


@dataclass(frozen=True)
class GithubApplyOutcomes:
    """Typed advisory outcomes returned after applying a resolved plan."""

    pr_template: GithubFileOutcome
    workflow: GithubFileOutcome


def _read_existing(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        content = path.read_bytes()
        content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GithubFileError(
            f"could not read existing {path}: {exc}",
            path=path,
            hint="Ensure the file is UTF-8 and readable, then re-run.",
        ) from exc
    return content


def _inspect_template(path: Path, desired: bytes) -> GithubFileInspection:
    existing = _read_existing(path)
    if existing is None:
        return GithubFileInspection(
            GithubFileKind.PR_TEMPLATE,
            path,
            GithubExistingState.MISSING,
            None,
            desired,
            GithubFileDecision.CREATE,
        )
    if existing == desired:
        state = GithubExistingState.IDENTICAL
        decision = GithubFileDecision.KEEP
    else:
        block = parse_metadata_block(existing.decode("utf-8"))
        if block.block_count >= 2:
            raise GithubFileError(
                f"{path} has {block.block_count} super-harness metadata blocks; "
                "refusing to splice (manual cleanup required).",
                path=path,
                hint=(
                    "Remove the duplicate `<!-- super-harness:metadata -->` … "
                    "`<!-- /super-harness:metadata -->` block(s); exactly one is expected."
                ),
            )
        if block.block_count == 1:
            state = GithubExistingState.CONFIGURED
            decision = GithubFileDecision.KEEP
        else:
            state = GithubExistingState.CONFLICT
            decision = None
    return GithubFileInspection(
        GithubFileKind.PR_TEMPLATE,
        path,
        state,
        existing,
        desired,
        decision,
    )


def _inspect_workflow(path: Path, desired: bytes) -> GithubFileInspection:
    existing = _read_existing(path)
    if existing is None:
        state = GithubExistingState.MISSING
        decision = GithubFileDecision.CREATE
    elif existing == desired:
        state = GithubExistingState.IDENTICAL
        decision = GithubFileDecision.KEEP
    else:
        state = GithubExistingState.CONFLICT
        decision = None
    return GithubFileInspection(
        GithubFileKind.WORKFLOW,
        path,
        state,
        existing,
        desired,
        decision,
    )


def inspect_github_files(
    root: Path,
    bundled_pr_template: bytes,
    bundled_workflow: bytes,
) -> GithubFilesInspection:
    """Inspect both GitHub files without creating directories or writing bytes."""

    root = Path(root)
    return GithubFilesInspection(
        root=root,
        pr_template=_inspect_template(root / PR_TEMPLATE_PATH, bundled_pr_template),
        workflow=_inspect_workflow(root / WORKFLOW_PATH, bundled_workflow),
    )


def resolve_github_plan(
    inspection: GithubFilesInspection,
    decisions: Mapping[str, GithubFileDecision],
    keep_reasons: Mapping[str, GithubKeepReason] | None = None,
) -> GithubPlan:
    """Resolve every ambiguity into a closed, prompt-free GitHub plan."""

    frozen_decisions = MappingProxyType(dict(decisions))
    frozen_reasons = MappingProxyType(dict(keep_reasons or {}))

    def resolve(file: GithubFileInspection) -> GithubFilePlan:
        key = file.path.relative_to(inspection.root).as_posix()
        decision = file.decision or frozen_decisions.get(key)
        if decision is None:
            raise ValueError(f"GitHub file decision for {key!r} is unresolved")
        allowed = {
            GithubFileKind.PR_TEMPLATE: {
                GithubFileDecision.CREATE,
                GithubFileDecision.KEEP,
                GithubFileDecision.APPEND,
                GithubFileDecision.OVERWRITE,
            },
            GithubFileKind.WORKFLOW: {
                GithubFileDecision.CREATE,
                GithubFileDecision.KEEP,
                GithubFileDecision.OVERWRITE,
            },
        }[file.kind]
        if decision not in allowed:
            raise ValueError(f"{decision.value} is invalid for {key}")
        if file.state is not GithubExistingState.MISSING and decision is GithubFileDecision.CREATE:
            raise ValueError(f"cannot create existing GitHub file {key}")
        if file.state is GithubExistingState.MISSING and decision is not GithubFileDecision.CREATE:
            raise ValueError(f"missing GitHub file {key} must be created")
        return GithubFilePlan(
            file,
            decision,
            frozen_reasons.get(key, GithubKeepReason.UNCHANGED),
        )

    return GithubPlan(
        inspection.root,
        resolve(inspection.pr_template),
        resolve(inspection.workflow),
    )


def _keep_outcome(reason: GithubKeepReason) -> GithubFileOutcome:
    if reason is GithubKeepReason.DECLINED:
        return "declined"
    if reason is GithubKeepReason.NON_INTERACTIVE:
        return "skipped"
    return "kept-existing"


def apply_github_file(plan: GithubFilePlan) -> GithubFileOutcome:
    """Apply one resolved file plan without consulting any prompt seam."""

    file = plan.inspection
    if plan.decision is GithubFileDecision.KEEP:
        return _keep_outcome(plan.keep_reason)

    if plan.decision is GithubFileDecision.APPEND:
        assert file.existing_content is not None
        existing = file.existing_content.decode("utf-8")
        block = parse_metadata_block(existing)
        if block.block_count:
            raise GithubFileError(
                f"{file.path} changed after inspection; refusing to append",
                path=file.path,
                hint="Re-run init to inspect the current file.",
            )
        placeholder = f"{METADATA_BEGIN}\n{METADATA_END}\n".encode()
        content = existing.rstrip("\n").encode() + b"\n\n" + placeholder
    else:
        content = file.desired_content

    file.path.parent.mkdir(parents=True, exist_ok=True)
    file.path.write_bytes(content)
    return "wrote"


def apply_github_plan(plan: GithubPlan) -> GithubApplyOutcomes:
    """Apply only resolved decisions; this function has no prompt dependency."""

    return GithubApplyOutcomes(
        pr_template=apply_github_file(plan.pr_template),
        workflow=apply_github_file(plan.workflow),
    )
