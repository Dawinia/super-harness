"""CLI contracts for prepare/begin without reviewer execution."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

from click.testing import CliRunner, Result
from pytest import MonkeyPatch

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path, pending_reviews_dir
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
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
        "      min_independent: 2\n"
        "      max_automatic_rounds_per_epoch: 3\n",
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
    assert data["runs"][0]["capture_stdout"] is True
    assert data["runs"][0]["telemetry_path"].endswith("/events.jsonl")
    assert data["runs"][0]["stdout_path"] == data["runs"][0]["telemetry_path"]
    assert not executable.with_name("codex.executed").exists()
    assert Path(data["runs"][0]["invocation_path"]).is_file()
    events = read_change_events(events_path(root), "change")
    starts = [event for event in events if event.type == "review_round_started"]
    assert len(starts) == 1
    assert starts[0].payload["automatic"] is True


def test_import_records_frozen_codex_jsonl_telemetry(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    run = cast(list[dict[str, object]], begun["runs"])[0]
    telemetry_path = Path(cast(str, run["telemetry_path"]))
    telemetry_path.write_text(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"thread-import",'
                '"model":"gpt-review"}',
                '{"type":"item.completed","item":{"id":"item-1",'
                '"type":"command_execution","command":"git diff",'
                '"aggregated_output":"large command output",'
                '"exit_code":0,"status":"completed"}}',
                '{"type":"turn.completed","duration_ms":321,"usage":'
                '{"input_tokens":50,"cached_input_tokens":20,'
                '"output_tokens":5}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result_path = root / "codex-result.json"
    result_path.write_text(json.dumps(_result_for_run(begun)), encoding="utf-8")

    imported = CliRunner().invoke(
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
            cast(str, run["run_id"]),
            "--result-file",
            str(result_path),
        ],
    )

    assert imported.exit_code == EXIT_OK, imported.output
    data = json.loads(imported.output)["data"]
    assert data["actual_model"] == "gpt-review"
    assert data["session_id"] == "thread-import"
    assert data["usage_available"] is True
    receipt = next(
        event.payload["receipt"]
        for event in read_change_events(events_path(root), "change")
        if event.type == "review_result_imported"
    )
    assert receipt["session_id"] == "thread-import"
    assert receipt["usage"] == {
        "input_tokens": 50,
        "cached_input_tokens": 20,
        "output_tokens": 5,
    }
    assert receipt["duration_ms"] == 321
    assert receipt["tool_trace"] == [
        {
            "id": "item-1",
            "type": "command_execution",
            "command": "git diff",
            "exit_code": 0,
            "status": "completed",
        }
    ]


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


def test_import_rejects_result_when_head_changed_after_round_began(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    verdict = _result_for_run(begun)
    result_path = root / "codex-result.json"
    result_path.write_text(json.dumps(verdict), encoding="utf-8")
    (root / "src" / "app.py").write_text("value = 3\n", encoding="utf-8")
    _git(root, "commit", "-am", "change after review began")

    result = CliRunner().invoke(
        main,
        [
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

    assert result.exit_code == EXIT_VALIDATION
    assert "current HEAD no longer matches the frozen review target" in result.output
    assert not any(
        event.type == "review_result_imported"
        for event in read_change_events(events_path(root), "change")
    )


def test_round_closure_uses_frozen_governance_after_live_policy_changes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _enable_claude(root)
    _fake_codex(root, monkeypatch)
    _fake_claude(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    codex_run, codex_verdict = _result_for_source(begun, "codex")
    claude_run, _ = _result_for_source(begun, "claude")
    codex_path = root / "codex-result.json"
    codex_path.write_text(json.dumps(codex_verdict), encoding="utf-8")

    (root / ".harness" / "review-governance.yaml").write_text(
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
        "      min_independent: 1\n",
        encoding="utf-8",
    )
    failed = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "run",
            "fail",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, claude_run["run_id"]),
            "--reason",
            "producer unavailable",
        ],
    )
    assert failed.exit_code == EXIT_OK, failed.output

    imported = CliRunner().invoke(
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

    assert imported.exit_code == EXIT_OK, imported.output
    data = json.loads(imported.output)["data"]
    assert data["round_outcome"] == "execution_failed"
    assert data["milestone"] is None
    assert data["new_state"] == "AWAITING_CODE_REVIEW"


def test_rejecting_result_wins_over_peer_execution_failure(
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
            "summary": "The reviewed implementation is unsafe.",
        }
    ]
    codex_path = root / "codex-result.json"
    codex_path.write_text(json.dumps(codex_verdict), encoding="utf-8")
    imported = CliRunner().invoke(
        main,
        [
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
    assert imported.exit_code == EXIT_OK, imported.output
    claude_run, _ = _result_for_source(begun, "claude")

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
            cast(str, claude_run["run_id"]),
            "--reason",
            "producer unavailable",
        ],
    )

    assert failed.exit_code == EXIT_OK, failed.output
    data = json.loads(failed.output)["data"]
    assert data["round_outcome"] == "rejected"
    assert data["milestone"] == "code_review_failed"
    events = read_change_events(events_path(root), "change")
    assert events[-1].type == "code_review_failed"
    assert events[-1].payload["missing_sources"] == ["claude"]


def test_stale_head_prevents_rejection_milestone_when_peer_execution_fails(
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
            "summary": "The reviewed implementation is unsafe.",
        }
    ]
    codex_path = root / "codex-result.json"
    codex_path.write_text(json.dumps(codex_verdict), encoding="utf-8")
    imported = CliRunner().invoke(
        main,
        [
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
    assert imported.exit_code == EXIT_OK, imported.output

    (root / "src" / "app.py").write_text("value = 3\n", encoding="utf-8")
    _git(root, "commit", "-aqm", "move review target")
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    claude_run, _ = _result_for_source(begun, "claude")

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
            cast(str, claude_run["run_id"]),
            "--reason",
            "producer unavailable",
        ],
    )

    assert failed.exit_code == EXIT_OK, failed.output
    data = json.loads(failed.output)["data"]
    assert data["round_outcome"] == "execution_failed"
    assert data["milestone"] is None
    events = read_change_events(events_path(root), "change")
    assert not any(event.type == "code_review_failed" for event in events)
    assert events[-1].type == "review_round_closed"
    assert events[-1].payload["current_head"] == current_head
    assert events[-1].payload["target_stale"] is True


def test_legacy_round_without_frozen_governance_cannot_approve(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    verdict = _result_for_run(begun)
    result_path = root / "codex-result.json"
    result_path.write_text(json.dumps(verdict), encoding="utf-8")

    log_path = events_path(root)
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    started = next(record for record in records if record["type"] == "review_round_started")
    payload = cast(dict[str, object], started["payload"])
    payload.pop("required_sources")
    payload.pop("min_independent")
    payload.pop("require_distinct_model_families")
    log_path.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
        + "\n",
        encoding="utf-8",
    )

    imported = CliRunner().invoke(
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

    assert imported.exit_code == EXIT_OK, imported.output
    data = json.loads(imported.output)["data"]
    assert data["round_outcome"] == "execution_failed"
    assert data["milestone"] is None
    assert data["new_state"] == "AWAITING_CODE_REVIEW"
    events = read_change_events(events_path(root), "change")
    assert events[-1].type == "review_round_closed"
    assert events[-1].payload["frozen_governance_complete"] is False


def test_failed_source_retry_reuses_original_round_prior_findings(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _enable_claude(root)
    _fake_codex(root, monkeypatch)
    _fake_claude(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    codex_run, codex_verdict = _result_for_source(begun, "codex")
    codex_verdict["findings"] = [
        {
            "id": "peer-note",
            "severity": "minor",
            "file": "src/app.py",
            "summary": "A retained peer observation is not prior retry context.",
        }
    ]
    codex_path = root / "codex-result.json"
    codex_path.write_text(json.dumps(codex_verdict), encoding="utf-8")
    imported = CliRunner().invoke(
        main,
        [
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
    assert imported.exit_code == EXIT_OK, imported.output
    claude_run, _ = _result_for_source(begun, "claude")
    failed = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "run",
            "fail",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, claude_run["run_id"]),
            "--reason",
            "producer unavailable",
        ],
    )
    assert failed.exit_code == EXIT_OK, failed.output

    retried = _begin(root)

    assert [run["source"] for run in cast(list[dict[str, object]], retried["runs"])] == [
        "claude"
    ]
    second_run = cast(list[dict[str, object]], retried["runs"])[0]
    failed_again = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "run",
            "fail",
            "change",
            "--reviewer",
            "code-reviewer",
            "--run-id",
            cast(str, second_run["run_id"]),
            "--reason",
            "producer still unavailable",
        ],
    )
    assert failed_again.exit_code == EXIT_OK, failed_again.output

    retried_again = _begin(root)

    assert [
        run["source"]
        for run in cast(list[dict[str, object]], retried_again["runs"])
    ] == ["claude"]
    starts = [
        event
        for event in read_change_events(events_path(root), "change")
        if event.type == "review_round_started"
    ]
    assert starts[0].payload["open_finding_ids"] == []
    assert starts[1].payload["open_finding_ids"] == []
    assert starts[2].payload["open_finding_ids"] == []


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


def _codex_telemetry(model: str) -> str:
    started = json.dumps(
        {"type": "thread.started", "thread_id": "thread-x", "model": model}
    )
    completed = json.dumps(
        {
            "type": "turn.completed",
            "duration_ms": 10,
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
            },
        }
    )
    return f"{started}\n{completed}\n"


def _import_run(
    root: Path, begun: dict[str, object], *, model: str
) -> Result:
    run = cast(list[dict[str, object]], begun["runs"])[0]
    telemetry_path = Path(cast(str, run["telemetry_path"]))
    telemetry_path.write_text(_codex_telemetry(model), encoding="utf-8")
    result_path = root / "codex-result.json"
    result_path.write_text(json.dumps(_result_for_run(begun)), encoding="utf-8")
    return CliRunner().invoke(
        main,
        [
            "--json", "--workspace", str(root), "review", "result", "import",
            "change", "--reviewer", "code-reviewer",
            "--run-id", cast(str, run["run_id"]), "--result-file", str(result_path),
        ],
    )


def test_import_accepts_dated_model_variant(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Regression (PR#79 finding #6): a producer reporting a more-specific dated
    id for the requested model is honored, not a contradiction."""
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    imported = _import_run(root, begun, model="gpt-review-20260101")
    assert imported.exit_code == EXIT_OK, imported.output


