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
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

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
_SOURCE_CONTEXTS = ("bundle-only", "incremental", "full-change")
_BUILTIN_SOURCE_INSTRUCTIONS: dict[str, str] = {
    "subagent": "Dispatch an independent subagent reviewer and record its verdict.",
    "external": "Run an external reviewer and record its verdict.",
    "human": "Ask a human reviewer to inspect the change and record the verdict.",
}


class ReviewerPolicyError(ValueError):
    """`.harness/policy.yaml` is present but its reviewers block is malformed."""


@dataclass(frozen=True)
class ReviewerSourcePolicy:
    """Resolved execution/context hints for one configured reviewer source.

    These are instructions for the actor that runs the reviewer. super-harness
    validates and surfaces them, but it still never spawns the reviewer itself.
    ``agent_options`` is intentionally agent-specific: Codex, Claude Code, human
    review, and other runners do not share one universal effort/mode vocabulary.
    """

    instructions: str
    agent: str | None
    context: str | None
    agent_options: dict[str, Any]


@dataclass(frozen=True)
class ReviewerIndependencePolicy:
    """Resolved policy for one lifecycle reviewer role."""

    reviewer: str
    strategy: str
    min_independent: int
    allowed_sources: tuple[str, ...]
    source_instructions: dict[str, str]
    source_profiles: dict[str, ReviewerSourcePolicy]
    participants: tuple[str, ...] = ()


def _source_policy_payload(profile: ReviewerSourcePolicy) -> dict[str, Any]:
    return {
        "instructions": profile.instructions,
        "agent": profile.agent,
        "context": profile.context,
        "agent_options": dict(profile.agent_options),
    }


def reviewer_policy_payload(policy: ReviewerIndependencePolicy) -> dict[str, Any]:
    """Return the JSON/YAML-safe reviewer-source policy payload.

    This shape is embedded in review bundles and status JSON. It deliberately
    keeps runner knobs under each source's ``agent_options`` instead of
    inventing one cross-agent effort/mode vocabulary.
    """

    return {
        "reviewer": policy.reviewer,
        "strategy": policy.strategy,
        "min_independent": policy.min_independent,
        "allowed_sources": list(policy.allowed_sources),
        "participants": list(policy.participants),
        "source_profiles": {
            source: _source_policy_payload(profile)
            for source, profile in policy.source_profiles.items()
        },
    }


def _load_policy_yaml(root: Path) -> dict[str, Any]:
    policy_path = root / ".harness" / "policy.yaml"
    if not policy_path.is_file():
        return {}
    text = policy_path.read_text(encoding="utf-8")
    _reject_duplicate_yaml_keys(text)
    try:
        parsed: Any = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ReviewerPolicyError(f"policy.yaml is not valid YAML: {e}") from e
    return parsed if isinstance(parsed, dict) else {}


def _reject_duplicate_yaml_keys(text: str) -> None:
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as e:
        raise ReviewerPolicyError(f"policy.yaml is not valid YAML: {e}") from e
    if root is None:
        return

    def visit(node: object) -> None:
        if isinstance(node, MappingNode):
            seen: set[tuple[str, str]] = set()
            for key_node, value_node in node.value:
                if isinstance(key_node, ScalarNode):
                    key = (key_node.tag, key_node.value)
                    if key in seen:
                        raise ReviewerPolicyError(
                            f"duplicate YAML key in policy.yaml: {key_node.value!r}"
                        )
                    seen.add(key)
                visit(value_node)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                visit(item)

    visit(root)


def _append_source(sources: list[str], source: str) -> None:
    if source in sources:
        raise ReviewerPolicyError(f"duplicate reviewer source: {source!r}")
    sources.append(source)


