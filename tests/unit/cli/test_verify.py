"""Unit tests for `super-harness verify` (Phase 8 Task 8.7).

`verify` drives the builtin VerificationRunner sensor through a one-shot
SensorDispatcher and reports its verdict. These tests stand up a real
`.harness/` with a hand-written `verification.yaml` and invoke `verify` via
CliRunner (the Phase-2 CLI test pattern).

Coverage:
  1. all-pass → exit 0 + a verification_passed event lands on disk
  2. must_pass fail (a `false` check) → exit 2 + verification_failed event
  3. --json → envelope.data == result.details (frozen verify_data_block keys)
  4. no `.harness/` → exit 3 (EXIT_NO_CONFIG)
  5. --layer / --check thread into the activity payload (here: --check selects)
  6. no slug + no active change → exit 2 (EXIT_VALIDATION)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)

# A verification.yaml with baseline disabled (keeps fixtures simple — no
# anchor/lifecycle/scope baselines to satisfy) and sequential execution so the
# `false` must_pass check is the deterministic verdict driver.
_PASS_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
  env: {}
execution:
  mode: sequential
  max_parallelism: 1
  fail_fast: false
checks:
  - id: ok-1
    command: "true"
  - id: ok-2
    command: "true"
adapter_provided: []
"""

_FAIL_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
  env: {}
execution:
  mode: sequential
  max_parallelism: 1
  fail_fast: false
checks:
  - id: ok-1
    command: "true"
  - id: boom
    command: "false"
adapter_provided: []
"""


def _init_workspace(ws: Path, *, yaml_text: str, slug: str | None = None) -> None:
    """Create `<ws>/.harness/` with verification.yaml and (optionally) a change.

    When `slug` is given, drive it through the lifecycle to
    IMPLEMENTATION_IN_PROGRESS so its events.jsonl is a realistic stream the
    sensor's baselines (if enabled) could read; for verify the change just needs
    to exist so read_active_change_id resolves it when no explicit slug is passed.
    """
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    (ws / ".harness" / "verification.yaml").write_text(yaml_text)
    if slug is not None:
        _drive_to_in_progress(ws, slug)


def _drive_to_in_progress(ws: Path, slug: str) -> None:
    """Emit intent_declared → plan_ready → plan_approved → implementation_started."""
    writer = EventWriter(events_path(ws))
    for evt_type in (
        "intent_declared",
        "plan_ready",
        "plan_approved",
        "implementation_started",
    ):
        writer.emit(
            Event(
                event_id=new_event_id(),
                type=evt_type,
                change_id=slug,
                timestamp="2026-05-29T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain",
                payload={},
            )
        )
    refresh_state_after_emit(ws)


def _read_event_types(ws: Path) -> list[str]:
    lines = events_path(ws).read_text().splitlines()
    return [json.loads(line)["type"] for line in lines if line.strip()]


def test_verify_all_pass_exit_0_and_event(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _read_event_types(tmp_path)[-1] == "verification_passed"


def test_verify_must_pass_fail_exit_2(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _read_event_types(tmp_path)[-1] == "verification_failed"


def test_verify_json_data_is_result_details(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "verify", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.output)
    assert payload["command"] == "verify"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    data = payload["data"]
    # Frozen verify_data_block keys (cli-command-surface §3.4).
    assert set(data) == {
        "change_id",
        "all_pass_must",
        "checks_run",
        "results",
        "summary_path",
    }
    assert data["change_id"] == "my-change"
    assert data["all_pass_must"] is True
    assert data["checks_run"] == 2
    # Per-result row shape (no `command` key in the data block — that lives in
    # summary.json only).
    assert set(data["results"][0]) == {
        "id",
        "status",
        "exit_code",
        "duration_ms",
        "must_pass",
        "output_path",
    }


def test_verify_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "No .harness/" in combined


def test_verify_missing_config_exit_3(tmp_path: Path) -> None:
    # `.harness/` exists but verification.yaml absent → EXIT_NO_CONFIG.
    (tmp_path / ".harness").mkdir()
    _drive_to_in_progress(tmp_path, "my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "verification config not found" in combined


def test_verify_check_filter_selects_subset(tmp_path: Path) -> None:
    # --check selects only `boom`; with `ok-1` filtered out the run is the single
    # failing must_pass check → fail, and checks_run == 1 proves the filter threaded
    # through the activity payload into collect_checks.
    _init_workspace(tmp_path, yaml_text=_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "verify", "my-change",
         "--check", "boom"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    data = json.loads(r.output)["data"]
    assert data["checks_run"] == 1
    assert data["results"][0]["id"] == "boom"


def test_verify_layer_filter_threaded(tmp_path: Path) -> None:
    # --layer user keeps the user_checks layer (both pass) → exit 0 with both
    # checks run. (Proves --layer reaches collect_checks; baseline is disabled
    # anyway so this isolates the user layer.)
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "verify", "my-change",
         "--layer", "user"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert json.loads(r.output)["data"]["checks_run"] == 2


def test_verify_no_slug_no_active_exit_2(tmp_path: Path) -> None:
    # `.harness/` + verification.yaml present, but NO change exists → no slug
    # and no active change → EXIT_VALIDATION.
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug=None)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "verify"])
    assert r.exit_code == EXIT_VALIDATION
    combined = r.output + (r.stderr or "")
    assert "no change specified" in combined


def test_verify_resolves_active_change(tmp_path: Path) -> None:
    # No explicit slug → resolve the most recently active non-terminal change from state.yaml.
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="active-one")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "verify"])
    assert r.exit_code == EXIT_OK, r.output
    assert _read_event_types(tmp_path)[-1] == "verification_passed"


# --------------------------------------------------------------------------- #
# FIX 1 — config / placeholder errors surface as EXIT_VALIDATION (2), not a
# raw traceback + EXIT_GENERIC (1).
# --------------------------------------------------------------------------- #

# Syntactically broken YAML (unbalanced brackets) → yaml.YAMLError at load.
_CORRUPT_YAML = "layers: {baseline: {enabled: true}\nchecks: [\n"

# Wrong shape: a bad `capture` enum value (valid YAML, invalid schema).
_BAD_ENUM_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  capture: everything
checks: []
adapter_provided: []
"""

