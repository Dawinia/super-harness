"""Codex CLI reviewer producer protocol (compile/parse only)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from super_harness.adapters.reviewer.base import (
    ReviewerInvocation,
    ReviewerProtocolAdapter,
    ReviewerProtocolError,
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
        )
