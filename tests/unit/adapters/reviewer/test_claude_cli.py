from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.adapters.reviewer.base import ReviewerProtocolError
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


def test_rejects_is_error_even_with_well_formed_structured_output(
    tmp_path: Path,
) -> None:
    """A producer that reports failure must never be recorded as a genuine result,
    even if it also emitted a complete-looking verdict object."""
    output = tmp_path / "result.raw.json"
    output.write_text(
        """{
  "is_error": true,
  "result": "API Error: 529 Overloaded",
  "structured_output": {
    "bundle_digest": "digest",
    "checklist": [],
    "findings": []
  }
}
""",
        encoding="utf-8",
    )
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    with pytest.raises(ReviewerProtocolError) as excinfo:
        adapter.parse_result(output)

    message = str(excinfo.value)
    assert "529 Overloaded" in message
    assert "invocation.json" in message
    assert "review run fail" in message


def test_rejects_is_error_without_structured_output(tmp_path: Path) -> None:
    """Pins the empirically observed 529 shape (no structured_output at all) so a
    later refactor cannot silently start accepting it.

    Asserting on the message is load-bearing: this shape ALSO trips the older
    "missing object structured_output" guard, so a bare `pytest.raises` would keep
    passing if the is_error guard were deleted. The remedy text is the stable marker
    that the diagnosis was "the producer failed", not "the payload was malformed"."""
    output = tmp_path / "result.raw.json"
    output.write_text(
        '{"is_error": true, "result": "API Error: 529 Overloaded"}\n',
        encoding="utf-8",
    )
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    with pytest.raises(ReviewerProtocolError) as excinfo:
        adapter.parse_result(output)

    assert "review run fail" in str(excinfo.value)


def test_parses_clean_payload_with_is_error_false(tmp_path: Path) -> None:
    output = tmp_path / "result.raw.json"
    output.write_text(
        """{
  "is_error": false,
  "result": "done",
  "structured_output": {
    "bundle_digest": "digest",
    "checklist": [],
    "findings": []
  }
}
""",
        encoding="utf-8",
    )
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    assert adapter.parse_result(output).verdict["bundle_digest"] == "digest"


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


def _result_file(tmp_path: Path, extra: str) -> Path:
    output = tmp_path / "result.raw.json"
    output.write_text(
        "{\n"
        '  "structured_output": {"bundle_digest": "d", "checklist": [], "findings": []}'
        f"{extra}\n"
        "}\n",
        encoding="utf-8",
    )
    return output


def test_parse_result_records_session_id(tmp_path: Path) -> None:
    """claude reports its session id and we currently discard it, so every claude
    receipt in the wild records null. Pure recording — nothing depends on it yet."""
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")
    out = _result_file(tmp_path, ',\n  "session_id": "6f73688a-e5d2-4e5f-b203-fd05d406d336"')

    assert adapter.parse_result(out).session_id == "6f73688a-e5d2-4e5f-b203-fd05d406d336"


def test_parse_result_tolerates_missing_or_non_string_session_id(tmp_path: Path) -> None:
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    assert adapter.parse_result(_result_file(tmp_path, "")).session_id is None
    assert adapter.parse_result(_result_file(tmp_path, ',\n  "session_id": 17')).session_id is None
    assert adapter.parse_result(_result_file(tmp_path, ',\n  "session_id": ""')).session_id is None


def test_parse_result_records_reported_cost(tmp_path: Path) -> None:
    """`claude --print` states its own cost at the top level. The harness does not
    price tokens itself; it stops throwing away a number the producer supplies."""
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")
    out = _result_file(tmp_path, ',\n  "total_cost_usd": 1.340269')

    assert adapter.parse_result(out).reported_cost_usd == 1.340269


def test_parse_result_never_fabricates_a_cost(tmp_path: Path) -> None:
    adapter = ClaudeCliReviewerProtocol(executable="/opt/bin/claude")

    assert adapter.parse_result(_result_file(tmp_path, "")).reported_cost_usd is None
    assert adapter.parse_result(
        _result_file(tmp_path, ',\n  "total_cost_usd": "1.34"')
    ).reported_cost_usd is None
    assert adapter.parse_result(
        _result_file(tmp_path, ',\n  "total_cost_usd": true')
    ).reported_cost_usd is None
