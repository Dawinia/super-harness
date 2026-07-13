"""Tracked, agent-neutral review governance configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


class ReviewGovernanceError(ValueError):
    """The tracked review governance file is missing or malformed."""


@dataclass(frozen=True)
class ReviewerSourceGovernance:
    """One evidence-provenance label used for independence checks."""

    name: str
    kind: Literal["automated", "human"]


@dataclass(frozen=True)
class ReviewerRoleGovernance:
    """Shared requirements for one lifecycle reviewer role."""

    reviewer: str
    participants: tuple[str, ...]
    min_independent: int
    max_automatic_rounds_per_epoch: int


@dataclass(frozen=True)
class ReviewGovernance:
    """Resolved tracked review governance for a workspace."""

    version: int
    base_branch: str
    sources: dict[str, ReviewerSourceGovernance]
    roles: dict[str, ReviewerRoleGovernance]
    require_distinct_model_families: bool


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewGovernanceError(f"{field} must be a mapping")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ReviewGovernanceError(f"{field} must be an integer >= 1")
    return value


def load_review_governance(root: Path) -> ReviewGovernance:
    """Load `.harness/review-governance.yaml` from ``root``."""

    path = root / ".harness" / "review-governance.yaml"
    if not path.is_file():
        legacy = root / ".harness" / "policy.yaml"
        if legacy.is_file():
            raise ReviewGovernanceError(
                "legacy .harness/policy.yaml does not define the review execution "
                "protocol; replace it with tracked .harness/review-governance.yaml "
                "and a user-local review profile"
            )
        raise ReviewGovernanceError(
            f"{path} not found; run `super-harness init` or add tracked review governance"
        )
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ReviewGovernanceError(f"{path} is not valid YAML: {exc}") from exc

    top = _mapping(raw, "review-governance.yaml")
    version = top.get("version")
    if version != 1:
        raise ReviewGovernanceError("review-governance.yaml version must be 1")
    review = _mapping(top.get("review"), "review")

    base_branch = review.get("base_branch", "main")
    if not isinstance(base_branch, str) or not base_branch:
        raise ReviewGovernanceError("review.base_branch must be a non-empty string")

    raw_sources = _mapping(review.get("sources"), "review.sources")
    sources: dict[str, ReviewerSourceGovernance] = {}
    for name, value in raw_sources.items():
        if not isinstance(name, str) or not name:
            raise ReviewGovernanceError("review.sources keys must be non-empty strings")
        source = _mapping(value, f"review.sources.{name}")
        kind = source.get("kind")
        if kind not in {"automated", "human"}:
            raise ReviewGovernanceError(
                f"review.sources.{name}.kind must be 'automated' or 'human'"
            )
        sources[name] = ReviewerSourceGovernance(name=name, kind=kind)

    raw_roles = _mapping(review.get("roles"), "review.roles")
    roles: dict[str, ReviewerRoleGovernance] = {}
    for reviewer, value in raw_roles.items():
        if not isinstance(reviewer, str) or not reviewer:
            raise ReviewGovernanceError("review.roles keys must be non-empty strings")
        role = _mapping(value, f"review.roles.{reviewer}")
        raw_participants = role.get("participants")
        if not isinstance(raw_participants, list) or any(
            not isinstance(item, str) or not item for item in raw_participants
        ):
            raise ReviewGovernanceError(
                f"review.roles.{reviewer}.participants must be a list of non-empty strings"
            )
        participants = tuple(raw_participants)
        if len(set(participants)) != len(participants):
            raise ReviewGovernanceError(
                f"review.roles.{reviewer} has duplicate participant"
            )
        unknown = [source for source in participants if source not in sources]
        if unknown:
            raise ReviewGovernanceError(
                f"review.roles.{reviewer} has unknown participant {unknown[0]!r}"
            )
        min_independent = _positive_int(
            role.get("min_independent", len(participants)),
            f"review.roles.{reviewer}.min_independent",
        )
        if min_independent != len(participants):
            raise ReviewGovernanceError(
                f"review.roles.{reviewer}.min_independent must match participants "
                f"count ({len(participants)})"
            )
        max_rounds = _positive_int(
            role.get("max_automatic_rounds_per_epoch", 2),
            f"review.roles.{reviewer}.max_automatic_rounds_per_epoch",
        )
        roles[reviewer] = ReviewerRoleGovernance(
            reviewer=reviewer,
            participants=participants,
            min_independent=min_independent,
            max_automatic_rounds_per_epoch=max_rounds,
        )

    require_distinct = review.get("require_distinct_model_families", False)
    if not isinstance(require_distinct, bool):
        raise ReviewGovernanceError(
            "review.require_distinct_model_families must be a boolean"
        )

    return ReviewGovernance(
        version=version,
        base_branch=base_branch,
        sources=sources,
        roles=roles,
        require_distinct_model_families=require_distinct,
    )
