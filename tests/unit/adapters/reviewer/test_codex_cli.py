from __future__ import annotations

from pathlib import Path

from super_harness.adapters.reviewer.codex_cli import CodexCliReviewerProtocol


def test_compiles_fresh_codex_invocation_without_executing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    prompt_path = run_dir / "prompt.md"
    schema_path = run_dir / "verdict.schema.json"
    adapter = CodexCliReviewerProtocol(executable="/opt/bin/codex")

    invocation = adapter.compile_invocation(
        workspace=tmp_path,
        run_dir=run_dir,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model="gpt-review",
        agent_options={
            "reasoning_effort": "medium",
            "sandbox": "read-only",
        },
    )

    assert invocation.argv == (
        "/opt/bin/codex",
        "exec",
        "--ephemeral",
        "--model",
        "gpt-review",
        "--sandbox",
        "read-only",
        "--config",
        'model_reasoning_effort="medium"',
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(run_dir / "result.json"),
        "--cd",
        str(tmp_path),
        "-",
    )
    assert invocation.stdin_path == prompt_path
    assert invocation.output_path == run_dir / "result.json"
    assert invocation.telemetry_path == run_dir / "events.jsonl"
    assert invocation.stdout_path == run_dir / "events.jsonl"
    assert invocation.cwd == tmp_path
    assert invocation.capture_stdout is True
    assert invocation.requested_model == "gpt-review"
    assert invocation.requested_options == {
        "reasoning_effort": "medium",
        "sandbox": "read-only",
    }
    assert not hasattr(adapter, "run")


def test_parses_direct_codex_verdict_without_inventing_telemetry(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    output.write_text(
        '{"bundle_digest":"digest","checklist":[],"findings":[]}\n',
        encoding="utf-8",
    )
    adapter = CodexCliReviewerProtocol(executable="/opt/bin/codex")

    result = adapter.parse_result(output)

    assert result.verdict == {
        "bundle_digest": "digest",
        "checklist": [],
        "findings": [],
    }
    assert result.actual_model is None
    assert result.usage is None
    assert result.duration_ms is None


def test_parses_codex_jsonl_telemetry_without_replacing_verdict(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    output.write_text(
        '{"bundle_digest":"digest","checklist":[],"findings":[]}\n',
        encoding="utf-8",
    )
    telemetry = tmp_path / "events.jsonl"
    telemetry.write_text(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"thread-123",'
                '"model":"gpt-review"}',
                '{"type":"item.completed","item":{"id":"item-1",'
                '"type":"command_execution","command":"git diff",'
                '"aggregated_output":"large command output",'
                '"exit_code":0,"status":"completed"}}',
                '{"type":"turn.completed","duration_ms":1234,"usage":'
                '{"input_tokens":100,"cached_input_tokens":25,'
                '"output_tokens":10}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = CodexCliReviewerProtocol(executable="/opt/bin/codex")

    result = adapter.parse_result(output, telemetry_path=telemetry)

    assert result.verdict == {
        "bundle_digest": "digest",
        "checklist": [],
        "findings": [],
    }
    assert result.actual_model == "gpt-review"
    assert result.session_id == "thread-123"
    assert result.usage == {
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 10,
    }
    assert result.duration_ms == 1234
    assert result.tool_trace == [
        {
            "id": "item-1",
            "type": "command_execution",
            "command": "git diff",
            "exit_code": 0,
            "status": "completed",
        }
    ]
