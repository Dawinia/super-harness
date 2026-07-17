"""Immutable preflight and planning boundary for ``super-harness init``.

The module deliberately stops before prompting, rendering, or applying a plan.
``inspect_workspace`` only observes filesystem and executable availability;
``build_init_plan`` is pure for a captured request, preflight, and choice set.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

import yaml

from super_harness.engineering.review_governance import (
    ReviewGovernanceError,
    load_review_governance,
)
from super_harness.engineering.review_profiles import (
    ReviewProfilesError,
    load_review_profiles,
)


class InteractionMode(str, Enum):
    """How init choices are collected outside this planning boundary."""

    NON_INTERACTIVE = "non-interactive"
    LINE = "line"
    GUIDED = "guided"


class ReviewWrite(str, Enum):
    """How existing review configuration is treated."""

    PRESERVE = "preserve"
    UPDATE = "update"
    RESET = "reset"


class FileAction(str, Enum):
    """Closed executor-neutral disposition for one planned path."""

    CREATE = "create"
    UPDATE = "update"
    PRESERVE = "preserve"
    SKIP = "skip"


class HarnessState(str, Enum):
    """Observed state of the workspace's harness directory."""

    ABSENT = "absent"
    INITIALIZED = "initialized"
    PARTIAL = "partial"


class ExistingFileDecision(str, Enum):
    """Interactive decision for an existing user-owned file."""

    PRESERVE = "preserve"
    UPDATE = "update"


class GitHubDecision(str, Enum):
    """Whether optional GitHub files belong in the plan."""

    SKIP = "skip"
    CREATE = "create"


class GithubFileDecision(str, Enum):
    """Resolved disposition for one existing GitHub-owned setup file."""

    CREATE = "create"
    KEEP = "keep"
    APPEND = "append"
    OVERWRITE = "overwrite"


class InitPlanValidationError(ValueError):
    """A request, preflight, and choice set cannot produce a safe init plan."""

    def __init__(self, message: str, *, code: str = "invalid-init-plan") -> None:
        super().__init__(message)
        self.code = code


_Value = TypeVar("_Value")


def _frozen_mapping(value: Mapping[str, _Value]) -> Mapping[str, _Value]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class InitRequest:
    """Explicit CLI inputs normalized before workspace inspection."""

    workspace: Path
    interaction_mode: InteractionMode = InteractionMode.NON_INTERACTIVE
    force: bool = False
    integrations: tuple[str, ...] = ()
    review_producers: tuple[str, ...] = ()
    review_models: Mapping[str, str] = field(default_factory=dict)
    review_flags_explicit: bool = False
    framework: str | None = None
    no_agent: bool = False
    setup_github: bool = False
    assume_yes: bool = False
    quiet: bool = False
    json_output: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(self, "integrations", tuple(self.integrations))
        object.__setattr__(self, "review_producers", tuple(self.review_producers))
        object.__setattr__(self, "review_models", _frozen_mapping(self.review_models))


@dataclass(frozen=True)
class InitPreflight:
    """Read-only workspace facts captured before choices are resolved."""

    harness_state: HarnessState
    existing_file_bytes: Mapping[str, bytes]
    available_integrations: frozenset[str]
    available_review_producers: frozenset[str]
    detected_integrations: tuple[str, ...]
    detected_review_producers: tuple[str, ...]
    persisted_review_producers: tuple[str, ...] = ()
    persisted_review_models: Mapping[str, str] = field(default_factory=dict)
    review_config_error: str | None = None
    github_available: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "existing_file_bytes", _frozen_mapping(self.existing_file_bytes))
        object.__setattr__(self, "available_integrations", frozenset(self.available_integrations))
        object.__setattr__(
            self,
            "available_review_producers",
            frozenset(self.available_review_producers),
        )
        object.__setattr__(self, "detected_integrations", tuple(self.detected_integrations))
        object.__setattr__(
            self,
            "detected_review_producers",
            tuple(self.detected_review_producers),
        )
        object.__setattr__(
            self,
            "persisted_review_producers",
            tuple(self.persisted_review_producers),
        )
        object.__setattr__(
            self,
            "persisted_review_models",
            _frozen_mapping(self.persisted_review_models),
        )


