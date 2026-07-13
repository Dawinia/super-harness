"""User-local reviewer producer profiles.

The file is intentionally gitignored: it selects locally installed producer
protocols and explicit model/options without turning machine choices into shared
repository governance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from super_harness.engineering.review_governance import ReviewGovernance


class ReviewProfilesError(ValueError):
    """The user-local review profile file is malformed or incomplete."""


@dataclass(frozen=True)
class ReviewProducerProfile:
    """Explicit producer selection for one governance source label."""

    source: str
    protocol: str
    model: str
    cost_class: Literal["standard", "expensive"]
    agent_options: dict[str, Any]


@dataclass(frozen=True)
class ReviewProfiles:
    """All locally configured producer profiles."""

    version: int
    sources: dict[str, ReviewProducerProfile]


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewProfilesError(f"{field} must be a mapping")
    return value


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewProfilesError(f"{field} must be a non-empty string")
    return value


def load_review_profiles(root: Path) -> ReviewProfiles:
    """Load the optional `.harness/review-profiles.local.yaml` file."""

    path = root / ".harness" / "review-profiles.local.yaml"
    if not path.is_file():
        return ReviewProfiles(version=1, sources={})
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ReviewProfilesError(f"{path} is not valid YAML: {exc}") from exc

    top = _mapping(raw, "review-profiles.local.yaml")
    version = top.get("version")
    if version != 1:
        raise ReviewProfilesError("review-profiles.local.yaml version must be 1")
    raw_sources = _mapping(top.get("sources"), "sources")

    sources: dict[str, ReviewProducerProfile] = {}
    for source, value in raw_sources.items():
        if not isinstance(source, str) or not source:
            raise ReviewProfilesError("sources keys must be non-empty strings")
        profile = _mapping(value, f"sources.{source}")
        protocol = _required_string(profile.get("protocol"), f"sources.{source}.protocol")
        model = _required_string(profile.get("model"), f"sources.{source}.model")
        cost_class = profile.get("cost_class", "standard")
        if cost_class not in {"standard", "expensive"}:
            raise ReviewProfilesError(
                f"sources.{source}.cost_class must be 'standard' or 'expensive'"
            )
        raw_options = profile.get("agent_options", {})
        options = _mapping(raw_options, f"sources.{source}.agent_options")
        if any(not isinstance(key, str) or not key for key in options):
            raise ReviewProfilesError(
                f"sources.{source}.agent_options keys must be non-empty strings"
            )
        sources[source] = ReviewProducerProfile(
            source=source,
            protocol=protocol,
            model=model,
            cost_class=cost_class,
            agent_options=dict(options),
        )

    return ReviewProfiles(version=version, sources=sources)


def resolve_role_profiles(
    governance: ReviewGovernance,
    profiles: ReviewProfiles,
    reviewer: str,
) -> dict[str, ReviewProducerProfile]:
    """Resolve explicit profiles for a role's automated participants.

    Human participants intentionally need no local producer profile.
    """

    role = governance.roles.get(reviewer)
    if role is None:
        raise ReviewProfilesError(f"review role {reviewer!r} is not configured")
    resolved: dict[str, ReviewProducerProfile] = {}
    for source in role.participants:
        source_governance = governance.sources[source]
        if source_governance.kind == "human":
            continue
        profile = profiles.sources.get(source)
        if profile is None:
            raise ReviewProfilesError(
                f"automated source {source!r} requires an explicit entry in "
                ".harness/review-profiles.local.yaml"
            )
        resolved[source] = profile
    return resolved