# A non-allowlisted ${PR_URL} placeholder in a check command (rejected at load).
_BAD_PLACEHOLDER_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  capture: none
checks:
  - id: deploy
    command: "deploy ${PR_URL}"
adapter_provided: []
"""


def test_verify_corrupt_yaml_exit_2_no_traceback(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_CORRUPT_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "not valid YAML" in combined
    # A clean error, NOT a swallowed sensor crash / raw traceback.
    assert "Traceback" not in combined
    assert r.exception is None or isinstance(r.exception, SystemExit)


def test_verify_wrong_shape_exit_2(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_BAD_ENUM_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "Traceback" not in combined


def test_verify_bad_placeholder_exit_2(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_BAD_PLACEHOLDER_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "PR_URL" in combined
    assert "Traceback" not in combined


# --------------------------------------------------------------------------- #
# FIX 2 — an unknown / layer-mismatched `--check` id is a hard EXIT_VALIDATION,
# not a vacuous pass.
# --------------------------------------------------------------------------- #


def test_verify_unknown_check_exit_2(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "verify", "my-change",
         "--check", "does-not-exist"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "no such check" in combined
    assert "does-not-exist" in combined


def test_verify_known_check_still_runs(tmp_path: Path) -> None:
    # A valid --check id runs as before (exit 0 here — `ok-1` passes).
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "verify", "my-change",
         "--check", "ok-1"],
    )
    assert r.exit_code == EXIT_OK, r.output
    data = json.loads(r.output)["data"]
    assert data["checks_run"] == 1
    assert data["results"][0]["id"] == "ok-1"


def test_verify_check_layer_mismatch_exit_2(tmp_path: Path) -> None:
    # A baseline id requested under --layer user is not collectable there → error.
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "verify", "my-change",
         "--layer", "user", "--check", "lifecycle-ordering"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "no such check" in combined
    assert "lifecycle-ordering" in combined


# --------------------------------------------------------------------------- #
# FIX 3 — `--json` summary_path + results[].output_path are REPO-RELATIVE.
# --------------------------------------------------------------------------- #


def test_verify_json_paths_are_repo_relative(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "verify", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    data = json.loads(r.output)["data"]
    # Repo-relative: starts with `.harness/verification-results/`, no leading `/`.
    assert data["summary_path"].startswith(".harness/verification-results/")
    assert not data["summary_path"].startswith("/")
    for row in data["results"]:
        op = row["output_path"]
        # `capture: none` → output_path is None; if present it must be relative.
        if op is not None:
            assert not op.startswith("/")
            assert op.startswith(".harness/verification-results/")


# --------------------------------------------------------------------------- #
# Task 14.3 — --pr slug-from-PR resolution
#
# Resolution order (cli-command-surface §verify):
#   1. positional <slug>            → use directly
#   2. --pr <num> (no positional)   → fetch PR body, parse metadata block,
#                                     extract Change field, validate slug
#   3. read_active_change_id(root)  → fallback
#   4. None                         → exit 2 "no change specified"
#
# Exit-code matrix for --pr-only resolution failures (intentionally DIFFERS
# from `pr validate` — see verify.py module docstring + plan §Task 14.3):
#   gh.GhError                                  → 4 EXIT_EXTERNAL_TOOL
#   No metadata block at all                    → 4 EXIT_EXTERNAL_TOOL
#   Malformed metadata (unbalanced markers)     → 2 EXIT_VALIDATION
#   ≥2 metadata blocks (AC-3 violation)         → 2 EXIT_VALIDATION
#   Block present, missing Change field         → 4 EXIT_EXTERNAL_TOOL
#   Block present, Change present, bad slug     → 2 EXIT_VALIDATION
#   Block present, Change present, valid slug   → resolve OK, run verification
#
# gh is ALWAYS mocked here at `super_harness.cli.verify.gh.view_pr` (the
# import site inside verify.py — Phase 12 pattern).
# --------------------------------------------------------------------------- #

VIEW_PR_VERIFY = "super_harness.cli.verify.gh.view_pr"


def _metadata_body(change: str = "my-change") -> str:
    """A PR body carrying ONE well-formed super-harness metadata block."""
    return (
        "Some PR description.\n\n"
        "<!-- super-harness:metadata -->\n"
        f"Change: {change}\n"
        "Tier: Normal\n"
        "Verification: passed\n"
        "super-harness version: 0.1.0\n"
        "<!-- /super-harness:metadata -->\n"
    )


def test_verify_positional_only_does_not_call_gh(tmp_path: Path) -> None:
    """Positional slug + no --pr → gh.view_pr is NOT invoked (regression guard)."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    with mock.patch(VIEW_PR_VERIFY) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "my-change"]
        )
    assert r.exit_code == EXIT_OK, r.output
    m.assert_not_called()


