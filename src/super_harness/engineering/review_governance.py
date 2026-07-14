"""Tracked, agent-neutral review governance configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class ReviewGovernanceError(ValueError):
    """The tracked review governance file is missing or malformed."""


class DuplicateYamlKeyError(ValueError):
    """A YAML document declares the same mapping key twice.

    ``yaml.safe_load`` silently keeps the last of two duplicate keys, so a
    hand edit or merge-conflict resolution that leaves two ``roles:`` (or two
    ``code-reviewer:``) blocks would silently weaken review requirements with no
    error anywhere. Both the tracked governance loader and the user-local
    profile loader reject duplicates up front instead.
    """


def reject_duplicate_yaml_keys(text: str, *, label: str) -> None:
    """Raise ``DuplicateYamlKeyError`` if any mapping in ``text`` repeats a key.

    A YAML syntax error is left for the caller's ``yaml.safe_load`` to surface
    uniformly (this pass only guards against the silent last-wins hazard).
    """

    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return
    if root is None:
        return

    def visit(node: object) -> None:
        if isinstance(node, MappingNode):
            seen: set[tuple[str, str]] = set()
            for key_node, value_node in node.value:
                if isinstance(key_node, ScalarNode):
                    key = (key_node.tag, key_node.value)
                    if key in seen:
                        raise DuplicateYamlKeyError(
                            f"duplicate YAML key in {label}: {key_node.value!r}"
                        )
                    seen.add(key)
                visit(value_node)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                visit(item)

    visit(root)


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
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewGovernanceError(f"{path} is not readable: {exc}") from exc
    try:
        reject_duplicate_yaml_keys(text, label="review-governance.yaml")
    except DuplicateYamlKeyError as exc:
        raise ReviewGovernanceError(str(exc)) from exc
    try:
        raw: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
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
