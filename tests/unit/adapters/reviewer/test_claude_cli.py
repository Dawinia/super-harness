from __future__ import annotations

from pathlib import Path

from super_harness.adapters.reviewer.claude_cli import ClaudeCliReviewerProtocol


def test_compiles_fresh_claude_invocation_without_executing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prompt_path = run_dir / "prompt.md"
    schema_path = run_dir / "verdict.schema.json"
    schema_path.write_text('{"type":"object"}\n', encoding="utf-8")
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    invocation = adapter.compile_invocation(
        workspace=tmp_path,
        run_dir=run_dir,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model="claude-review",
        agent_options={"effort": "medium"},
    )

    assert invocation.argv == (
        "/opt/bin/claude",
        "--print",
        "--no-session-persistence",
        "--model",
        "claude-review",
        "--effort",
        "medium",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "Read,Grep,Glob,Bash(git *)",
        "--output-format",
        "json",
        "--json-schema",
        '{"type":"object"}',
    )
    assert invocation.stdin_path == prompt_path
    assert invocation.output_path == run_dir / "result.raw.json"
    assert invocation.cwd == tmp_path
    assert invocation.capture_stdout is True
    assert invocation.requested_model == "claude-review"
    assert invocation.requested_options == {"effort": "medium"}
    assert not hasattr(adapter, "run")


def test_parses_claude_structured_output_and_optional_telemetry(tmp_path: Path) -> None:
    output = tmp_path / "result.raw.json"
    output.write_text(
        """{
  "structured_output": {
    "bundle_digest": "digest",
    "checklist": [],
    "findings": []
  },
  "modelUsage": {"claude-review": {"inputTokens": 120}},
  "usage": {"input_tokens": 120, "output_tokens": 30},
  "duration_ms": 4500
}
""",
        encoding="utf-8",
    )
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    result = adapter.parse_result(output)

    assert result.verdict["bundle_digest"] == "digest"
    assert result.actual_model == "claude-review"
    assert result.usage == {"input_tokens": 120, "output_tokens": 30}
    assert result.duration_ms == 4500
