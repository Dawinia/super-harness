"""Claude CLI reviewer producer protocol (compile/parse only)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from super_harness.adapters.reviewer.base import (
    ReviewerInvocation,
    ReviewerProtocolAdapter,
    ReviewerProtocolError,
    ReviewerProtocolResult,
)

_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class ClaudeCliReviewerProtocol(ReviewerProtocolAdapter):
    """Compile a fresh, non-persistent `claude --print` review invocation."""

    name = "claude-cli"

    def __init__(self, executable: str | None = None) -> None:
        resolved = executable or shutil.which("claude")
        if not resolved:
            raise ReviewerProtocolError(
                "claude-cli is not installed (claude not found on PATH)"
            )
        self.executable = resolved

    def compile_invocation(
        self,
        *,
        workspace: Path,
        run_dir: Path,
        prompt_path: Path,
        schema_path: Path,
        model: str,
        agent_options: dict[str, Any],
    ) -> ReviewerInvocation:
        if not model:
            raise ReviewerProtocolError("claude-cli requires an explicit model")
        unsupported = set(agent_options) - {"effort"}
        if unsupported:
            raise ReviewerProtocolError(
                f"claude-cli unsupported agent option {sorted(unsupported)[0]!r}"
            )
        effort = agent_options.get("effort")
        if not isinstance(effort, str) or effort not in _EFFORTS:
            raise ReviewerProtocolError(
                f"claude-cli effort must be one of {sorted(_EFFORTS)}"
            )
        try:
            schema: object = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewerProtocolError(
                f"cannot load Claude verdict schema {schema_path}: {exc}"
            ) from exc
        if not isinstance(schema, dict):
            raise ReviewerProtocolError("Claude verdict schema must be a JSON object")
        # The installed Claude CLI validates `--json-schema` against its own registry
        # and rejects the draft-2020-12 `$schema` URI outright ("no schema with key or
        # ref https://json-schema.org/draft/2020-12/schema"), exiting non-zero with an
        # EMPTY stdout — which then surfaces downstream as an unparseable result, not
        # as the schema error it really is. The annotation is meaningless for a schema
        # inlined on the command line anyway. The strip is protocol-local and operates
        # on a copy: codex-cli passes the SAME file by path (`--output-schema`) and
        # does accept `$schema`, so the on-disk artefact must keep it.
        inlined = {k: v for k, v in schema.items() if k != "$schema"}
        compact_schema = json.dumps(inlined, separators=(",", ":"), sort_keys=True)
        output_path = run_dir / "result.raw.json"
        argv = (
            self.executable,
            "--print",
            "--no-session-persistence",
            "--model",
            model,
            "--effort",
            effort,
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            "Read,Grep,Glob,Bash(git *)",
            "--output-format",
            "json",
            "--json-schema",
            compact_schema,
        )
        return ReviewerInvocation(
            protocol=self.name,
            argv=argv,
            cwd=workspace,
            stdin_path=prompt_path,
            output_path=output_path,
            requested_model=model,
            requested_options=dict(agent_options),
            capture_stdout=True,
            stdout_path=output_path,
        )

    def parse_result(
        self, output_path: Path, *, telemetry_path: Path | None = None
    ) -> ReviewerProtocolResult:
        """Unwrap Claude JSON output and preserve optional reported telemetry."""

        del telemetry_path
        raw = self._read_json_object(output_path)
        # A `claude --print` failure still exits 0 and still writes well-formed JSON,
        # only with `is_error: true` and the error text in `result`. The observed
        # transient shape (HTTP 529) also drops `structured_output` and so is already
        # rejected below; this guard closes the residual case where a producer reports
        # failure AND emits a complete-looking verdict, which would otherwise be
        # recorded as a genuine review result.
        if raw.get("is_error"):
            reported = raw.get("result")
            detail = (
                f": {reported}"
                if isinstance(reported, str) and reported.strip()
                else ""
            )
            raise ReviewerProtocolError(
                "claude-cli reported is_error, so its output is not a review "
                f"verdict{detail}. If the failure was transient (e.g. HTTP 529 "
                "Overloaded), re-run the producer against the same frozen "
                "invocation.json — a retry does not consume a review round. If the "
                "producer is permanently unavailable (e.g. no API quota), record the "
                "attempt with `review run fail --run-id <id> --reason \"<why>\"`."
            )
        verdict = raw.get("structured_output")
        if not isinstance(verdict, dict):
            raise ReviewerProtocolError(
                "claude-cli result is missing object structured_output"
            )
        model_usage = raw.get("modelUsage")
        actual_model: str | None = None
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            candidate = next(iter(model_usage))
            if isinstance(candidate, str) and candidate:
                actual_model = candidate
        usage = raw.get("usage")
        normalized_usage = dict(usage) if isinstance(usage, dict) else None
        duration = raw.get("duration_ms")
        duration_ms = duration if isinstance(duration, (int, float)) else None
        session = raw.get("session_id")
        session_id = session if isinstance(session, str) and session else None
        cost = raw.get("total_cost_usd")
        reported_cost_usd = (
            float(cost)
            if isinstance(cost, (int, float)) and not isinstance(cost, bool)
            else None
        )
        return ReviewerProtocolResult(
            verdict=verdict,
            actual_model=actual_model,
            session_id=session_id,
            usage=normalized_usage,
            duration_ms=duration_ms,
            tool_trace=raw.get("tool_trace"),
            reported_cost_usd=reported_cost_usd,
        )
