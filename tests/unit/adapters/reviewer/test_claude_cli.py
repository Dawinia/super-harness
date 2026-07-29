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
    assert invocation.stdout_path == run_dir / "result.raw.json"
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


def _compile(tmp_path: Path, schema_json: str):
    """Compile one invocation from a schema file; returns (invocation, schema_path)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    prompt_path = run_dir / "prompt.md"
    schema_path = run_dir / "verdict.schema.json"
    schema_path.write_text(schema_json, encoding="utf-8")
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")
    invocation = adapter.compile_invocation(
        workspace=tmp_path,
        run_dir=run_dir,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model="claude-review",
        agent_options={"effort": "medium"},
    )
    return invocation, schema_path


def _inlined_schema(invocation) -> dict:
    import json

    argv = list(invocation.argv)
    return json.loads(argv[argv.index("--json-schema") + 1])


def test_inlined_schema_omits_dollar_schema(tmp_path: Path) -> None:
    """The installed Claude CLI rejects the draft-2020-12 `$schema` URI outright
    ("no schema with key or ref ..."), exiting non-zero with EMPTY stdout — which
    then surfaces as an unparseable result. Every other key must survive."""
    invocation, _ = _compile(
        tmp_path,
        '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        '"type":"object","additionalProperties":false,'
        '"required":["run_id"],"properties":{"run_id":{"type":"string"}}}',
    )
    inlined = _inlined_schema(invocation)
    assert "$schema" not in inlined
    assert inlined == {
        "type": "object",
        "additionalProperties": False,
        "required": ["run_id"],
        "properties": {"run_id": {"type": "string"}},
    }


def test_schema_file_on_disk_is_not_mutated(tmp_path: Path) -> None:
    """Codex consumes the SAME file by path and does accept `$schema`, so the
    strip must happen on a copy."""
    import json

    original = (
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}'
    )
    _, schema_path = _compile(tmp_path, original)
    assert json.loads(schema_path.read_text(encoding="utf-8"))["$schema"] == (
        "https://json-schema.org/draft/2020-12/schema"
    )


def test_schema_without_dollar_schema_is_passed_through(tmp_path: Path) -> None:
    invocation, _ = _compile(tmp_path, '{"type":"object","title":"verdict"}')
    assert _inlined_schema(invocation) == {"type": "object", "title": "verdict"}


def test_no_json_schema_ref_url_in_argv(tmp_path: Path) -> None:
    """Regression anchor for the real defect: a refactor that re-inlines the raw
    schema object must fail loudly here."""
    invocation, _ = _compile(
        tmp_path,
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}',
    )
    assert not any("json-schema.org" in arg for arg in invocation.argv)