@dataclass(frozen=True)
class InitChoices:
    """Values selected by an outer line or guided interaction layer."""

    integrations: tuple[str, ...] | None = None
    review_write: ReviewWrite | None = None
    review_producers: tuple[str, ...] | None = None
    review_models: Mapping[str, str] = field(default_factory=dict)
    existing_files: Mapping[str, ExistingFileDecision] = field(default_factory=dict)
    github_decision: GitHubDecision | None = None
    github_file_decisions: Mapping[str, GithubFileDecision] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.integrations is not None:
            object.__setattr__(self, "integrations", tuple(self.integrations))
        if self.review_producers is not None:
            object.__setattr__(self, "review_producers", tuple(self.review_producers))
        object.__setattr__(self, "review_models", _frozen_mapping(self.review_models))
        object.__setattr__(self, "existing_files", _frozen_mapping(self.existing_files))
        object.__setattr__(
            self,
            "github_file_decisions",
            _frozen_mapping(self.github_file_decisions),
        )


@dataclass(frozen=True)
class PlannedFileAction:
    """One ordered, executor-neutral file operation."""

    path: Path
    action: FileAction
    content: bytes | None = None
    review_write: ReviewWrite | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True)
class InitPlan:
    """Fully validated immutable plan; applying it is intentionally out of scope."""

    harness_state: HarnessState
    review_write: ReviewWrite
    integrations: tuple[str, ...]
    review_producers: tuple[str, ...]
    review_models: Mapping[str, str]
    github_decision: GitHubDecision
    file_actions: tuple[PlannedFileAction, ...]
    github_file_decisions: Mapping[str, GithubFileDecision] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "integrations", tuple(self.integrations))
        object.__setattr__(self, "review_producers", tuple(self.review_producers))
        object.__setattr__(self, "review_models", _frozen_mapping(self.review_models))
        object.__setattr__(self, "file_actions", tuple(self.file_actions))
        object.__setattr__(
            self,
            "github_file_decisions",
            _frozen_mapping(self.github_file_decisions),
        )


@dataclass(frozen=True)
class _IntegrationDefinition:
    executable: str
    path: Path


@dataclass(frozen=True)
class _ReviewProducerDefinition:
    source: str
    executable: str
    agent_options: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_options", _frozen_mapping(self.agent_options))


_INTEGRATIONS: Mapping[str, _IntegrationDefinition] = MappingProxyType(
    {
        "codex": _IntegrationDefinition("codex", Path(".codex/hooks.json")),
        "claude-code": _IntegrationDefinition("claude", Path(".claude/settings.local.json")),
    }
)
_REVIEW_PRODUCERS: Mapping[str, _ReviewProducerDefinition] = MappingProxyType(
    {
        "codex-cli": _ReviewProducerDefinition(
            source="codex",
            executable="codex",
            agent_options={"reasoning_effort": "medium", "sandbox": "read-only"},
        ),
        "claude-cli": _ReviewProducerDefinition(
            source="claude",
            executable="claude",
            agent_options={"effort": "medium"},
        ),
    }
)
_SCAFFOLD_FILES: tuple[tuple[Path, bytes], ...] = (
    (Path(".harness/events.jsonl"), b""),
    (Path(".harness/state.yaml"), b"version: 1\n"),
    (Path(".harness/adapters.yaml"), b"version: 1\nadapters: []\n"),
)
_SKELETON_PATHS = (
    Path(".harness/sensors.yaml"),
    Path(".harness/gates.yaml"),
    Path(".harness/source-paths.yaml"),
    Path(".harness/derived-docs.yaml"),
    Path(".harness/verification.yaml"),
    Path(".harness/conventions.md"),
)
_REVIEW_PATHS = (
    Path(".harness/review-governance.yaml"),
    Path(".harness/review-profiles.local.yaml"),
)
_USER_FILES = (Path("AGENTS.md"), Path(".gitignore"))
_GITHUB_FILES: tuple[tuple[Path, bytes], ...] = (
    (
        Path(".github/workflows/super-harness.yml"),
        b"name: super-harness\non: [pull_request]\n",
    ),
    (
        Path(".github/pull_request_template.md"),
        b"<!-- super-harness metadata is managed automatically -->\n",
    ),
)
_ALL_OBSERVED_PATHS = (
    tuple(path for path, _ in _SCAFFOLD_FILES)
    + _SKELETON_PATHS
    + _REVIEW_PATHS
    + tuple(definition.path for definition in _INTEGRATIONS.values())
    + _USER_FILES
    + tuple(path for path, _ in _GITHUB_FILES)
)


