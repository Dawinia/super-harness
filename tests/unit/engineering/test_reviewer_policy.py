"""Unit tests for reviewer-strategy policy (HG-02.C).

`load_reviewer_strategy` reads `.harness/policy.yaml` → `reviewers.<name>.strategy`,
defaulting to "subagent" so a user only sets it when they need human review (e.g.
under a token budget). The harness does not enforce the strategy — it tells the
agent whether to dispatch a Task subagent or hand off to a human.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.engineering.reviewer_policy import (
    REVIEW_STATE_REVIEWER,
    ReviewerIndependencePolicy,
    ReviewerPolicyError,
    ReviewerSourcePolicy,
    load_reviewer_policy,
    load_reviewer_strategy,
    reviewer_policy_payload,
)


def _write_policy(root: Path, body: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "policy.yaml").write_text(body)


def test_default_subagent_when_no_file(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "subagent"


def test_default_subagent_when_no_reviewers_block(tmp_path: Path) -> None:
    _write_policy(tmp_path, "gates:\n  pre_tool_use: {}\n")
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "subagent"


def test_reads_configured_human_strategy(tmp_path: Path) -> None:
    _write_policy(tmp_path, "reviewers:\n  plan-reviewer:\n    strategy: human\n")
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "human"


def test_per_reviewer_independent(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n  plan-reviewer:\n    strategy: human\n"
        "  code-reviewer:\n    strategy: hybrid\n",
    )
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "human"
    assert load_reviewer_strategy(tmp_path, "code-reviewer") == "hybrid"


def test_bad_strategy_raises(tmp_path: Path) -> None:
    _write_policy(tmp_path, "reviewers:\n  plan-reviewer:\n    strategy: robot\n")
    with pytest.raises(ReviewerPolicyError):
        load_reviewer_strategy(tmp_path, "plan-reviewer")


def test_state_to_reviewer_mapping() -> None:
    assert REVIEW_STATE_REVIEWER["AWAITING_PLAN_REVIEW"] == "plan-reviewer"
    assert REVIEW_STATE_REVIEWER["AWAITING_CODE_REVIEW"] == "code-reviewer"


# --- Multi-independent reviewer-source policy ------------------------------ #


def test_default_reviewer_policy_preserves_single_review(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    assert load_reviewer_policy(tmp_path, "plan-reviewer") == ReviewerIndependencePolicy(
        reviewer="plan-reviewer",
        strategy="subagent",
        min_independent=1,
        allowed_sources=(),
        source_instructions={},
        source_profiles={},
    )


def test_reads_min_independent_and_source_mapping(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    subagent: {}\n"
        "    external:\n"
        "      instructions: Run an external reviewer.\n"
        "  plan-reviewer:\n"
        "    strategy: hybrid\n"
        "    min_independent: 2\n",
    )
    policy = load_reviewer_policy(tmp_path, "plan-reviewer")
    assert policy.strategy == "hybrid"
    assert policy.min_independent == 2
    assert policy.allowed_sources == ("subagent", "external")
    assert policy.source_instructions["external"] == "Run an external reviewer."
    assert "independent subagent" in policy.source_instructions["subagent"]


def test_reads_agent_specific_source_profile(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    external:\n"
        "      agent: codex\n"
        "      context: bundle-only\n"
        "      instructions: Run codex against the prepared bundle only.\n"
        "      agent_options:\n"
        "        reasoning_effort: medium\n"
        "        sandbox: read-only\n"
        "  code-reviewer:\n"
        "    min_independent: 1\n",
    )

    policy = load_reviewer_policy(tmp_path, "code-reviewer")

    assert policy.source_profiles["external"] == ReviewerSourcePolicy(
        instructions="Run codex against the prepared bundle only.",
        agent="codex",
        context="bundle-only",
        agent_options={"reasoning_effort": "medium", "sandbox": "read-only"},
    )


def test_policy_payload_preserves_agent_specific_source_options(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    subagent:\n"
        "      agent: task-subagent\n"
        "      context: incremental\n"
        "      agent_options:\n"
        "        effort: medium\n"
        "    external:\n"
        "      agent: codex\n"
        "      context: bundle-only\n"
        "      agent_options:\n"
        "        reasoning_effort: medium\n"
        "        sandbox: read-only\n"
        "  code-reviewer:\n"
        "    strategy: subagent\n"
        "    min_independent: 2\n",
    )
    policy = load_reviewer_policy(tmp_path, "code-reviewer")

    assert reviewer_policy_payload(policy) == {
        "reviewer": "code-reviewer",
        "strategy": "subagent",
        "min_independent": 2,
        "allowed_sources": ["subagent", "external"],
        "source_profiles": {
            "subagent": {
                "instructions": "Dispatch an independent subagent reviewer and record its verdict.",
                "agent": "task-subagent",
                "context": "incremental",
                "agent_options": {"effort": "medium"},
            },
            "external": {
                "instructions": "Run an external reviewer and record its verdict.",
                "agent": "codex",
                "context": "bundle-only",
                "agent_options": {"reasoning_effort": "medium", "sandbox": "read-only"},
            },
        },
    }


def test_source_agent_options_require_agent(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    external:\n"
        "      agent_options:\n"
        "        reasoning_effort: medium\n",
    )

    with pytest.raises(ReviewerPolicyError, match="agent"):
        load_reviewer_policy(tmp_path, "code-reviewer")


def test_source_rejects_agent_agnostic_effort_or_mode(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    external:\n"
        "      effort: medium\n"
        "      mode: read-only\n",
    )

    with pytest.raises(ReviewerPolicyError, match="agent_options"):
        load_reviewer_policy(tmp_path, "code-reviewer")


def test_accepts_source_list_shorthand(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources: [subagent, external]\n"
        "  code-reviewer:\n"
        "    min_independent: 2\n",
    )
    policy = load_reviewer_policy(tmp_path, "code-reviewer")
    assert policy.allowed_sources == ("subagent", "external")


def test_duplicate_source_list_entries_raise(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources: [subagent, subagent]\n"
        "  plan-reviewer:\n"
        "    min_independent: 2\n",
    )

    with pytest.raises(ReviewerPolicyError, match="duplicate reviewer source"):
        load_reviewer_policy(tmp_path, "plan-reviewer")


def test_duplicate_source_mapping_keys_raise(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    subagent: {}\n"
        "    subagent:\n"
        "      instructions: overwritten\n"
        "    external: {}\n"
        "  plan-reviewer:\n"
        "    min_independent: 2\n",
    )

    with pytest.raises(ReviewerPolicyError, match="duplicate YAML key"):
        load_reviewer_policy(tmp_path, "plan-reviewer")


def test_min_independent_requires_enough_configured_sources(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources: [subagent]\n"
        "  plan-reviewer:\n"
        "    min_independent: 2\n",
    )
    with pytest.raises(ReviewerPolicyError, match="at least 2"):
        load_reviewer_policy(tmp_path, "plan-reviewer")


def test_bad_min_independent_raises(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  plan-reviewer:\n"
        "    min_independent: 0\n",
    )
    with pytest.raises(ReviewerPolicyError, match="min_independent"):
        load_reviewer_policy(tmp_path, "plan-reviewer")


def test_bad_source_shape_raises(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n"
        "  sources:\n"
        "    external:\n"
        "      instructions: [not, a, string]\n",
    )
    with pytest.raises(ReviewerPolicyError, match="instructions"):
        load_reviewer_policy(tmp_path, "plan-reviewer")