def _resolve_sources(
    raw: object,
) -> tuple[tuple[str, ...], dict[str, str], dict[str, ReviewerSourcePolicy]]:
    if raw is None:
        return (), {}, {}
    if isinstance(raw, list):
        sources: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item:
                raise ReviewerPolicyError("reviewers.sources must contain non-empty strings")
            _append_source(sources, item)
        list_instructions = {
            src: _BUILTIN_SOURCE_INSTRUCTIONS[src]
            for src in sources
            if src in _BUILTIN_SOURCE_INSTRUCTIONS
        }
        list_profiles = {
            src: ReviewerSourcePolicy(
                instructions=text,
                agent=None,
                context=None,
                agent_options={},
            )
            for src, text in list_instructions.items()
        }
        return tuple(sources), list_instructions, list_profiles
    if isinstance(raw, dict):
        sources = []
        mapped_instructions: dict[str, str] = {}
        profiles: dict[str, ReviewerSourcePolicy] = {}
        for source, cfg in raw.items():
            if not isinstance(source, str) or not source:
                raise ReviewerPolicyError("reviewers.sources keys must be non-empty strings")
            _append_source(sources, source)
            if cfg is None:
                cfg = {}
            if not isinstance(cfg, dict):
                raise ReviewerPolicyError(f"reviewers.sources.{source} must be a mapping")
            if "effort" in cfg or "mode" in cfg:
                raise ReviewerPolicyError(
                    f"reviewers.sources.{source}: effort/mode are agent-specific; "
                    "put them under agent_options with an explicit agent"
                )
            agent = cfg.get("agent")
            if agent is not None and (not isinstance(agent, str) or not agent):
                raise ReviewerPolicyError(f"reviewers.sources.{source}.agent must be a string")
            context = cfg.get("context")
            if context is not None:
                if not isinstance(context, str) or context not in _SOURCE_CONTEXTS:
                    raise ReviewerPolicyError(
                        f"reviewers.sources.{source}.context must be one of "
                        f"{list(_SOURCE_CONTEXTS)}"
                    )
            agent_options = cfg.get("agent_options")
            if agent_options is None:
                resolved_options: dict[str, Any] = {}
            else:
                if agent is None:
                    raise ReviewerPolicyError(
                        f"reviewers.sources.{source}.agent_options requires an explicit agent"
                    )
                if not isinstance(agent_options, dict):
                    raise ReviewerPolicyError(
                        f"reviewers.sources.{source}.agent_options must be a mapping"
                    )
                resolved_options = {}
                for key, value in agent_options.items():
                    if not isinstance(key, str) or not key:
                        raise ReviewerPolicyError(
                            f"reviewers.sources.{source}.agent_options keys must be strings"
                        )
                    resolved_options[key] = value
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
            source_instruction = mapped_instructions.get(source, "")
            profiles[source] = ReviewerSourcePolicy(
                instructions=source_instruction,
                agent=agent,
                context=context,
                agent_options=resolved_options,
            )
        return tuple(sources), mapped_instructions, profiles
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
            source_profiles={},
        )
    allowed_sources, source_instructions, source_profiles = _resolve_sources(
        reviewers.get("sources")
    )
    block = reviewers.get(reviewer)
    if not isinstance(block, dict):
        block = {}
    strategy = block.get("strategy", _DEFAULT_STRATEGY)
    if strategy not in _STRATEGIES:
        raise ReviewerPolicyError(
            f"reviewers.{reviewer}.strategy={strategy!r} is not one of {list(_STRATEGIES)}"
        )
    raw_participants = block.get("participants")
    if isinstance(raw_participants, list):
        if any(not isinstance(source, str) or not source for source in raw_participants):
            raise ReviewerPolicyError(
                f"reviewers.{reviewer}.participants must contain non-empty strings"
            )
        participants = tuple(raw_participants)
        if len(set(participants)) != len(participants):
            raise ReviewerPolicyError(
                f"reviewers.{reviewer}.participants has duplicate participant"
            )
        unknown = [source for source in participants if source not in allowed_sources]
        if unknown:
            raise ReviewerPolicyError(
                f"reviewers.{reviewer}.participants has unknown participant: {unknown[0]!r}"
            )
    elif raw_participants is None:
        participants = ()
    else:
        raise ReviewerPolicyError(f"reviewers.{reviewer}.participants must be a list")
    default_threshold = len(participants) if participants else _DEFAULT_MIN_INDEPENDENT
    min_independent = block.get("min_independent", default_threshold)
    if (
        not isinstance(min_independent, int)
        or isinstance(min_independent, bool)
        or min_independent < 1
    ):
        raise ReviewerPolicyError(f"reviewers.{reviewer}.min_independent must be an integer >= 1")
    if participants and "min_independent" in block and min_independent != len(participants):
        raise ReviewerPolicyError(
            f"reviewers.{reviewer}.min_independent must match participants count "
            f"({len(participants)})"
        )
    if min_independent > 1 and len(allowed_sources) < min_independent:
        raise ReviewerPolicyError(
            f"reviewers.{reviewer}.min_independent requires at least "
            f"{min_independent} configured reviewer sources"
        )
    if not participants and allowed_sources:
        if len(allowed_sources) > min_independent:
            raise ReviewerPolicyError(
                f"reviewers.{reviewer} source selection is ambiguous; configure participants"
            )
        if len(allowed_sources) == min_independent:
            participants = allowed_sources
    return ReviewerIndependencePolicy(
        reviewer=reviewer,
        strategy=str(strategy),
        min_independent=min_independent,
        allowed_sources=allowed_sources,
        source_instructions=source_instructions,
        source_profiles=source_profiles,
        participants=participants,
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