def test_verify_pr_resolves_slug_and_runs(tmp_path: Path) -> None:
    """--pr only, well-formed block → resolves Change field → verification runs."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    with mock.patch(
        VIEW_PR_VERIFY, return_value={"body": _metadata_body("my-change")}
    ) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_OK, r.output
    m.assert_called_once()
    # The verification ran on the resolved slug (last event lands).
    assert _read_event_types(tmp_path)[-1] == "verification_passed"


def test_verify_pr_no_metadata_block_exits_4(tmp_path: Path) -> None:
    """--pr but the PR body has no metadata block → exit 4 (precondition)."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    with mock.patch(
        VIEW_PR_VERIFY, return_value={"body": "just a normal PR description"}
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output
    combined = r.output + (r.stderr or "")
    assert "no super-harness metadata block" in combined
    # Actionable hint points at `pr emit-opened`.
    assert "pr emit-opened" in combined


def test_verify_pr_multiple_blocks_exits_2(tmp_path: Path) -> None:
    """--pr with ≥2 metadata blocks → AC-3 violation → exit 2."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    body = _metadata_body() + "\n" + _metadata_body()
    with mock.patch(VIEW_PR_VERIFY, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "AC-3" in combined or "metadata blocks" in combined


def test_verify_pr_malformed_block_exits_2(tmp_path: Path) -> None:
    """--pr with an unclosed begin marker → structural error → exit 2."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    # BEGIN with no END → malformed (unclosed begin).
    body = (
        "Some PR description.\n\n"
        "<!-- super-harness:metadata -->\n"
        "Change: my-change\n"
        "Tier: Normal\n"
        # NO end marker.
    )
    with mock.patch(VIEW_PR_VERIFY, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "malformed" in combined


def test_verify_pr_gh_error_exits_4(tmp_path: Path) -> None:
    """--pr + gh.GhError → exit 4 (EXIT_EXTERNAL_TOOL)."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    from super_harness.engineering import gh

    with mock.patch(
        VIEW_PR_VERIFY, side_effect=gh.GhError("gh pr view 42 failed (exit 1)")
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output
    combined = r.output + (r.stderr or "")
    assert "could not fetch PR" in combined


def test_verify_positional_wins_over_pr(tmp_path: Path) -> None:
    """Positional + --pr both → positional wins; gh.view_pr is NOT called."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    with mock.patch(VIEW_PR_VERIFY) as m:
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "verify", "my-change", "--pr", "42"],
        )
    assert r.exit_code == EXIT_OK, r.output
    m.assert_not_called()