def test_import_rejects_contradictory_model(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Regression (PR#79 finding #6): a genuinely disjoint reported model is still
    rejected as a contradiction."""
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    imported = _import_run(root, begun, model="sonnet-other")
    assert imported.exit_code == EXIT_VALIDATION
    assert "contradicts" in imported.output


def test_import_rejects_dirty_in_scope_tree(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Regression (PR#79 finding #2): uncommitted in-scope edits made after begin
    must block code-review import — the reviewer only saw the committed diff."""
    root = _repo(tmp_path)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    # Dirty an in-scope file without committing (HEAD still matches target).
    (root / "src" / "app.py").write_text("value = 999\n", encoding="utf-8")
    imported = _import_run(root, begun, model="gpt-review")
    assert imported.exit_code == EXIT_VALIDATION
    assert "uncommitted changes" in imported.output


def test_retry_contract_events_slices_identically_for_begin_and_authorize() -> None:
    """Regression (PR#79 finding #7): the shared retry-slicing helper both begin
    and authorize call must slice the event log before a closed execution_failed
    round so both resolve the SAME contract digest (no stale-packet disagreement)."""
    from super_harness.cli.review import _retry_contract_events
    from super_harness.engineering.review_runs import derive_review_execution

    def ev(event_type: str, payload: dict[str, object], eid: str) -> Event:
        return Event(
            event_id=eid, type=event_type, change_id="change",
            timestamp="2026-07-13T00:00:00Z",
            actor=Actor(type="agent", identifier="t"), framework="plain",
            payload=payload,
        )

    epoch = "impl-1"
    common = {
        "reviewer": "code-reviewer", "epoch_id": epoch,
        "contract_digest": "cd", "target_head": "th", "profile_digest": "pd",
    }
    run = {
        "run_id": "run-1", "source": "codex", "protocol": "codex-cli",
        "requested_model": "m", "requested_options": {},
    }
    events = [
        ev("implementation_complete", {}, epoch),
        ev("review_round_started", {**common, "round_id": "r1", "automatic": True,
            "required_sources": ["codex"], "min_independent": 1,
            "require_distinct_model_families": False, "runs": [run]}, "s1"),
        ev("review_run_failed", {**common, "round_id": "r1", "run_id": "run-1",
            "source": "codex", "reason": "crash"}, "f1"),
        ev("review_round_closed", {**common, "round_id": "r1",
            "outcome": "execution_failed"}, "c1"),
    ]
    execution = derive_review_execution(events, "code-reviewer")
    packet = {"contract_digest": "cd", "target_head": "th", "profile_digest": "pd"}

    contract_events, anchor, retrying = _retry_contract_events(
        events, execution, packet, "code-reviewer"
    )
    assert retrying is True
    assert anchor is not None and anchor.round_id == "r1"
    # Sliced to everything BEFORE the failed round's review_round_started.
    assert [e.event_id for e in contract_events] == ["impl-1"]


def test_plan_reviewer_round_freezes_no_code_finding_ids(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Regression (PR#79 finding #3): a plan-reviewer round must NOT freeze
    open code-review finding ids (the plan prompt never surfaces them, so import
    could never dispose them and plan review would wedge)."""
    root = tmp_path
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "docs").mkdir()
    (root / "src").mkdir()
    (root / "docs" / "plan.md").write_text(
        "---\nchange: change\nstage: plan\n---\n\nplan\n", encoding="utf-8"
    )
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature")
    (root / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(root, "commit", "-aqm", "impl")

    harness = root / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        "    codex:\n"
        "      kind: automated\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [codex]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds_per_epoch: 2\n"
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
        "    agent_options:\n"
        "      reasoning_effort: medium\n"
        "      sandbox: read-only\n",
        encoding="utf-8",
    )
    _fake_codex(root, monkeypatch)
    scope = {"scope": {"files": ["docs/", "src/"]}}
    seq: list[tuple[str, dict[str, object]]] = [
        ("intent_declared", {}),
        ("plan_ready", scope),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
        ("code_review_failed", {
            "reviewer": "code-reviewer",
            "verdict": {"findings": [{"id": "codex/r0/F1", "severity": "major"}],
                        "prior_findings": []},
        }),
        ("plan_redeclared", {"reason": "revise"}),
        ("plan_ready", scope),
    ]
    for event_type, payload in seq:
        EventWriter(events_path(root)).emit(
            Event(
                event_id=new_event_id(), type=event_type, change_id="change",
                timestamp="2026-07-13T00:00:00Z",
                actor=Actor(type="human", identifier="test"),
                framework="plain", payload=payload,
            )
        )
    refresh_state_after_emit(root)

    # Sanity: the code finding is genuinely open (would be frozen without the fix).
    from super_harness.core.review_verdict import derive_open_findings
    assert derive_open_findings(
        read_change_events(events_path(root), "change"), "change"
    ) == ["codex/r0/F1"]

    for verb in ("prepare", "begin"):
        result = CliRunner().invoke(
            main,
            ["--json", "--workspace", str(root), "review", verb, "change",
             "--reviewer", "plan-reviewer"],
        )
        assert result.exit_code == EXIT_OK, result.output

    started = [
        e for e in read_change_events(events_path(root), "change")
        if e.type == "review_round_started" and e.payload.get("reviewer") == "plan-reviewer"
    ]
    assert started, "plan-reviewer round should have started"
    assert started[-1].payload["open_finding_ids"] == []


def _enable_codex_human_quorum(root: Path) -> None:
    """code-reviewer requires codex (automated) + human, min_independent 2."""
    (root / ".harness" / "review-governance.yaml").write_text(
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
        "      participants: [codex, human]\n"
        "      min_independent: 2\n"
        "      max_automatic_rounds_per_epoch: 2\n",
        encoding="utf-8",
    )


def _human_confirm_code_review(root: Path, monkeypatch: MonkeyPatch) -> Result:
    """Draft + TTY-confirm a human code-review approval; return the confirm run."""
    CliRunner().invoke(
        main,
        ["--json", "--workspace", str(root), "review", "prepare", "change",
         "--reviewer", "code-reviewer"],
    )
    packet = json.loads(
        (pending_reviews_dir(root, "change") / "code-reviewer"
         / "draft.packet.json").read_text()
    )
    verdict = {
        "bundle_digest": packet["bundle_digest"],
        "scope_sufficient": True,
        "checklist": [{"item": item, "status": "pass"} for item in packet["checklist"]],
        "findings": [],
        "prior_findings": [],
    }
    verdict_path = root / "human-verdict.json"
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    drafted = CliRunner().invoke(
        main,
        ["--json", "--workspace", str(root), "review", "human", "draft", "change",
         "--reviewer", "code-reviewer", "--verdict-file", str(verdict_path)],
    )
    assert drafted.exit_code == EXIT_OK, drafted.output
    nonce = json.loads(drafted.output)["data"]["nonce"]
    monkeypatch.setattr("super_harness.cli.review._interactive_terminal", lambda: True)
    return CliRunner().invoke(
        main,
        ["--workspace", str(root), "review", "human", "confirm", "change",
         "--reviewer", "code-reviewer", "--nonce", nonce],
        input="y\n",
    )


def test_automated_round_alone_cannot_approve_when_human_required(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Regression (PR#79 finding #1): a role that also lists a human participant
    (min_independent > automated count) must not reach code_review_passed on the
    automated imports alone — the human review is still required."""
    root = _repo(tmp_path)
    _enable_codex_human_quorum(root)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    imported = _import_run(root, begun, model="gpt-review")
    assert imported.exit_code == EXIT_OK, imported.output

    events = read_change_events(events_path(root), "change")
    types = [e.type for e in events]
    assert "code_review_passed" not in types
    closed = [e for e in events if e.type == "review_round_closed"]
    assert closed and closed[-1].payload["outcome"] == "execution_failed"


def test_mixed_quorum_completes_when_human_confirms_after_automated(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Regression (PR#79 finding #1): the intended mixed-role flow — automated
    round runs (holds, fail-closed), then the human completes the quorum. The
    human confirm counts the automated receipt for the same target HEAD (despite
    the shifted contract digest) and emits code_review_passed."""
    root = _repo(tmp_path)
    _enable_codex_human_quorum(root)
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    assert _import_run(root, begun, model="gpt-review").exit_code == EXIT_OK
    assert derive_state(events_path(root))["change"].current_state == "AWAITING_CODE_REVIEW"

    confirmed = _human_confirm_code_review(root, monkeypatch)
    assert confirmed.exit_code == EXIT_OK, confirmed.output

    events = read_change_events(events_path(root), "change")
    passed = [e for e in events if e.type == "code_review_passed"]
    assert passed, "human confirm should complete the codex+human quorum"
    assert set(passed[-1].payload["independent_sources"]) == {"codex", "human"}
    assert derive_state(events_path(root))["change"].current_state == "READY_TO_MERGE"


def _single_source_governance(root: Path, *, blocking_severity: str | None) -> None:
    """Rewrite `_repo`'s single-source (codex) code-reviewer governance,
    optionally pinning `blocking_severity`. Call BEFORE `_prepare` so the value
    is frozen into the round."""
    extra = (
        f"      blocking_severity: {blocking_severity}\n"
        if blocking_severity is not None
        else ""
    )
    (root / ".harness" / "review-governance.yaml").write_text(
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
        "      max_automatic_rounds_per_epoch: 2\n" + extra,
        encoding="utf-8",
    )


def _import_verdict_with_finding(
    root: Path,
    monkeypatch: MonkeyPatch,
    *,
    severity: str,
    finding_id: str = "F-1",
    scope_sufficient: bool = True,
) -> dict[str, object]:
    """Drive prepare→begin→import for the single codex source with a verdict
    that fails checklist[0] and raises one finding of `severity`. Returns the
    import command's JSON `data`."""
    _fake_codex(root, monkeypatch)
    _prepare(root)
    begun = _begin(root)
    verdict = _result_for_run(begun)
    verdict["scope_sufficient"] = scope_sufficient
    checklist = cast(list[dict[str, object]], verdict["checklist"])
    checklist[0]["status"] = "fail"
    verdict["findings"] = [
        {
            "id": finding_id,
            "severity": severity,
            "file": "src/app.py",
            "summary": "A graded observation about the change.",
        }
    ]
    result_path = root / "codex-result.json"
    result_path.write_text(json.dumps(verdict), encoding="utf-8")
    imported = CliRunner().invoke(
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
    assert imported.exit_code == EXIT_OK, imported.output
    return cast(dict[str, object], json.loads(imported.output)["data"])


def test_minor_only_round_approves_at_default_threshold(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)  # default governance → blocking_severity major
    data = _import_verdict_with_finding(root, monkeypatch, severity="minor")
    assert data["round_outcome"] == "approved"
    assert data["milestone"] == "code_review_passed"


def test_minor_finding_still_surfaces_in_open_undisposed(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from super_harness.core.review_verdict import derive_open_findings

    root = _repo(tmp_path)
    _import_verdict_with_finding(
        root, monkeypatch, severity="minor", finding_id="MIN-1"
    )
    # Honesty law: an APPROVED minor finding is still recorded + surfaced
    # (findings are namespaced by source/run, e.g. `codex/run_.../MIN-1`).
    open_ids = derive_open_findings(
        read_change_events(events_path(root), "change"), "change"
    )
    assert any(fid.endswith("/MIN-1") for fid in open_ids), open_ids


def test_major_finding_still_rejects_at_default_threshold(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    data = _import_verdict_with_finding(root, monkeypatch, severity="major")
    assert data["round_outcome"] == "rejected"


def test_blocking_severity_minor_restores_reject_on_minor(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _single_source_governance(root, blocking_severity="minor")
    data = _import_verdict_with_finding(root, monkeypatch, severity="minor")
    assert data["round_outcome"] == "rejected"


def test_scope_insufficient_rejects_regardless_of_severity(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    data = _import_verdict_with_finding(
        root, monkeypatch, severity="minor", scope_sufficient=False
    )
    assert data["round_outcome"] == "rejected"
