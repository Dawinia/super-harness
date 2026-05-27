"""Path resolution helpers for super-harness.

Every CLI command + sensor + adapter that touches the workspace needs to find
the `.harness/` directory. `find_harness_root` walks up from a given path
(usually cwd) until it finds the directory or hits the filesystem root.

Per cli-command-surface §3.1 (workspace resolution) + lifecycle-event-model
§2 (canonical file locations under `.harness/`).
"""
from pathlib import Path


class HarnessNotInitialized(RuntimeError):
    """Raised when no `.harness/` directory is found walking up from start."""


def find_harness_root(start: Path) -> Path:
    """Walk up from `start` looking for `.harness/`.

    Args:
        start: where to begin search (typically cwd or `--workspace` argument)

    Returns:
        The directory containing `.harness/`.

    Raises:
        HarnessNotInitialized: if filesystem root is reached without finding `.harness/`.
    """
    current = start.resolve()
    while True:
        if (current / ".harness").is_dir():
            return current
        if current.parent == current:
            raise HarnessNotInitialized(
                f"No .harness/ directory found from {start} or any parent. "
                "Run `super-harness init` first."
            )
        current = current.parent


def events_path(root: Path) -> Path:
    """`.harness/events.jsonl` — append-only event stream (lifecycle §2)."""
    return root / ".harness" / "events.jsonl"


def state_path(root: Path) -> Path:
    """`.harness/state.yaml` — derived cache (lifecycle §3.8 reducer output)."""
    return root / ".harness" / "state.yaml"


def lock_path(root: Path, name: str) -> Path:
    """`.harness/.<name>.lock` — fcntl.flock sentinel files for serializing writes."""
    return root / ".harness" / f".{name}.lock"
