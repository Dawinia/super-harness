from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.engineering.review_governance import (
    ReviewerRoleGovernance,
    ReviewerSourceGovernance,
    ReviewGovernance,
)
from super_harness.engineering.review_profiles import (
    ReviewProducerProfile,
    ReviewProfiles,
    ReviewProfilesError,
    load_review_profiles,
    resolve_role_profiles,
)


def test_loads_user_local_review_profiles(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  codex:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        "    cost_class: standard\n"
        "    agent_options:\n"
        "      reasoning_effort: medium\n"
        "      sandbox: read-only\n",
        encoding="utf-8",
    )

    assert load_review_profiles(tmp_path) == ReviewProfiles(
        version=1,
        sources={
            "codex": ReviewProducerProfile(
                source="codex",
                protocol="codex-cli",
                model="gpt-review",
                cost_class="standard",
                agent_options={
                    "reasoning_effort": "medium",
                    "sandbox": "read-only",
                },
            )
        },
    )


def test_automated_participant_requires_explicit_local_profile() -> None:
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={
            "codex": ReviewerSourceGovernance(name="codex", kind="automated"),
            "human": ReviewerSourceGovernance(name="human", kind="human"),
        },
        roles={
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("codex",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )
        },
        require_distinct_model_families=False,
    )

    with pytest.raises(
        ReviewProfilesError, match=r"codex.*review-profiles\.local\.yaml"
    ):
        resolve_role_profiles(
            governance,
            ReviewProfiles(version=1, sources={}),
            "code-reviewer",
        )


def test_human_only_role_needs_no_local_producer_profile() -> None:
    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={
            "human": ReviewerSourceGovernance(name="human", kind="human"),
        },
        roles={
            "plan-reviewer": ReviewerRoleGovernance(
                reviewer="plan-reviewer",
                participants=("human",),
                min_independent=1,
                max_automatic_rounds_per_epoch=2,
            )
        },
        require_distinct_model_families=False,
    )

    assert resolve_role_profiles(
        governance,
        ReviewProfiles(version=1, sources={}),
        "plan-reviewer",
    ) == {}