def _inspect_persisted_review(root: Path) -> tuple[tuple[str, ...], Mapping[str, str]]:
    governance = load_review_governance(root)
    profiles = load_review_profiles(root)
    automated_sources = {
        source for source, item in governance.sources.items() if item.kind == "automated"
    }
    producers: list[str] = []
    models: dict[str, str] = {}
    for source, profile in profiles.sources.items():
        definition = _REVIEW_PRODUCERS.get(profile.protocol)
        if definition is None:
            raise ReviewProfilesError(
                f"unsupported persisted review producer protocol {profile.protocol!r}"
            )
        if definition.source != source:
            raise ReviewProfilesError(
                f"persisted producer {profile.protocol!r} does not match source {source!r}"
            )
        if source not in automated_sources:
            raise ReviewProfilesError(
                f"persisted profile source {source!r} is not automated in governance"
            )
        producers.append(profile.protocol)
        models[source] = profile.model
    missing_profiles = automated_sources.difference(models)
    if missing_profiles:
        source = sorted(missing_profiles)[0]
        raise ReviewProfilesError(f"automated source {source!r} has no persisted profile")
    return tuple(producers), MappingProxyType(models)


def inspect_workspace(
    request: InitRequest,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> InitPreflight:
    """Capture workspace state without creating, updating, or deleting anything."""

    root = request.workspace
    harness = root / ".harness"
    if not harness.exists():
        harness_state = HarnessState.ABSENT
    elif harness.is_dir() and (harness / "events.jsonl").is_file():
        harness_state = HarnessState.INITIALIZED
    else:
        harness_state = HarnessState.PARTIAL

    existing: dict[str, bytes] = {}
    for relative in _ALL_OBSERVED_PATHS:
        path = root / relative
        if path.is_file():
            existing[relative.as_posix()] = path.read_bytes()

    executable_paths = {
        executable: executable_lookup(executable) for executable in {"codex", "claude", "gh"}
    }
    available_integrations = frozenset(
        name
        for name, definition in _INTEGRATIONS.items()
        if executable_paths[definition.executable] is not None
    )
    available_producers = frozenset(
        name
        for name, definition in _REVIEW_PRODUCERS.items()
        if executable_paths[definition.executable] is not None
    )
    detected_integrations = tuple(name for name in _INTEGRATIONS if name in available_integrations)
    detected_producers = tuple(name for name in _REVIEW_PRODUCERS if name in available_producers)

    persisted_producers: tuple[str, ...] = ()
    persisted_models: Mapping[str, str] = MappingProxyType({})
    review_error: str | None = None
    should_parse_review = (
        request.interaction_mode is not InteractionMode.NON_INTERACTIVE
        and request.force
        and any(path.as_posix() in existing for path in _REVIEW_PATHS)
    )
    if should_parse_review:
        try:
            persisted_producers, persisted_models = _inspect_persisted_review(root)
        except (ReviewGovernanceError, ReviewProfilesError) as exc:
            review_error = str(exc)

    return InitPreflight(
        harness_state=harness_state,
        existing_file_bytes=existing,
        available_integrations=available_integrations,
        available_review_producers=available_producers,
        detected_integrations=detected_integrations,
        detected_review_producers=detected_producers,
        persisted_review_producers=persisted_producers,
        persisted_review_models=persisted_models,
        review_config_error=review_error,
        github_available=executable_paths["gh"] is not None,
    )


def _validate_known_unique(
    values: tuple[str, ...], known: Mapping[str, object], label: str
) -> None:
    if len(set(values)) != len(values):
        raise InitPlanValidationError(f"duplicate {label} selection")
    unknown = [value for value in values if value not in known]
    if unknown:
        raise InitPlanValidationError(f"unknown {label} {unknown[0]!r}")


def _resolve_review_write(
    request: InitRequest,
    preflight: InitPreflight,
    choices: InitChoices,
) -> ReviewWrite:
    if request.review_flags_explicit:
        return ReviewWrite.UPDATE
    if (
        request.interaction_mode is InteractionMode.NON_INTERACTIVE
        and request.force
        and any(path.as_posix() in preflight.existing_file_bytes for path in _REVIEW_PATHS)
    ):
        return ReviewWrite.PRESERVE
    if preflight.harness_state is HarnessState.ABSENT:
        return ReviewWrite.UPDATE
    if choices.review_write is not None:
        return choices.review_write
    return ReviewWrite.PRESERVE


def _resolve_integrations(
    request: InitRequest,
    preflight: InitPreflight,
    choices: InitChoices,
    review_write: ReviewWrite,
) -> tuple[str, ...]:
    if request.integrations:
        integrations = request.integrations
    elif choices.integrations is not None:
        integrations = choices.integrations
    elif request.interaction_mode is not InteractionMode.NON_INTERACTIVE and (
        preflight.harness_state is HarnessState.ABSENT or review_write is ReviewWrite.RESET
    ):
        integrations = preflight.detected_integrations
    else:
        integrations = ()
    _validate_known_unique(integrations, _INTEGRATIONS, "integration")
    return integrations


def _resolve_reviews(
    request: InitRequest,
    preflight: InitPreflight,
    choices: InitChoices,
    review_write: ReviewWrite,
) -> tuple[tuple[str, ...], Mapping[str, str]]:
    if review_write is ReviewWrite.PRESERVE:
        return preflight.persisted_review_producers, preflight.persisted_review_models

    noninteractive_explicit = (
        request.interaction_mode is InteractionMode.NON_INTERACTIVE
        and request.review_flags_explicit
    )
    if noninteractive_explicit:
        producers = request.review_producers
        models: dict[str, str] = dict(request.review_models)
    else:
        use_persisted = review_write is ReviewWrite.UPDATE and request.force
        if request.review_producers:
            producers = request.review_producers
        elif choices.review_producers is not None:
            producers = choices.review_producers
        elif use_persisted:
            producers = preflight.persisted_review_producers
        elif request.interaction_mode is not InteractionMode.NON_INTERACTIVE:
            producers = preflight.detected_review_producers
        else:
            producers = ()

        models = dict(preflight.persisted_review_models) if use_persisted else {}
        if request.review_producers or choices.review_producers is not None:
            selected_sources = {
                _REVIEW_PRODUCERS[producer].source
                for producer in producers
                if producer in _REVIEW_PRODUCERS
            }
            models = {
                source: model for source, model in models.items() if source in selected_sources
            }
        models.update(choices.review_models)
        models.update(request.review_models)

    _validate_known_unique(producers, _REVIEW_PRODUCERS, "review producer")
    for producer in producers:
        if producer not in preflight.available_review_producers:
            raise InitPlanValidationError(f"review producer {producer!r} is not available")

    sources = {_REVIEW_PRODUCERS[producer].source for producer in producers}
    for source, model in models.items():
        if not source or not isinstance(model, str) or not model:
            raise InitPlanValidationError("review models must be non-empty strings")
        if source not in sources:
            if producers:
                raise InitPlanValidationError(
                    f"review model source {source!r} does not match a selected producer"
                )
            raise InitPlanValidationError(
                f"review model source {source!r} has no selected producer"
            )
    for producer in producers:
        source = _REVIEW_PRODUCERS[producer].source
        if source not in models:
            raise InitPlanValidationError(
                f"review producer {producer!r} requires an explicit model for {source!r}"
            )
    return producers, MappingProxyType(models)


def _review_content(
    producers: tuple[str, ...], models: Mapping[str, str]
) -> tuple[bytes, bytes | None]:
    selected_sources = [_REVIEW_PRODUCERS[producer].source for producer in producers]
    governance_sources: dict[str, object] = {
        source: {"kind": "automated"} for source in selected_sources
    }
    governance_sources["human"] = {"kind": "human"}
    participants = selected_sources or ["human"]
    role = {
        "participants": participants,
        "min_independent": len(participants),
        "max_automatic_rounds_per_epoch": 2,
    }
    governance = {
        "version": 1,
        "review": {
            "base_branch": "main",
            "sources": governance_sources,
            "roles": {"plan-reviewer": dict(role), "code-reviewer": dict(role)},
            "require_distinct_model_families": False,
        },
    }
    profile_sources: dict[str, object] = {}
    for producer in producers:
        definition = _REVIEW_PRODUCERS[producer]
        profile_sources[definition.source] = {
            "protocol": producer,
            "model": models[definition.source],
            "cost_class": "standard",
            "agent_options": dict(definition.agent_options),
        }
    governance_bytes = yaml.safe_dump(governance, sort_keys=False).encode()
    if not profile_sources:
        return governance_bytes, None
    profile_bytes = yaml.safe_dump(
        {"version": 1, "sources": profile_sources}, sort_keys=False
    ).encode()
    return governance_bytes, profile_bytes


def _ordinary_action(
    relative: Path,
    content: bytes,
    preflight: InitPreflight,
) -> PlannedFileAction:
    exists = relative.as_posix() in preflight.existing_file_bytes
    action = FileAction.UPDATE if exists else FileAction.CREATE
    return PlannedFileAction(relative, action, content)


def _review_file_actions(
    preflight: InitPreflight,
    review_write: ReviewWrite,
    governance: bytes,
    profile: bytes | None,
) -> tuple[PlannedFileAction, PlannedFileAction]:
    if review_write is ReviewWrite.PRESERVE:
        actions: list[PlannedFileAction] = []
        for path in _REVIEW_PATHS:
            content = preflight.existing_file_bytes.get(path.as_posix())
            action = FileAction.PRESERVE if content is not None else FileAction.SKIP
            actions.append(PlannedFileAction(path, action, content, review_write))
        return actions[0], actions[1]

    governance_path, profile_path = _REVIEW_PATHS
    governance_action = (
        FileAction.UPDATE
        if governance_path.as_posix() in preflight.existing_file_bytes
        else FileAction.CREATE
    )
    if profile is None:
        profile_action = (
            FileAction.UPDATE
            if profile_path.as_posix() in preflight.existing_file_bytes
            else FileAction.SKIP
        )
    else:
        profile_action = (
            FileAction.UPDATE
            if profile_path.as_posix() in preflight.existing_file_bytes
            else FileAction.CREATE
        )
    return (
        PlannedFileAction(governance_path, governance_action, governance, review_write),
        PlannedFileAction(profile_path, profile_action, profile, review_write),
    )


def build_init_plan(
    request: InitRequest,
    preflight: InitPreflight,
    choices: InitChoices,
) -> InitPlan:
    """Validate captured inputs and return an ordered immutable file plan."""

    if preflight.harness_state is not HarnessState.ABSENT and not request.force:
        raise InitPlanValidationError(
            "workspace already has a harness directory; force is required",
            code="force-required",
        )

    review_write = _resolve_review_write(request, preflight, choices)
    if preflight.review_config_error is not None and review_write is not ReviewWrite.RESET:
        raise InitPlanValidationError(
            "persisted review configuration is invalid or unsupported; choose explicit RESET: "
            f"{preflight.review_config_error}",
            code="review-reset-required",
        )

    integrations = _resolve_integrations(request, preflight, choices, review_write)
    producers, models = _resolve_reviews(request, preflight, choices, review_write)
    governance, profile = _review_content(producers, models)

    if request.setup_github:
        github_decision = GitHubDecision.CREATE
    elif choices.github_decision is not None:
        github_decision = choices.github_decision
    else:
        github_decision = GitHubDecision.SKIP

    events_path, state_path, adapters_path = (path for path, _ in _SCAFFOLD_FILES)
    events_content = preflight.existing_file_bytes.get(events_path.as_posix())
    state_content = preflight.existing_file_bytes.get(state_path.as_posix())
    adapters_content = preflight.existing_file_bytes.get(adapters_path.as_posix())
    actions: list[PlannedFileAction] = [
        PlannedFileAction(
            events_path,
            FileAction.PRESERVE if events_content is not None else FileAction.CREATE,
            events_content if events_content is not None else b"",
        ),
        PlannedFileAction(
            state_path,
            FileAction.PRESERVE if state_content is not None else FileAction.SKIP,
            state_content,
        ),
        PlannedFileAction(
            adapters_path,
            (
                FileAction.UPDATE
                if integrations and adapters_content is not None
                else FileAction.CREATE
                if integrations
                else FileAction.PRESERVE
                if adapters_content is not None
                else FileAction.SKIP
            ),
            adapters_content,
        ),
    ]
    actions.extend(_ordinary_action(path, b"", preflight) for path in _SKELETON_PATHS)
    actions.extend(_review_file_actions(preflight, review_write, governance, profile))
    for name, definition in _INTEGRATIONS.items():
        if name in integrations:
            actions.append(_ordinary_action(definition.path, b"", preflight))
        else:
            actions.append(PlannedFileAction(definition.path, FileAction.SKIP))

    user_contents = {
        "AGENTS.md": b"<!-- super-harness managed section -->\n",
        ".gitignore": b".harness/review-profiles.local.yaml\n",
    }
    for path in _USER_FILES:
        existing = preflight.existing_file_bytes.get(path.as_posix())
        if existing is None:
            actions.append(PlannedFileAction(path, FileAction.CREATE, user_contents[path.name]))
            continue
        actions.append(PlannedFileAction(path, FileAction.UPDATE, existing))

    for path, content in _GITHUB_FILES:
        if github_decision is GitHubDecision.CREATE:
            actions.append(_ordinary_action(path, content, preflight))
        else:
            actions.append(PlannedFileAction(path, FileAction.SKIP))

    return InitPlan(
        harness_state=preflight.harness_state,
        review_write=review_write,
        integrations=integrations,
        review_producers=producers,
        review_models=models,
        github_decision=github_decision,
        file_actions=tuple(actions),
        github_file_decisions=choices.github_file_decisions,
    )
