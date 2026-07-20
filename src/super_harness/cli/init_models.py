from __future__ import annotations

import json
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.10 CI job
    import tomli as tomllib  # type: ignore[import-not-found]


_SUPPORTED_SOURCES = frozenset({"codex", "claude"})


@dataclass(frozen=True)
class ReviewerModelCandidate:
    source: str
    model: str
    origin: str
    precedence: int


@dataclass(frozen=True)
class ReviewerModelDiscovery:
    candidates: Mapping[str, tuple[ReviewerModelCandidate, ...]] = field(
        default_factory=dict
    )
    errors: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidates",
            MappingProxyType(
                {source: tuple(values) for source, values in self.candidates.items()}
            ),
        )
        object.__setattr__(self, "errors", MappingProxyType(dict(self.errors)))


def _model(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _read_codex_models(path: Path) -> tuple[tuple[str, str, int], ...]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    values: list[tuple[str, str, int]] = []
    active = _model(data.get("model"))
    if active is not None:
        values.append((active, "Codex CLI config", 10))
    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        for name in sorted(profiles):
            profile = profiles[name]
            if not isinstance(profile, dict):
                continue
            configured = _model(profile.get("model"))
            if configured is not None:
                values.append((configured, f"Codex CLI profile {name}", 20))
    return tuple(values)


def _read_claude_models(path: Path) -> tuple[tuple[str, str, int], ...]:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return ()
    active = _model(data.get("model"))
    return () if active is None else ((active, "Claude CLI config", 10),)


def _append_candidate(
    values: list[ReviewerModelCandidate],
    *,
    source: str,
    model: str,
    origin: str,
    precedence: int,
) -> None:
    if any(candidate.model == model for candidate in values):
        return
    values.append(ReviewerModelCandidate(source, model, origin, precedence))


def discover_reviewer_models(
    *,
    home: Path,
    persisted_models: Mapping[str, str],
    sources: Collection[str] | None = None,
) -> ReviewerModelDiscovery:
    """Read configured model identifiers without retaining raw provider config."""

    selected = _SUPPORTED_SOURCES if sources is None else _SUPPORTED_SOURCES.intersection(sources)
    candidates: dict[str, tuple[ReviewerModelCandidate, ...]] = {}
    errors: dict[str, str] = {}

    for source in sorted(selected):
        values: list[ReviewerModelCandidate] = []
        persisted = _model(persisted_models.get(source))
        if persisted is not None:
            _append_candidate(
                values,
                source=source,
                model=persisted,
                origin="existing workspace profile",
                precedence=0,
            )

        path = (
            home / ".codex" / "config.toml"
            if source == "codex"
            else home / ".claude" / "settings.json"
        )
        if path.exists():
            try:
                configured = (
                    _read_codex_models(path)
                    if source == "codex"
                    else _read_claude_models(path)
                )
            except (OSError, UnicodeDecodeError):
                errors[source] = (
                    "Codex CLI config could not be read"
                    if source == "codex"
                    else "Claude CLI config could not be read"
                )
            except tomllib.TOMLDecodeError:
                errors[source] = "Codex CLI config is not valid TOML"
            except json.JSONDecodeError:
                errors[source] = "Claude CLI config is not valid JSON"
            else:
                for model, origin, precedence in configured:
                    _append_candidate(
                        values,
                        source=source,
                        model=model,
                        origin=origin,
                        precedence=precedence,
                    )

        if values:
            candidates[source] = tuple(values)

    return ReviewerModelDiscovery(candidates=candidates, errors=errors)
