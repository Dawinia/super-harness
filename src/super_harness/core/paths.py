"""Path resolution helpers for super-harness.

Every CLI command + sensor + adapter that touches the workspace needs to find
the `.harness/` directory. `find_harness_root` walks up from a given path
(usually cwd) until it finds the directory or hits the filesystem root.

Per cli-command-surface §3.1 (workspace resolution) + lifecycle-event-model
§2 (canonical file locations under `.harness/`).
"""
from pathlib import Path


class HarnessNotInitialized(RuntimeError):
    """Raised when no `.harness/` directory is found walking up from start.

    CLI callers should map this exception to exit code 3 (EXIT_NO_CONFIG) per
    cli-command-surface §2.2 "Config not found" semantics.

    Carries `.message` (one-line error, no trailing period, no remediation) and
    `.hint` (actionable next step) as separate attributes so CLI wrap sites can
    pass them to `format_error(message=..., hint=...)` — the format contract
    requires remediation on its own ``Hint:`` line, not inline in the message.
    `__str__` joins them with a space for non-CLI callers that just want the
    full sentence (older `str(e)` consumers + log output stay unchanged).
    """

    # Stable hint text — every CLI wrap site reads this verbatim so any future
    # tweak lands in exactly one place.
    HINT = "Run `super-harness init` first."

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.hint = self.HINT

    def __str__(self) -> str:
        # Preserve the original one-string shape for any non-CLI caller (logs,
        # `str(e)` in tests, debug repr). CLI sites should use .message + .hint.
        return f"{self.message}. {self.hint}"


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
            # Pass ONLY the one-line error portion — the remediation lives on
            # `HarnessNotInitialized.hint` (HINT class attr) so CLI wrap sites
            # can route it to format_error's `Hint:` line.
            raise HarnessNotInitialized(
                f"No .harness/ directory found from {start} or any parent"
            )
        current = current.parent


def events_path(root: Path) -> Path:
    """`.harness/events.jsonl` — append-only event stream (lifecycle §2)."""
    return root / ".harness" / "events.jsonl"


def gate_blocks_path(root: Path) -> Path:
    """`.harness/gate-blocks.jsonl` — append-only, best-effort local telemetry of
    pre-tool-use gate BLOCK decisions (Stage 2). Observability only: NOT part of
    the event stream, never drives state (see core/gate_blocks.py)."""
    return root / ".harness" / "gate-blocks.jsonl"


def state_path(root: Path) -> Path:
    """`.harness/state.yaml` — derived cache (lifecycle §3.8 reducer output)."""
    return root / ".harness" / "state.yaml"


def lock_path(root: Path, name: str) -> Path:
    """`.harness/.<name>.lock` — fcntl.flock sentinel files for serializing writes."""
    return root / ".harness" / f".{name}.lock"


def sensors_yaml_path(root: Path) -> Path:
    """`.harness/sensors.yaml` — sensor registry config (sensor-gate-architecture §2.3).

    Optional file: absent → only built-in sensors are available. Phase 3.5
    (`super-harness sensor list`) reads this to list the built-in sensors named
    in it (v0.1 is builtin-only).
    """
    return root / ".harness" / "sensors.yaml"


def gates_yaml_path(root: Path) -> Path:
    """`.harness/gates.yaml` — gate registry config (sensor-gate-architecture §2.3).

    Optional file: absent → only built-in gates are available. Phase 3.5
    (`super-harness gate list`) reads this to list the built-in gates named in
    it (v0.1 is builtin-only).
    """
    return root / ".harness" / "gates.yaml"


def adapters_yaml_path(root: Path) -> Path:
    """`.harness/adapters.yaml` — adapter registry config (adapter-architecture §2.3).

    Optional file: absent → only built-in adapters are available. Lists both
    framework and agent adapters (the §2.3 list-of-dicts shape) for the
    `super-harness adapter` CLI to enumerate.
    """
    return root / ".harness" / "adapters.yaml"


def verification_yaml_path(root: Path) -> Path:
    """`.harness/verification.yaml` — verification check config (engineering-integration §2.3).

    Written by `init`; read by the `VerificationRunner` sensor (Phase 8) and the
    `verify` / `done` CLI commands. Absence is a hard error (EXIT_NO_CONFIG), not
    a fall-back-to-defaults signal — see `engineering.verification_config`.
    """
    return root / ".harness" / "verification.yaml"


def verification_results_dir(root: Path, change_id: str, ts: str) -> Path:
    """`.harness/verification-results/<change_id>/<ts>` — per-run output archive.

    The `VerificationRunner` sensor (Phase 8) writes captured check output and a
    `summary.json` here. `ts` is an ISO-8601 UTC timestamp string (the run's
    start time, produced by the runner) so concurrent / repeat runs for one
    change never clobber each other.
    """
    return root / ".harness" / "verification-results" / change_id / ts


def pending_reviews_dir(root: Path, change_id: str) -> Path:
    """Per-change directory for transient review bundles (gitignored — input aid,
    NOT the record of review; the record is the inlined verdict in the event)."""
    return root / ".harness" / "pending-reviews" / change_id


def canonical_relpath(root: Path, file: str | None) -> str | None:
    """Resolve `file` to a POSIX repo-relative path under `root`, or None.

    `file` may be absolute or relative-to-root. Symlinks and `..` are resolved
    (via `Path.resolve()`), so callers that require a `.md` suffix MUST re-check it
    AFTER this call — a `docs/plans/c.md` symlink to `src/x.py` returns `"src/x.py"`.
    Anything that does not resolve to a path *inside* `root` (a true traversal
    escape, an absolute path outside, an unresolvable path) returns None. Never
    raises — soundness comes from the caller's `.md` + recorded-artifact checks, not
    from this function guessing intent.
    """
    if not file:
        return None
    try:
        base = root.resolve()
        p = Path(file)
        target = (p if p.is_absolute() else base / p).resolve()
        return target.relative_to(base).as_posix()
    except (ValueError, OSError, RuntimeError):
        return None
