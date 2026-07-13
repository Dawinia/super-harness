"""CLI contracts for prepare/begin without reviewer execution."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

from click.testing import CliRunner
from pytest import MonkeyPatch

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.review_verdict import read_change_events
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )


def _repo(root: Path, *, cost_class: str = "standard") -> Path:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "review@example.test")
    _git(root, "config", "user.name", "Reviewer")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature")
    (root / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(root, "commit", "-aqm", "implementation")

    harness = root / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        "    codex:\n"
        "      kind: automated\n"
        "    human:\n"
        "      kind: human\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [human]\n"
        "      min_independent: 1\n"
        "    code-reviewer:\n"
        "      participants: [codex]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds_per_epoch: 2\n",
        encoding="utf-8",
    )
    (harness / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  codex:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        f"    cost_class: {cost_class}\n"
        "    agent_options:\n"
        "      reasoning_effort: medium\n"
        "      sandbox: read-only\n",
        encoding="utf-8",
    )
    for event_type, payload in [
        ("intent_declared", {}),
        ("plan_ready", {"scope": {"files": ["src/"]}}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
    ]:
        EventWriter(events_path(root)).emit(
            Event(
                event_id=new_event_id(),
                type=event_type,
                change_id="change",
                timestamp="2026-07-13T00:00:00Z",
                actor=Actor(type="human", identifier="test"),
                framework="plain",
                payload=payload,
            )
        )
    refresh_state_after_emit(root)
    return root


def _fake_codex(root: Path, monkeypatch: MonkeyPatch) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "codex"
    executable.write_text(
        "#!/bin/sh\ntouch \"$0.executed\"\nexit 99\n", encoding="utf-8"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return executable


def _enable_claude(root: Path) -> None:
    harness = root / ".harness"
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        "    codex:\n"
        "      kind: automated\n"
        "    claude:\n"
        "      kind: automated\n"
        "    human:\n"
        "      kind: human\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [human]\n"
        "      min_independent: 1\n"
        "    code-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 2\n",
        encoding="utf-8",
    )
    (harness / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  codex:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        "    agent_options:\n"
        "      reasoning_effort: medium\n"
        "      sandbox: read-only\n"
        "  claude:\n"
        "    protocol: claude-cli\n"
        "    model: claude-review\n"
        "    agent_options:\n"
        "      effort: medium\n",
        encoding="utf-8",
    )


def _fake_claude(root: Path, monkeypatch: MonkeyPatch) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "claude"
    executable.write_text(
        "#!/bin/sh\ntouch \"$0.executed\"\nexit 99\n", encoding="utf-8"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return executable


def _prepare(root: Path) -> dict[str, object]:
    result = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "prepare",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    parsed = json.loads(result.output)
    return cast(dict[str, object], parsed["data"])


def _begin(root: Path) -> dict[str, object]:
    result = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "begin",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    parsed = json.loads(result.output)
    return cast(dict[str, object], parsed["data"])


def _result_for_run(begin_data: dict[str, object]) -> dict[str, object]:
    runs = cast(list[dict[str, object]], begin_data["runs"])
    run = runs[0]
    contract = json.loads(Path(cast(str, begin_data["contract_path"])).read_text())
    packet = contract["packet"]
    return {
        "run_id": run["run_id"],
        "source": run["source"],
        "target_head": begin_data["target_head"],
        "contract_digest": begin_data["contract_digest"],
        "bundle_digest": packet["bundle_digest"],
        "scope_sufficient": True,
        "checklist": [
            {"item": item, "status": "pass"} for item in packet["checklist"]
        ],
        "findings": [],
        "prior_findings": [],
    }


def _result_for_source(
    begin_data: dict[str, object], source: str
) -> tuple[dict[str, object], dict[str, object]]:
    runs = cast(list[dict[str, object]], begin_data["runs"])
    run = next(item for item in runs if item["source"] == source)
    contract = json.loads(Path(cast(str, begin_data["contract_path"])).read_text())
    packet = contract["packet"]
    verdict: dict[str, object] = {
        "run_id": run["run_id"],
        "source": source,
        "target_head": begin_data["target_head"],
        "contract_digest": begin_data["contract_digest"],
        "bundle_digest": packet["bundle_digest"],
        "scope_sufficient": True,
        "checklist": [
            {"item": item, "status": "pass"} for item in packet["checklist"]
        ],
        "findings": [],
        "prior_findings": [],
    }
    return run, verdict


def test_begin_freezes_invocation_without_executing_producer(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    executable = _fake_codex(root, monkeypatch)
    prepared = _prepare(root)

    result = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "begin",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    data = json.loads(result.output)["data"]
    assert data["contract_digest"] == prepared["contract_digest"]
    assert data["runs"][0]["argv"][:4] == [
        str(executable),
        "exec",
        "--ephemeral",
        "--model",
    ]
    assert data["runs"][0]["requested_model"] == "gpt-review"
    assert not executable.with_name("codex.executed").exists()
    assert Path(data["runs"][0]["invocation_path"]).is_file()
    events = read_change_events(events_path(root), "change")
    starts = [event for event in events if event.type == "review_round_started"]
    assert len(starts) == 1
    assert starts[0].payload["automatic"] is True


def test_begin_rejects_stale_prepared_packet(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    (root / "src" / "app.py").write_text("value = 3\n", encoding="utf-8")
    _git(root, "commit", "-am", "change after prepare")

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "begin",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )

    assert result.exit_code == EXIT_VALIDATION
    assert "prepared review packet is stale" in result.output
    assert not any(
        event.type == "review_round_started"
        for event in read_change_events(events_path(root), "change")
    )


def test_expensive_profile_requires_one_shot_authorization(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path, cost_class="expensive")
    _fake_codex(root, monkeypatch)
    _prepare(root)

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "begin",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )

    assert result.exit_code == EXIT_VALIDATION
    assert "one-shot human authorization" in result.output
    assert not any(
        event.type == "review_round_started"
        for event in read_change_events(events_path(root), "change")
    )


def test_interactive_authorization_is_bound_and_consumed_once(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path, cost_class="expensive")
    _fake_codex(root, monkeypatch)
    _prepare(root)
    monkeypatch.setattr("super_harness.cli.review._interactive_terminal", lambda: True)

    authorized = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "authorize",
            "change",
            "--reviewer",
            "code-reviewer",
            "--reason",
            "Human selected the expensive profile for this round",
        ],
        input="y\n",
    )

    assert authorized.exit_code == EXIT_OK, authorized.output
    authorization = next(
        event
        for event in read_change_events(events_path(root), "change")
        if event.type == "review_round_authorized"
    )
    authorization_id = authorization.event_id
    begun = _begin(root)
    events = read_change_events(events_path(root), "change")
    start = next(event for event in events if event.type == "review_round_started")
    assert start.payload["authorization_id"] == authorization_id
    assert begun["round_id"] == start.payload["round_id"]


def test_import_records_receipt_closes_round_and_emits_milestone(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    verdict = _result_for_run(begun)
    result_path = root / "codex-result.json"
    result_path.write_text(json.dumps(verdict), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "result",
            "import",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, verdict["run_id"]),
            "--result-file",
            str(result_path),
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    data = json.loads(result.output)["data"]
    assert data["round_outcome"] == "approved"
    assert data["milestone"] == "code_review_passed"
    assert data["new_state"] == "READY_TO_MERGE"
    events = read_change_events(events_path(root), "change")
    imported = [event for event in events if event.type == "review_result_imported"]
    assert len(imported) == 1
    assert imported[0].payload["receipt"]["requested_model"] == "gpt-review"
    assert imported[0].payload["receipt"]["usage"] is None
    assert [event.type for event in events[-3:]] == [
        "review_result_imported",
        "review_round_closed",
        "code_review_passed",
    ]

    duplicate = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "result",
            "import",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, verdict["run_id"]),
            "--result-file",
            str(result_path),
        ],
    )
    assert duplicate.exit_code == EXIT_OK, duplicate.output
    assert json.loads(duplicate.output)["data"]["idempotent"] is True
    assert len(
        [
            event
            for event in read_change_events(events_path(root), "change")
            if event.type == "review_result_imported"
        ]
    ) == 1


def test_run_failure_closes_execution_failed_and_retry_uses_new_ids(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    first = _begin(root)
    first_run = cast(list[dict[str, object]], first["runs"])[0]

    failed = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "run",
            "fail",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, first_run["run_id"]),
            "--reason",
            "producer exited 1",
        ],
    )

    assert failed.exit_code == EXIT_OK, failed.output
    assert json.loads(failed.output)["data"]["round_outcome"] == "execution_failed"
    second = _begin(root)
    second_run = cast(list[dict[str, object]], second["runs"])[0]
    assert second["round_id"] != first["round_id"]
    assert second_run["run_id"] != first_run["run_id"]
    assert second["automatic_rounds_used"] == 2


def test_blocker_waits_for_every_source_then_rejects_with_namespaced_finding(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _enable_claude(root)
    _fake_codex(root, monkeypatch)
    _fake_claude(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    codex_run, codex_verdict = _result_for_source(begun, "codex")
    checklist = cast(list[dict[str, object]], codex_verdict["checklist"])
    checklist[0]["status"] = "fail"
    codex_verdict["findings"] = [
        {
            "id": "B-1",
            "severity": "blocker",
            "file": "src/app.py",
            "summary": "The changed value violates the contract.",
        }
    ]
    codex_path = root / "codex-result.json"
    codex_path.write_text(json.dumps(codex_verdict), encoding="utf-8")

    first = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "result",
            "import",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, codex_run["run_id"]),
            "--result-file",
            str(codex_path),
        ],
    )

    assert first.exit_code == EXIT_OK, first.output
    assert json.loads(first.output)["data"]["round_outcome"] is None
    assert not any(
        event.type == "code_review_failed"
        for event in read_change_events(events_path(root), "change")
    )

    claude_run, claude_verdict = _result_for_source(begun, "claude")
    claude_path = root / "claude-result.json"
    claude_path.write_text(
        json.dumps(
            {
                "structured_output": claude_verdict,
                "modelUsage": {"claude-review": {"inputTokens": 100}},
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "duration_ms": 1234,
            }
        ),
        encoding="utf-8",
    )
    second = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "result",
            "import",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, claude_run["run_id"]),
            "--result-file",
            str(claude_path),
        ],
    )

    assert second.exit_code == EXIT_OK, second.output
    assert json.loads(second.output)["data"]["round_outcome"] == "rejected"
    events = read_change_events(events_path(root), "change")
    rejection = next(event for event in events if event.type == "code_review_failed")
    findings = rejection.payload["verdict"]["findings"]
    assert findings[0]["id"] == f"codex/{codex_run['run_id']}/B-1"
    assert rejection.payload["independent_sources"] == ["claude", "codex"]
