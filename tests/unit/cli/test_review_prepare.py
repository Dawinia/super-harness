"""Unit tests for `super-harness review prepare`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.paths import pending_reviews_dir
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _seed_change(ws: Path, declared: list[str], framework: str = "plain") -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    for t, p in [("intent_declared", {}), ("plan_ready", {"scope": {"files": declared}}),
                 ("plan_approved", {}), ("implementation_started", {}),
                 ("verification_passed", {}),
                 ("implementation_complete", {})]:
        EventWriter(events_path(ws)).emit(Event(
            event_id=new_event_id(), type=t, change_id="c",
            timestamp="2026-06-23T00:00:00Z",
            actor=Actor(type="human", identifier="cli"), framework=framework, payload=p))
    refresh_state_after_emit(ws)


def _set_reviewer_source_policy(ws: Path) -> None:
    (ws / ".harness" / "policy.yaml").write_text(
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
        "      instructions: Run Codex against the prepared review bundle only.\n"
        "      agent_options:\n"
        "        reasoning_effort: medium\n"
        "        sandbox: read-only\n"
        "  code-reviewer:\n"
        "    strategy: subagent\n"
        "    min_independent: 2\n"
    )


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    return tmp_path


def test_prepare_writes_bundle(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_OK, r.output
    out = json.loads(r.output)
    assert out["status"] == "pass"
    bundle_path = pending_reviews_dir(ws, "c") / "code-reviewer.bundle.json"
    assert bundle_path.is_file()
    bundle = json.loads(bundle_path.read_text())
    assert bundle["diff_in_scope"] == ["src/a.py"]
    assert bundle["bundle_digest"]


def test_prepare_embeds_reviewer_source_policy_hints(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    _set_reviewer_source_policy(ws)

    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])

    assert r.exit_code == EXIT_OK, r.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "code-reviewer.bundle.json").read_text()
    )
    assert bundle["review_policy"] == {
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
                "instructions": "Run Codex against the prepared review bundle only.",
                "agent": "codex",
                "context": "bundle-only",
                "agent_options": {"reasoning_effort": "medium", "sandbox": "read-only"},
            },
        },
    }


def test_prepare_dirty_tree_errors(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    (ws / "src" / "a.py").write_text("dirty\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "commit" in r.output.lower()


def test_prepare_wires_resolver_for_openspec(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"], framework="openspec")
    r = CliRunner().invoke(
        main,
        ["--json", "--workspace", str(ws), "review", "prepare", "c",
         "--reviewer", "code-reviewer", "--base", "main"],
    )
    assert r.exit_code == 0, r.output
    bundle = json.loads(
        (ws / ".harness" / "pending-reviews" / "c" / "code-reviewer.bundle.json").read_text()
    )
    assert bundle["spec_path"].endswith("openspec/changes/c/proposal.md")
    assert bundle["plan_path"].endswith("openspec/changes/c/tasks.md")
