"""JSON envelope wrapper for super-harness CLI machine-readable output.

Per cli-command-surface §3.4. All `--json` invocations emit a single-line JSON
object with this exact 6-key shape:

    {
      "command": "verify",
      "version": "0.1.0",
      "status": "pass" | "fail" | "warning",
      "exit_code": 0,
      "data": { /* command-specific */ },
      "errors": [ /* present even when empty */ ]
    }

This shape is the public contract CI parsers depend on. v0.1 freezes the keys;
v0.2+ may add new keys (CI parsers must tolerate unknown keys) but MUST NOT
remove or rename existing ones without a major version bump.
"""
import json
from typing import Any, Literal

from super_harness.version import __version__

# Envelope-level Status. Note: deliberately 3 values (NOT 4 like
# sensor-gate-architecture §AC-1's SensorResult.status). Sensor-level status
# is a per-sensor verdict; envelope-level Status is the **rolled-up command
# outcome**: did this CLI invocation succeed (pass) / fail / surface a
# non-blocking warning. Informational sensor results roll up to "pass" or
# "warning" at the envelope level, never surface as their own envelope status.
Status = Literal["pass", "fail", "warning"]


def json_envelope(
    *,
    command: str,
    status: Status,
    exit_code: int,
    data: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> str:
    """Render a single-line JSON envelope.

    Args:
        command: human-readable command name (e.g., "verify", "change start").
        status: one of "pass" / "fail" / "warning".
        exit_code: the process exit code the caller will sys.exit() with.
        data: command-specific payload. None → empty dict in output.
        errors: list of error objects (each at least {code, message}). None → empty list.

    Returns:
        Single-line JSON string (newline-free). Caller does
        `click.echo(json_envelope(...))` — click adds the trailing newline.
    """
    payload: dict[str, Any] = {
        "command": command,
        "version": __version__,
        "status": status,
        "exit_code": exit_code,
        "data": data or {},
        "errors": errors or [],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=False)
