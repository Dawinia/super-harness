"""Integration tests for ``super-harness pr emit-opened`` (Phase 13 Task 13.7).

``pr emit-opened`` is the CI-side ``pr_opened`` emitter that fires the
PR-decorator workflow. Symmetric with Task 13.6's ``on-merge`` but simpler:
``--change`` is always explicit (CI knows ``head_ref`` = branch = slug,
VISION convention), and only one sensor (``PRDecorator``) is dispatched.

Coverage map (per the brief):

Wiring:
  1. test_emits_pr_opened_event
  2. test_dispatches_pr_decorator
  3. test_data_schema_pass_path

Pre-flight + failure paths:
  4. test_no_harness_dir_exits_3
  5. test_gh_failure_exits_4
  6. test_emit_precondition_error_exits_1
     (natural path — ``pr_opened`` on a change with NO prior events fires
     ``EmitPreconditionError`` because ``current_state`` is None and
     transitions reject any non-``intent_declared`` first event.)

Format:
  7. test_human_mode_summary_to_stdout

Idempotency:
  8. test_re_emit_idempotent_pr_decorator_replaces

Registration:
  9. test_pr_emit_opened_registered_under_pr_group

gh is ALWAYS mocked at the PR-decorator import site (Phase 12 lesson:
mock at the dispatched sensor's import site, since the dispatcher only
transitively calls gh through the sensor).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)
from super_harness.cli.pr import pr_group
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.gh import GhError
from super_harness.engineering.pr_metadata import METADATA_BEGIN, METADATA_END

# Mock at the SENSOR import site (Phase 12 pattern): the dispatcher
# transitively calls these through PRDecorator.check().
VIEW_PR = "super_harness.sensors.pr_decorator.view_pr"
EDIT_PR_BODY = "super_harness.sensors.pr_decorator.edit_pr_body"


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


CHANGE_ID = "2026-05-30-add-foo"
PR_NUMBER = 42


def _evt(change_id: str, evt_type: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(
        event_id=new_event_id(),
        type=evt_type,
        change_id=change_id,
        timestamp="2026-05-30T10:00:00Z",
        actor=Actor(type="human", identifier="cli"),
        framework="plain",
        payload=payload or {},
    )


def _seed_intent_declared(root: Path, change_id: str) -> None:
    """Minimal seed so ``pr_opened`` is a legal informational emit.

    ``pr_opened`` is in the ``_INFORMATIONAL`` set (transitions.py) which means
    it never changes state — but the transition table still rejects ANY
    non-``intent_declared`` event when ``current_state is None``. So we need
    at least one ``intent_declared`` on the change before ``pr_opened`` is legal.
    """
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    writer = EventWriter(events_path(root))
    writer.emit(_evt(change_id, "intent_declared", {"description": "x"}))


def _read_events(root: Path) -> list[dict[str, Any]]:
    if not events_path(root).exists():
        return []
    return [
        json.loads(line)
        for line in events_path(root).read_text().splitlines()
        if line.strip()
    ]


def _read_event_types(root: Path) -> list[str]:
    return [e["type"] for e in _read_events(root)]


# --------------------------------------------------------------------------- #
# 1. Wiring — emits a pr_opened event
# --------------------------------------------------------------------------- #


def test_emits_pr_opened_event(tmp_path: Path) -> None:
    """Happy path emits a ``pr_opened`` event with the right shape."""
    _seed_intent_declared(tmp_path, CHANGE_ID)

    with (
        mock.patch(VIEW_PR, return_value={"body": ""}),
        mock.patch(EDIT_PR_BODY),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    pr_opened_events = [e for e in _read_events(tmp_path) if e["type"] == "pr_opened"]
    assert len(pr_opened_events) == 1
    ev = pr_opened_events[0]
    assert ev["change_id"] == CHANGE_ID
    assert ev["payload"].get("pr_number") == PR_NUMBER
    assert ev["actor"]["type"] == "ci"
    # Distinct from on-merge's identifier so events from the two CI legs are
    # distinguishable in events.jsonl.
    assert ev["actor"]["identifier"] == "pr-emit-opened"


# --------------------------------------------------------------------------- #
# 2. Wiring — dispatches PR-decorator (edit_pr_body called once, body has block)
# --------------------------------------------------------------------------- #


def test_dispatches_pr_decorator(tmp_path: Path) -> None:
    """``edit_pr_body`` gets called once with a body containing the metadata block
    and the change_id.
    """
    _seed_intent_declared(tmp_path, CHANGE_ID)

    with (
        mock.patch(VIEW_PR, return_value={"body": "original body text"}),
        mock.patch(EDIT_PR_BODY) as m_edit,
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    m_edit.assert_called_once()
    _, edit_args, edit_kwargs = m_edit.mock_calls[0]
    # Signature: edit_pr_body(pr_number, body) — accept positional or kwargs.
    pr_arg = edit_args[0] if edit_args else edit_kwargs.get("pr_number")
    body_arg = edit_args[1] if len(edit_args) >= 2 else edit_kwargs.get("body")
    assert pr_arg == PR_NUMBER
    assert METADATA_BEGIN in body_arg
    assert METADATA_END in body_arg
    assert CHANGE_ID in body_arg


# --------------------------------------------------------------------------- #
# 3. Frozen data schema on pass path (v0.1 non-frozen — internal command)
# --------------------------------------------------------------------------- #


def test_data_schema_pass_path(tmp_path: Path) -> None:
    """``--json`` pass envelope carries the documented (non-frozen v0.1) data shape."""
    _seed_intent_declared(tmp_path, CHANGE_ID)

    with (
        mock.patch(VIEW_PR, return_value={"body": ""}),
        mock.patch(EDIT_PR_BODY),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    payload = json.loads(r.stdout)
    assert payload["command"] == "pr emit-opened"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    data = payload["data"]
    assert data["pr_number"] == PR_NUMBER
    assert data["change_id"] == CHANGE_ID
    assert data["events_emitted"] == ["pr_opened"]
    assert data["sensors_triggered"] == ["PR-decorator"]


# --------------------------------------------------------------------------- #
# 4. Pre-flight — no .harness/ → exit 3, NO envelope on --json
# --------------------------------------------------------------------------- #


def test_no_harness_dir_exits_3(tmp_path: Path) -> None:
    """Missing ``.harness/`` → exit 3 + format_error to stderr; no JSON envelope."""
    r = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "--json",
            "pr",
            "emit-opened",
            "--pr",
            str(PR_NUMBER),
            "--change",
            CHANGE_ID,
        ],
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = (r.stderr or "") + r.output
    assert "No .harness/" in combined or "init" in combined
    # No JSON envelope on stdout even under --json (verify / on-merge pattern).
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# 5. gh failure → exit 4 (PR-decorator crashed via dispatcher's _safe_run)
# --------------------------------------------------------------------------- #


def test_gh_failure_exits_4(tmp_path: Path) -> None:
    """``view_pr`` raising ``GhError`` → PRDecorator crashes → dispatcher emits
    ``sensor_crashed`` + returns empty results → CLI translates to exit 4.

    The ``pr_opened`` event was emitted BEFORE dispatch, so it remains in
    events.jsonl alongside the ``sensor_crashed`` event.
    """
    _seed_intent_declared(tmp_path, CHANGE_ID)

    with (
        mock.patch(VIEW_PR, side_effect=GhError("network down")),
        mock.patch(EDIT_PR_BODY),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output + (r.stderr or "")
    combined = (r.stderr or "") + r.output
    # Stderr mentions something gh-related ("gh"/"PR-decorator"/"network").
    assert "gh" in combined or "PR-decorator" in combined or "network" in combined
    # No JSON envelope on stdout even under --json (3/4 do not emit one).
    assert r.stdout.strip() == ""

    # The pr_opened event was emitted FIRST before dispatch — it stays.
    types = _read_event_types(tmp_path)
    assert "pr_opened" in types
    # sensor_crashed was emitted by the dispatcher's _safe_run.
    assert "sensor_crashed" in types


# --------------------------------------------------------------------------- #
# 6. EmitPreconditionError → exit 1 (natural path: no prior events for the change)
# --------------------------------------------------------------------------- #


def test_emit_precondition_error_exits_1(tmp_path: Path) -> None:
    """Change has NO prior events → ``current_state`` is None → ``pr_opened`` is
    illegal as a first event (transitions table: only ``intent_declared`` may be
    first). EmitPreconditionError → exit 1 + format_error, NO envelope.
    """
    # .harness/ exists so we get PAST the exit-3 path; but NO seed events on
    # this change_id, so emit-time validation rejects pr_opened.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)

    with (
        mock.patch(VIEW_PR, return_value={"body": ""}),
        mock.patch(EDIT_PR_BODY),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                "nonexistent-change",
            ],
        )

    assert r.exit_code == EXIT_GENERIC, r.output + (r.stderr or "")
    combined = (r.stderr or "") + r.output
    assert "pr_opened" in combined or "illegal" in combined
    # NO JSON envelope on stdout (1/3/4 do not emit one).
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# 7. Format — human mode summary
# --------------------------------------------------------------------------- #


def test_human_mode_summary_to_stdout(tmp_path: Path) -> None:
    """No ``--json``, happy path → one-line summary mentioning the PR + change_id."""
    _seed_intent_declared(tmp_path, CHANGE_ID)

    with (
        mock.patch(VIEW_PR, return_value={"body": ""}),
        mock.patch(EDIT_PR_BODY),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    assert CHANGE_ID in r.stdout
    assert str(PR_NUMBER) in r.stdout
    # Not JSON.
    assert not r.stdout.lstrip().startswith("{")


# --------------------------------------------------------------------------- #
# 8. Idempotency — re-emit replaces the existing block (1→1, not 1→2)
# --------------------------------------------------------------------------- #


def test_re_emit_idempotent_pr_decorator_replaces(tmp_path: Path) -> None:
    """Two consecutive invocations: the second sees the body the first wrote
    (block already present) and ``edit_pr_body`` is still called with exactly
    one block — the 1→1 replace path of ``_merge_metadata_block``.
    """
    _seed_intent_declared(tmp_path, CHANGE_ID)

    # Simulate gh state across invocations: view_pr returns whatever
    # edit_pr_body was last called with.
    _pr_body_state: dict[int, str] = {PR_NUMBER: "original body"}

    def fake_view(pr_number: int, fields: list[str] | None = None) -> dict[str, Any]:
        return {"body": _pr_body_state[pr_number]}

    def fake_edit(pr_number: int, body: str) -> None:
        _pr_body_state[pr_number] = body

    with (
        mock.patch(VIEW_PR, side_effect=fake_view),
        mock.patch(EDIT_PR_BODY, side_effect=fake_edit) as m_edit,
    ):
        # First invocation: 0 blocks → append.
        r1 = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )
        assert r1.exit_code == EXIT_OK, r1.output + (r1.stderr or "")
        first_body = _pr_body_state[PR_NUMBER]
        assert first_body.count(METADATA_BEGIN) == 1
        assert first_body.count(METADATA_END) == 1

        # Second invocation: 1 block → replace in-place.
        r2 = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "pr",
                "emit-opened",
                "--pr",
                str(PR_NUMBER),
                "--change",
                CHANGE_ID,
            ],
        )
        assert r2.exit_code == EXIT_OK, r2.output + (r2.stderr or "")
        second_body = _pr_body_state[PR_NUMBER]
        # Still exactly one block — replace, not append.
        assert second_body.count(METADATA_BEGIN) == 1
        assert second_body.count(METADATA_END) == 1

    # edit_pr_body was called once per invocation (total 2).
    assert m_edit.call_count == 2


# --------------------------------------------------------------------------- #
# 9. Registration — emit-opened is a subcommand of pr_group
# --------------------------------------------------------------------------- #


def test_pr_emit_opened_registered_under_pr_group() -> None:
    """``pr emit-opened`` must be discoverable via the ``pr`` click group."""
    assert "emit-opened" in pr_group.commands
