"""Unit tests for ``cli/verify_render.render_failure_summary``.

The renderer is pure: dict (``SensorResult.details`` shape) → str. These tests
nail down the contract independent of `verify`/`done` CLI wiring (which is
tested separately in ``test_verify.py`` / ``test_done.py``).

Covers per OPEN-ITEMS S6 fixup:
- A failing must_pass check renders id + exit_code + duration_ms + output_path.
- A passing check is NOT rendered.
- An advisory failure (``must_pass: false``) is NOT rendered.
- ``output_path: None`` does NOT leak the literal string ``"None"``.
- ``summary_path`` is always the last line when present.
- Empty input (no failures, no summary_path) → empty string (caller-safe).
"""
from __future__ import annotations

from super_harness.cli.verify_render import render_failure_summary


def _row(
    *,
    check_id: str,
    status: str = "fail",
    exit_code: int = 1,
    duration_ms: int = 100,
    must_pass: bool = True,
    output_path: str | None = ".harness/verification-results/x/0/x.stdout",
) -> dict[str, object]:
    return {
        "id": check_id,
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "must_pass": must_pass,
        "output_path": output_path,
    }


def test_render_lists_failing_must_pass_check_with_all_fields() -> None:
    details = {
        "change_id": "my-change",
        "all_pass_must": False,
        "checks_run": 1,
        "results": [
            _row(
                check_id="openspec-validate",
                exit_code=1,
                duration_ms=1321,
                output_path=".harness/verification-results/c/0/openspec-validate.stdout",
            )
        ],
        "summary_path": ".harness/verification-results/c/0/summary.json",
    }
    out = render_failure_summary(details)
    assert "openspec-validate" in out
    assert "exit 1" in out
    assert "1321ms" in out
    assert ".harness/verification-results/c/0/openspec-validate.stdout" in out
    assert ".harness/verification-results/c/0/summary.json" in out


def test_render_skips_passing_checks() -> None:
    details = {
        "results": [
            _row(check_id="passed-1", status="pass", exit_code=0),
            _row(check_id="failed-1", status="fail", exit_code=2),
        ],
        "summary_path": ".harness/x/summary.json",
    }
    out = render_failure_summary(details)
    assert "failed-1" in out
    assert "passed-1" not in out


def test_render_skips_advisory_failures() -> None:
    # An advisory (must_pass=False) failure must NOT be listed — it doesn't
    # drive the verdict and isn't the operator's actionable signal here.
    details = {
        "results": [
            _row(check_id="advisory-fail", status="fail", must_pass=False),
            _row(check_id="must-pass-fail", status="fail", must_pass=True),
        ],
        "summary_path": ".harness/x/summary.json",
    }
    out = render_failure_summary(details)
    assert "must-pass-fail" in out
    assert "advisory-fail" not in out


def test_render_handles_none_output_path_without_leaking_literal_none() -> None:
    details = {
        "results": [
            _row(
                check_id="no-archive",
                exit_code=1,
                duration_ms=42,
                output_path=None,
            )
        ],
        "summary_path": ".harness/x/summary.json",
    }
    out = render_failure_summary(details)
    assert "no-archive" in out
    # No literal "None" leak — the dangling-pointer guard.
    assert "None" not in out
    # And no orphan `see:` line either.
    assert "see:" not in out


def test_render_includes_summary_path_last() -> None:
    details = {
        "results": [_row(check_id="boom")],
        "summary_path": ".harness/x/summary.json",
    }
    out = render_failure_summary(details)
    lines = out.splitlines()
    assert lines[-1] == "full summary: .harness/x/summary.json"


def test_render_returns_empty_when_nothing_to_say() -> None:
    # No failing must_pass rows AND no summary_path → render nothing so the
    # caller can echo unconditionally without polluting stderr.
    assert render_failure_summary({"results": [], "summary_path": ""}) == ""
    assert render_failure_summary({}) == ""


def test_render_no_trailing_newline() -> None:
    # Caller uses click.echo() which adds a newline; we must not double it.
    details = {
        "results": [_row(check_id="boom")],
        "summary_path": ".harness/x/summary.json",
    }
    out = render_failure_summary(details)
    assert out
    assert not out.endswith("\n")
