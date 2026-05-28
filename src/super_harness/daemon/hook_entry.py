"""Click-less PreToolUse hook entry-point.

Registered in pyproject.toml `[project.scripts]` as `super-harness-hook`.
PreToolUse hook script invokes this binary; the click-less import chain
saves ~10ms of cold-start cost vs invoking `super-harness gate check`
(per sensor-gate-architecture §3.6 #5 / daemon-architecture §3.5).

CLI shape (positional only, no flags — click-less):
    super-harness-hook <tool> [file]
        argv[1] = tool name (e.g. "Edit", "Write")
        argv[2] = file path (optional; some tools don't have a file argument)
    env SUPER_HARNESS_CHANGE_ID  optional override; default reads
                                  `active_change_id` from .harness/state.yaml

Exit codes:
    0  ALLOW (decision == "allow", or no .harness/ found, or daemon down + fail-safe)
    1  BLOCK (decision == "block"; stderr has the reason)
"""
from __future__ import annotations

import sys
from pathlib import Path

from super_harness.core.paths import HarnessNotInitialized, find_harness_root


def main() -> None:  # console_script entry
    argv = sys.argv[1:]
    if not argv:
        # No tool name → permissive (Axiom 1: harness must not block tools it
        # doesn't understand).
        sys.exit(0)
    tool = argv[0]
    file = argv[1] if len(argv) > 1 else None

    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        # No super-harness in this workspace → not our concern; ALLOW transparently.
        sys.exit(0)

    # Resolve change_id: env override > state.yaml active_change_id > None.
    import os

    change_id = os.environ.get("SUPER_HARNESS_CHANGE_ID")
    if not change_id:
        change_id = _read_active_change_id(root)

    # Late import to keep startup lean — supervisor pulls in client + protocol
    # + subprocess. ~3-5ms on Apple Silicon vs ~12ms for click.
    from super_harness.daemon import supervisor

    decision, reason = supervisor.gate_pre_tool_use(
        root, tool=tool, file=file, change_id=change_id
    )
    if decision == "block":
        sys.stderr.write(f"super-harness: BLOCK ({reason})\n")
        sys.exit(1)
    sys.exit(0)


def _read_active_change_id(root: Path) -> str | None:
    """Read `.harness/state.yaml::active_change_id` without raising on absence.

    Intentionally NOT going through HotState — that's daemon-side. The hook
    entry-point talks to the daemon, so reading state.yaml here is only
    needed when the supervisor needs a change_id to pass in the request.
    """
    state_path = root / ".harness" / "state.yaml"
    if not state_path.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(state_path.read_text()) or {}
    except Exception:
        return None
    val = data.get("active_change_id")
    return str(val) if val else None


if __name__ == "__main__":  # pragma: no cover
    main()
