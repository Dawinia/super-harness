"""Codex CLI reviewer producer protocol (compile/parse only)."""

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

_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
_SANDBOXES = frozenset({"read-only", "workspace-write", "danger-full-access"})


class CodexCliReviewerProtocol(ReviewerProtocolAdapter):
    """Compile a fresh, ephemeral `codex exec` review invocation."""

    name = "codex-cli"

    def __init__(self, executable: str | None = None) -> None:
        resolved = executable or shutil.which("codex")
        if not resolved:
            raise ReviewerProtocolError(
                "codex-cli is not installed (codex not found on PATH)"
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
            raise ReviewerProtocolError("codex-cli requires an explicit model")
        unsupported = set(agent_options) - {"reasoning_effort", "sandbox"}
        if unsupported:
            raise ReviewerProtocolError(
                f"codex-cli unsupported agent option {sorted(unsupported)[0]!r}"
            )
        effort = agent_options.get("reasoning_effort")
        if not isinstance(effort, str) or effort not in _EFFORTS:
            raise ReviewerProtocolError(
                f"codex-cli reasoning_effort must be one of {sorted(_EFFORTS)}"
            )
        sandbox = agent_options.get("sandbox")
        if not isinstance(sandbox, str) or sandbox not in _SANDBOXES:
            raise ReviewerProtocolError(
                f"codex-cli sandbox must be one of {sorted(_SANDBOXES)}"
            )
        output_path = run_dir / "result.json"
        telemetry_path = run_dir / "events.jsonl"
        argv = (
            self.executable,
            "exec",
            "--ephemeral",
            "--model",
            model,
            "--sandbox",
            sandbox,
            "--config",
            f'model_reasoning_effort="{effort}"',
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--cd",
            str(workspace),
            "-",
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
            stdout_path=telemetry_path,
            telemetry_path=telemetry_path,
        )

    def parse_result(
        self, output_path: Path, *, telemetry_path: Path | None = None
    ) -> ReviewerProtocolResult:
        """Parse the schema-bound verdict plus optional Codex JSONL telemetry."""

        verdict = self._read_json_object(output_path)
        if telemetry_path is None or not telemetry_path.is_file():
            return ReviewerProtocolResult(verdict=verdict)

        actual_model: str | None = None
        session_id: str | None = None
        usage: dict[str, Any] | None = None
        duration_ms: int | float | None = None
        tool_trace: list[dict[str, Any]] = []
        try:
            lines = telemetry_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                event: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            candidate_model = event.get("model")
            if actual_model is None and isinstance(candidate_model, str) and candidate_model:
                actual_model = candidate_model
            for key in ("thread_id", "session_id"):
                candidate_session = event.get(key)
                if (
                    session_id is None
                    and isinstance(candidate_session, str)
                    and candidate_session
                ):
                    session_id = candidate_session
            candidate_usage = event.get("usage")
            if isinstance(candidate_usage, dict):
                usage = dict(candidate_usage)
            candidate_duration = event.get("duration_ms")
            if isinstance(candidate_duration, (int, float)) and not isinstance(
                candidate_duration, bool
            ):
                duration_ms = candidate_duration
            if event.get("type") != "item.completed":
                continue
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {
                "command_execution",
                "file_change",
                "mcp_tool_call",
                "web_search",
            }:
                evidence = {
                    key: item[key]
                    for key in (
                        "id",
                        "type",
                        "status",
                        "command",
                        "exit_code",
                        "server",
                        "tool",
                        "query",
                        "action",
                    )
                    if key in item
                }
                changes = item.get("changes")
                if isinstance(changes, list):
                    evidence["change_count"] = len(changes)
                tool_trace.append(evidence)
        return ReviewerProtocolResult(
            verdict=verdict,
            actual_model=actual_model,
            session_id=session_id,
            usage=usage,
            duration_ms=duration_ms,
            tool_trace=tool_trace or None,
        )