def test_verify_pr_block_missing_change_field_exits_4(tmp_path: Path) -> None:
    """Block present + balanced + single, but missing Change field → exit 4."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    body = (
        "<!-- super-harness:metadata -->\n"
        "Tier: Normal\n"
        "Verification: passed\n"
        "<!-- /super-harness:metadata -->\n"
    )
    with mock.patch(VIEW_PR_VERIFY, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output
    combined = r.output + (r.stderr or "")
    assert "Change" in combined


def test_verify_pr_invalid_slug_format_exits_2(tmp_path: Path) -> None:
    """Block present + Change field with invalid slug (slashes) → A6 gate → exit 2."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    body = (
        "<!-- super-harness:metadata -->\n"
        "Change: feature/foo\n"  # invalid: slashes break A6 gate
        "Tier: Normal\n"
        "Verification: passed\n"
        "<!-- /super-harness:metadata -->\n"
    )
    with mock.patch(VIEW_PR_VERIFY, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "42"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "feature/foo" in combined


def test_verify_pr_non_integer_exits_2(tmp_path: Path) -> None:
    """--pr value is not an integer → clean EXIT_VALIDATION (no traceback)."""
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    with mock.patch(VIEW_PR_VERIFY) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "verify", "--pr", "not-a-number"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    m.assert_not_called()
    combined = r.output + (r.stderr or "")
    assert "Traceback" not in combined


# --------------------------------------------------------------------------- #
# OPEN-ITEMS #6 / S6 — failing-verdict output is rich (per-check rows + summary
# path), NOT just the one-line `result.summary`.
#
# The summary line ("verification failed (N checks, M failed)") still goes to
# STDOUT for back-compat with anything grepping it; the new rich rows go to
# STDERR so failure detail follows convention.
# --------------------------------------------------------------------------- #


# `false` is a must_pass failure; `true` is a passing must_pass check. Together
# they prove the renderer skips passing rows AND advisory rows aren't even
# present (no must_pass=False rows in this fixture — covered by the pure-renderer
# unit test in test_verify_render.py).
_MIXED_FAIL_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
  env: {}
execution:
  mode: sequential
  max_parallelism: 1
  fail_fast: false
checks:
  - id: ok-1
    command: "true"
  - id: boom
    command: "false"
adapter_provided: []
"""


def test_verify_failure_lists_failing_must_pass_checks_on_stderr(
    tmp_path: Path,
) -> None:
    _init_workspace(tmp_path, yaml_text=_MIXED_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    # The failing must_pass check id + exit_code are on stderr.
    assert "boom" in r.stderr
    assert "exit 1" in r.stderr
    # The passing check is NOT mentioned in the failure breakdown.
    assert "ok-1" not in r.stderr


def test_verify_failure_includes_summary_path_on_stderr(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_MIXED_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "full summary:" in r.stderr
    assert ".harness/verification-results/" in r.stderr
    assert "summary.json" in r.stderr


def test_verify_failure_keeps_one_line_summary_on_stdout(tmp_path: Path) -> None:
    # Back-compat: the existing `result.summary` string ("verification failed
    # (N checks, M failed)") still lands on STDOUT so grep-style tooling
    # doesn't break.
    _init_workspace(tmp_path, yaml_text=_MIXED_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "verification failed" in r.stdout
    # And the rich rows are NOT duplicated onto stdout (stderr-only).
    assert "boom" not in r.stdout


def test_verify_failure_handles_none_output_path(tmp_path: Path) -> None:
    # With `capture: none`, the failing check's `output_path` is None — the
    # renderer must NOT leak the literal "None" string.
    _init_workspace(tmp_path, yaml_text=_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "boom" in r.stderr
    # Defensive: with `capture: none` the row has no `see:` line — the literal
    # "None" must not leak as a dangling pointer.
    assert "None" not in r.stderr


def test_verify_failure_quiet_suppresses_rich_output(tmp_path: Path) -> None:
    # --quiet suppresses BOTH the one-line summary AND the rich breakdown;
    # only the exit code communicates the verdict.
    _init_workspace(tmp_path, yaml_text=_MIXED_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--quiet", "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert r.stderr == ""
    assert r.stdout == ""


def test_verify_failure_json_mode_no_rich_text_leak(tmp_path: Path) -> None:
    # --json failure: the envelope is on stdout, NOTHING extra on stderr (the
    # rich text would corrupt machine-readable output for misconfigured CI).
    _init_workspace(tmp_path, yaml_text=_MIXED_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] == "fail"
    # No rich rendering leaks onto stderr under --json.
    assert "boom" not in r.stderr
    assert "full summary:" not in r.stderr


# --- HG-01: ${SPEC_PATH} resolves through verify for an openspec change --------

# A user check that passes ONLY when ${SPEC_PATH} resolves to a real, existing
# file. If the path wiring is broken (SPEC_PATH=""), `test -f ""` is false → the
# must_pass check fails → exit 2. So exit 0 proves the path resolved end-to-end.
_SPEC_PATH_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
  env: {}
execution:
  mode: sequential
  max_parallelism: 1
  fail_fast: false
checks:
  - id: spec-file-exists
    command: 'test -f "${SPEC_PATH}"'
adapter_provided: []
"""


def _drive_openspec_in_progress(ws: Path, slug: str) -> None:
    """Same as _drive_to_in_progress but records framework='openspec' on the change."""
    writer = EventWriter(events_path(ws))
    for evt_type in (
        "intent_declared",
        "plan_ready",
        "plan_approved",
        "implementation_started",
    ):
        writer.emit(
            Event(
                event_id=new_event_id(),
                type=evt_type,
                change_id=slug,
                timestamp="2026-06-02T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="openspec",
                payload={},
            )
        )
    refresh_state_after_emit(ws)


def test_verify_resolves_spec_path_for_openspec_change(tmp_path: Path) -> None:
    # openspec change with a real proposal.md → ${SPEC_PATH} points at it → pass.
    slug = "2026-06-02-x"
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_SPEC_PATH_YAML)
    _drive_openspec_in_progress(tmp_path, slug)
    proposal = tmp_path / "openspec" / "changes" / slug / "proposal.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("## Why\nbecause\n")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "verify", slug])
    assert r.exit_code == EXIT_OK, r.output


def test_verify_spec_path_check_fails_when_proposal_absent(tmp_path: Path) -> None:
    # Same wiring, but proposal.md absent → the resolved (real) path doesn't exist
    # → `test -f` fails → exit 2. Proves ${SPEC_PATH} is the proposal path, not "".
    slug = "2026-06-02-x"
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_SPEC_PATH_YAML)
    _drive_openspec_in_progress(tmp_path, slug)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "verify", slug])
    assert r.exit_code == EXIT_VALIDATION, r.output
