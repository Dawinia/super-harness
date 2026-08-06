# tests/unit/core/test_review_bundle.py
"""Unit tests for core.review_bundle assembly."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from super_harness.core.review_bundle import (
    BundleError,
    assemble_bundle,
    load_base_branch,
)


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _change(ws: Path, declared: list[str], framework: str = "plain") -> str:
    """Seed a change in AWAITING_CODE_REVIEW with declared scope.files."""
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    seq = [
        ("intent_declared", {}),
        ("plan_ready", {"scope": {"files": declared}}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
    ]
    for t, payload in seq:
        EventWriter(events_path(ws)).emit(
            Event(
                event_id=new_event_id(), type=t, change_id="c",
                timestamp="2026-06-23T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework=framework, payload=payload,  # type: ignore[arg-type]
            )
        )
    refresh_state_after_emit(ws)
    return "c"


def _repo_with_change(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    (tmp_path / "other.py").write_text("o1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    (tmp_path / "other.py").write_text("o2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    return tmp_path


def test_assemble_bundle_happy(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["diff_in_scope"] == ["src/a.py"]
    assert b["out_of_scope"] == ["other.py"]
    assert b["checklist"] == [
        "spec-compliance", "scope-adherence", "code-quality", "edge-cases", "doc-impact",
    ]
    assert b["bundle_digest"]  # non-empty
    assert b["base"] == "main"


def test_assemble_bundle_rejects_dirty_in_scope_tree(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    (ws / "src" / "a.py").write_text("uncommitted\n")  # dirty in-scope file
    with pytest.raises(BundleError, match="commit"):
        assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")


def test_assemble_bundle_empty_scope_inert_digest(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, [])  # no declared scope
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["diff_in_scope"] == []
    # empty-scope digest is the constant empty-diff digest (freshness inert; documented)
    assert b["bundle_digest"]


def test_load_base_branch_default_and_override(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir(parents=True)
    assert load_base_branch(tmp_path) == "main"
    (tmp_path / ".harness" / "review-governance.yaml").write_text(
        "review:\n  base_branch: develop\n"
    )
    assert load_base_branch(tmp_path) == "develop"


def test_assemble_bundle_uses_injected_resolver(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])

    seen: dict[str, object] = {}

    def fake_resolver(framework: str | None, root: Path, change_id: str) -> tuple[str, str]:
        seen["framework"] = framework
        seen["change_id"] = change_id
        return "specs/proposal.md", "specs/tasks.md"

    b = assemble_bundle(
        ws, change_id="c", reviewer="code-reviewer", base="main",
        spec_plan_resolver=fake_resolver,
    )
    assert b["spec_path"] == "specs/proposal.md"
    assert b["plan_path"] == "specs/tasks.md"
    assert seen == {"framework": "plain", "change_id": "c"}


def test_assemble_bundle_no_resolver_yields_empty_spec_plan(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    _change(ws, ["src/"])
    b = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")
    assert b["spec_path"] == ""
    assert b["plan_path"] == ""


def test_assemble_bundle_finds_declared_plain_plan_frontmatter(tmp_path: Path) -> None:
    ws = _repo_with_change(tmp_path)
    (ws / "docs").mkdir()
    plan = ws / "docs" / "plan.md"
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Plan\n")
    _git(ws, "add", "docs/plan.md")
    _git(ws, "commit", "-qm", "plan")
    _change(ws, ["src/", "docs/plan.md"])

    bundle = assemble_bundle(ws, change_id="c", reviewer="code-reviewer", base="main")

    assert bundle["plan_path"] == "docs/plan.md"


def test_superpowers_change_gets_a_real_inspection_target(tmp_path: Path) -> None:
    """End-to-end: a superpowers-framework review must not be handed nothing.

    The regression this pins: `SuperpowersAdapter.spec_paths` reports absolute
    paths, `covered_by_scope` compares against repo-relative `git diff` output, so
    the assignment scope matched nothing, `diff_argv` came back `[]`, and the
    prompt told the reviewer its target was empty. A contract-compliant reviewer
    then approves a plan nobody read.

    The plan is committed on the FEATURE branch (as `_repo_with_change` already
    arranges). A plan committed on the base instead falls outside the range and
    yields an empty argv for a legitimate, different reason — the split-out
    defect — and this test would pass while proving nothing.
    """
    from super_harness.adapters.registry import resolve_spec_plan_paths
    from super_harness.engineering.review_contract import compile_review_contract
    from super_harness.engineering.review_governance import (
        ReviewerRoleGovernance,
        ReviewerSourceGovernance,
        ReviewGovernance,
    )
    from super_harness.engineering.review_profiles import ReviewProducerProfile

    ws = _repo_with_change(tmp_path)
    plans = ws / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "c-implementation.md").write_text(
        "---\nchange: c\nstage: plan\n---\n# Plan\n"
    )
    _git(ws, "add", "docs/plans/c-implementation.md")
    _git(ws, "commit", "-qm", "plan on the feature branch")
    _change(ws, ["src/", "docs/"], framework="superpowers")

    bundle = assemble_bundle(
        ws,
        change_id="c",
        reviewer="plan-reviewer",
        base="main",
        spec_plan_resolver=resolve_spec_plan_paths,
    )
    assert bundle["plan_path"] == "docs/plans/c-implementation.md"

    governance = ReviewGovernance(
        version=1,
        base_branch="main",
        sources={"external": ReviewerSourceGovernance(name="external", kind="automated")},
        roles={
            "plan-reviewer": ReviewerRoleGovernance(
                reviewer="plan-reviewer",
                participants=("external",),
                min_independent=1,
                max_automatic_rounds=2,
            )
        },
        require_distinct_model_families=False,
    )
    compiled = compile_review_contract(
        ws,
        bundle=dict(bundle),
        governance=governance,
        profiles={
            "external": ReviewProducerProfile(
                source="external", protocol="codex-cli", model="m",
                cost_class="standard", agent_options={},
            )
        },
        events=[],
        declared=["src/", "docs/"],
    )

    argv = compiled["assignments"][0]["inspection"]["diff_argv"]
    assert argv, "the reviewer was handed an empty target"
    assert "docs/plans/c-implementation.md" in argv
