"""No-execution contract for external reviewer producer protocols."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar


class ReviewerProtocolError(ValueError):
    """A producer profile, invocation, or completed output is invalid."""


@dataclass(frozen=True)
class ReviewerInvocation:
    """Frozen instructions for a caller-owned external producer process."""

    protocol: str
    argv: tuple[str, ...]
    cwd: Path
    stdin_path: Path
    output_path: Path
    requested_model: str
    requested_options: dict[str, Any]
    capture_stdout: bool = False
    stdout_path: Path | None = None
    telemetry_path: Path | None = None


@dataclass(frozen=True)
class ReviewerProtocolResult:
    """Normalized imported output plus optional producer-reported telemetry."""

    verdict: dict[str, Any]
    actual_model: str | None = None
    session_id: str | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int | float | None = None
    tool_trace: object | None = None


class ReviewerProtocolAdapter(ABC):
    """Compile invocations and parse existing outputs without running them."""

    name: ClassVar[str]

    @abstractmethod
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
        """Compile a caller-executed invocation contract."""

    def _read_json_object(self, output_path: Path) -> dict[str, Any]:
        """Parse a completed producer output file as a JSON object."""

        try:
            parsed: object = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewerProtocolError(
                f"cannot parse {self.name} result {output_path}: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ReviewerProtocolError(
                f"{self.name} result must be a JSON object, got "
                f"{type(parsed).__name__}"
            )
        return parsed

    def parse_result(
        self, output_path: Path, *, telemetry_path: Path | None = None
    ) -> ReviewerProtocolResult:
        """Normalize a direct structured verdict with unknown telemetry."""

        del telemetry_path
        return ReviewerProtocolResult(verdict=self._read_json_object(output_path))
