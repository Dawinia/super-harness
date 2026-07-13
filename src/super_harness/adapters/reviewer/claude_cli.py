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
        compact_schema = json.dumps(schema, separators=(",", ":"), sort_keys=True)
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
        )

    def parse_result(self, output_path: Path) -> ReviewerProtocolResult:
        """Unwrap Claude JSON output and preserve optional reported telemetry."""

        raw = self._read_json_object(output_path)
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
        return ReviewerProtocolResult(
            verdict=verdict,
            actual_model=actual_model,
            usage=normalized_usage,
            duration_ms=duration_ms,
            tool_trace=raw.get("tool_trace"),
        )
