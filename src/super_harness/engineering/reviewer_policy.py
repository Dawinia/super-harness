"""Reviewer-strategy policy (HG-02.C).

Reads `.harness/policy.yaml` → `reviewers.<name>.strategy`. The strategy tells the
agent how to produce a review verdict (the harness never runs the review itself):

- `subagent` (default) — dispatch a reviewer subagent (the agent's own Task tool).
- `human`             — hand off to a human, who records `review approve|reject`.
- `hybrid`            — subagent first, escalate to a human on a fail / Large tier.

A user sets `human` when a token budget rules out subagent review for everything.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from super_harness.core.events import Event

# Lifecycle review state → the reviewer responsible for it (SSOT).
REVIEW_STATE_REVIEWER: dict[str, str] = {
    "AWAITING_PLAN_REVIEW": "plan-reviewer",
    "AWAITING_CODE_REVIEW": "code-reviewer",
}
REVIEW_WINDOW_BOUNDARIES: dict[str, frozenset[str]] = {
    "plan-reviewer": frozenset({"plan_ready"}),
    "code-reviewer": frozenset({"implementation_complete", "code_review_failed"}),
}

_STRATEGIES = ("subagent", "human", "hybrid")
_DEFAULT_STRATEGY = "subagent"
_DEFAULT_MIN_INDEPENDENT = 1
_BUILTIN_SOURCE_INSTRUCTIONS: dict[str, str] = {
    "subagent": "Dispatch an independent subagent reviewer and record its verdict.",
    "external": "Run an external reviewer and record its verdict.",
    "human": "Ask a human reviewer to inspect the change and record the verdict.",
}


class ReviewerPolicyError(ValueError):
    """`.harness/policy.yaml` is present but its reviewers block is malformed."""


@dataclass(frozen=True)
class ReviewerIndependencePolicy:
    """Resolved policy for one lifecycle reviewer role."""

    reviewer: str
    strategy: str
    min_independent: int
    allowed_sources: tuple[str, ...]
    source_instructions: dict[str, str]


def _load_policy_yaml(root: Path) -> dict[str, Any]:
    policy_path = root / ".harness" / "policy.yaml"
    if not policy_path.is_file():
        return {}
    try:
        parsed: Any = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ReviewerPolicyError(f"policy.yaml is not valid YAML: {e}") from e
    return parsed if isinstance(parsed, dict) else {}


def _append_source(sources: list[str], source: str) -> None:
    if source in sources:
        raise ReviewerPolicyError(f"duplicate reviewer source: {source!r}")
    sources.append(source)


def _resolve_sources(raw: object) -> tuple[tuple[str, ...], dict[str, str]]:
    if raw is None:
        return (), {}
    if isinstance(raw, list):
        sources: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item:
                raise ReviewerPolicyError("reviewers.sources must contain non-empty strings")
            _append_source(sources, item)
        instructions = {
            src: _BUILTIN_SOURCE_INSTRUCTIONS[src]
            for src in sources
            if src in _BUILTIN_SOURCE_INSTRUCTIONS
        }
        return tuple(sources), instructions
    if isinstance(raw, dict):
        sources = []
        mapped_instructions: dict[str, str] = {}
        for source, cfg in raw.items():
            if not isinstance(source, str) or not source:
                raise ReviewerPolicyError("reviewers.sources keys must be non-empty strings")
            _append_source(sources, source)
            if cfg is None:
                cfg = {}
            if not isinstance(cfg, dict):
                raise ReviewerPolicyError(f"reviewers.sources.{source} must be a mapping")
            instr = cfg.get("instructions")
            if instr is not None:
                if not isinstance(instr, str):
                    raise ReviewerPolicyError(
                        f"reviewers.sources.{source}.instructions must be a string"
                )
                if instr:
                    mapped_instructions[source] = instr
            elif source in _BUILTIN_SOURCE_INSTRUCTIONS:
                mapped_instructions[source] = _BUILTIN_SOURCE_INSTRUCTIONS[source]
        return tuple(sources), mapped_instructions
    raise ReviewerPolicyError("reviewers.sources must be a list or mapping")


def load_reviewer_policy(root: Path, reviewer: str) -> ReviewerIndependencePolicy:
    """Return resolved review policy for `reviewer`.

    Tolerant of an absent policy.yaml / absent `reviewers` block / absent reviewer
    (→ defaults). Raises `ReviewerPolicyError` on malformed configured policy.
    """
    parsed = _load_policy_yaml(root)
    reviewers = parsed.get("reviewers")
    if not isinstance(reviewers, dict):
        return ReviewerIndependencePolicy(
            reviewer=reviewer,
            strategy=_DEFAULT_STRATEGY,
            min_independent=_DEFAULT_MIN_INDEPENDENT,
            allowed_sources=(),
            source_instructions={},
        )
    allowed_sources, source_instructions = _resolve_sources(reviewers.get("sources"))
    block = reviewers.get(reviewer)
    if not isinstance(block, dict):
        block = {}
    strategy = block.get("strategy", _DEFAULT_STRATEGY)
    if strategy not in _STRATEGIES:
        raise ReviewerPolicyError(
            f"reviewers.{reviewer}.strategy={strategy!r} is not one of {list(_STRATEGIES)}"
        )
    min_independent = block.get("min_independent", _DEFAULT_MIN_INDEPENDENT)
    if (
        not isinstance(min_independent, int)
        or isinstance(min_independent, bool)
        or min_independent < 1
    ):
        raise ReviewerPolicyError(f"reviewers.{reviewer}.min_independent must be an integer >= 1")
    if min_independent > 1 and len(allowed_sources) < min_independent:
        raise ReviewerPolicyError(
            f"reviewers.{reviewer}.min_independent requires at least "
            f"{min_independent} configured reviewer sources"
        )
    return ReviewerIndependencePolicy(
        reviewer=reviewer,
        strategy=str(strategy),
        min_independent=min_independent,
        allowed_sources=allowed_sources,
        source_instructions=source_instructions,
    )


def load_reviewer_strategy(root: Path, reviewer: str) -> str:
    """Return the configured strategy for `reviewer` (default `subagent`)."""
    return load_reviewer_policy(root, reviewer).strategy


def approved_review_sources(
    events: list[Event], reviewer: str, *, bundle_digest: str | None = None,
) -> list[str]:
    """Return distinct approving source labels in the current review attempt.

    Append order is causal truth. A new plan/code review attempt starts after the
    latest reviewer-specific boundary event, so stale partial approvals from an
    earlier rejected attempt do not count toward the current threshold.
    """
    boundaries = REVIEW_WINDOW_BOUNDARIES[reviewer]
    start = 0
    for idx, ev in enumerate(events):
        if ev.type in boundaries:
            start = idx + 1
    seen: set[str] = set()
    sources: list[str] = []
    for ev in events[start:]:
        if ev.type != "review_verdict_recorded":
            continue
        payload = ev.payload or {}
        if payload.get("reviewer") != reviewer or payload.get("outcome") != "approved":
            continue
        if bundle_digest is not None:
            verdict = payload.get("verdict")
            if not isinstance(verdict, dict) or verdict.get("bundle_digest") != bundle_digest:
                continue
        source = payload.get("source")
        if isinstance(source, str) and source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources
