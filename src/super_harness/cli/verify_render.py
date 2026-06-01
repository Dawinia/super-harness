"""Rich failure-summary renderer shared by `verify` and `done` CLI failures.

Pre-v0.1.0 smoke walkthrough (OPEN-ITEMS #6 / S6) surfaced that `verify`'s
non-JSON failure output was a single, near-useless line —
``"verification failed (4 checks, 1 failed)"`` — with no pointer to WHICH
check failed or WHERE its captured output lived. `done`'s failure path was
marginally better (it embedded ``summary_path`` in the hint) but also omitted
the per-check breakdown.

This module owns the rich human-readable rendering of failing must_pass
checks. Both `cli/verify.py` and `cli/done.py` import
``render_failure_summary`` and print its output to STDERR on a failed verdict
(the existing single-line ``result.summary`` stays on STDOUT for back-compat
with anything grepping that exact string).

Design constraints:
- Pure dict → str transformation. NO I/O inside (the caller decides where to
  print: stderr, stdout, or nowhere under ``--quiet``).
- Renders ONLY ``must_pass`` failures. Advisory (``must_pass: false``)
  failures don't drive the verdict and aren't actionable for the human at
  this rendering layer — listing them here would dilute the signal.
- ``output_path`` is `None` when a check ran without ``capture:`` or failed
  before archiving. We render the row WITHOUT a ``see:`` line in that case;
  we never let the literal string ``"None"`` leak to the user (would be a
  confusing dangling pointer).
- ``summary_path`` from the data block always lands at the end. If for some
  reason it's missing (defensive — should never happen from
  ``verify_data_block``), the renderer simply skips that line.

The input is `SensorResult.details` AS-IS (the frozen
``verify_data_block`` shape from
``sensors/verification_runner.py::verify_data_block``):

    {
      "change_id": str,
      "all_pass_must": bool,
      "checks_run": int,
      "results": [
        {"id": str, "status": str, "exit_code": int, "duration_ms": int,
         "must_pass": bool, "output_path": str | None},
        ...
      ],
      "summary_path": str,
    }
"""
from __future__ import annotations

from typing import Any


def render_failure_summary(details: dict[str, Any]) -> str:
    """Render the rich per-failing-check breakdown + summary_path.

    Args:
        details: The ``SensorResult.details`` payload for a failed verdict,
            i.e. the frozen ``verify_data_block`` dict.

    Returns:
        A multi-line string with one indented row per failing ``must_pass``
        check (``id``, ``exit_code``, ``duration_ms``, optional ``see:``
        ``output_path``) followed by a ``full summary: <summary_path>``
        line. Returns the empty string if there are no must_pass failures
        AND no summary_path — i.e. there is nothing useful to add over the
        existing one-line ``result.summary``. (Callers can therefore call
        unconditionally on failure without polluting stderr on edge cases.)
        The returned string has NO leading or trailing newline; the caller
        is expected to wrap it in ``click.echo(..., err=True)`` which adds
        the trailing newline.
    """
    results = details.get("results", []) or []
    # Only render must_pass failures — advisory (must_pass=False) failures
    # don't drive the verdict; surfacing them here would confuse the operator
    # about which row is the actual failure.
    failing = [
        r for r in results if r.get("must_pass") and r.get("status") != "pass"
    ]
    summary_path = details.get("summary_path")

    if not failing and not summary_path:
        return ""

    lines: list[str] = []
    for row in failing:
        check_id = row.get("id", "<unknown>")
        exit_code = row.get("exit_code", "?")
        duration_ms = row.get("duration_ms", "?")
        # f-string, NOT .format(): user-CONFIGURED check ids and paths must
        # never get interpreted as format spec / brace injection (Phase 13).
        lines.append(f"  x {check_id} (exit {exit_code}, {duration_ms}ms)")
        output_path = row.get("output_path")
        # Suppress when None — the literal "None" is a dangling pointer that
        # would confuse the operator. Cap unknown / non-string defensively.
        if output_path:
            lines.append(f"      see: {output_path}")

    if summary_path:
        lines.append(f"full summary: {summary_path}")

    return "\n".join(lines)
