import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def _init(tmp_path: Path):
    return CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])


def test_change_start_emits_intent_declared(tmp_path: Path):
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "change",
            "start",
            "2026-05-27-add-foo",
            "--description",
            "Add foo",
        ],
    )
    assert r.exit_code == 0
    events_file = tmp_path / ".harness" / "events.jsonl"
    assert events_file.exists()
    line = events_file.read_text().splitlines()[0]
    assert json.loads(line)["type"] == "intent_declared"
    assert json.loads(line)["change_id"] == "2026-05-27-add-foo"


def test_change_start_rejects_invalid_slug(tmp_path: Path):
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "start", "Has Spaces"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION


def test_change_list_shows_active(tmp_path: Path):
    _init(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])
    runner.invoke(main, ["--workspace", str(tmp_path), "state", "rebuild"])
    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "change", "list"],
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert any(c["change_id"] == "ch1" for c in payload["data"]["changes"])


def test_change_list_rejects_invalid_state(tmp_path: Path):
    """Fix I-2: --state must be one of the 11 canonical (uppercase) states.

    Before this fix, `--state intent_declared` silently matched nothing and
    returned exit 0 — a typo bug user couldn't see. click.Choice rejects with
    UsageError → exit 2 (which conveniently matches our EXIT_VALIDATION).
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "list", "--state", "intent_declared"],
    )
    assert r.exit_code == 2  # Click's UsageError exit code == EXIT_VALIDATION


def test_change_abandon_rejects_invalid_slug(tmp_path: Path):
    """Fix I-3: `abandon` validates slug symmetrically with `start`.

    Without this, `change abandon "Has Spaces"` falls through to the writer
    and surfaces a lifecycle-state-rule error the user can't act on.
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "abandon", "Has Spaces"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION


def test_change_list_rejects_conflicting_state_flags(tmp_path: Path):
    """Fix I-4 option (a): --active / --archived / --abandoned are mutex.

    Combining them always yields an empty set; reject at parse time with an
    actionable message instead of silently returning [].
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "list", "--active", "--archived"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION
    assert "mutually exclusive" in r.stderr


# -------------------- Task 2.5: `change resume` --------------------
#
# Coverage map:
# - test_change_resume_text                — plan-prescribed: markdown contains
#                                            slug + state literal "INTENT_DECLARED"
# - test_change_resume_unknown_slug        — unknown slug → EXIT_VALIDATION + hint
# - test_change_resume_no_harness          — no .harness/ → EXIT_NO_CONFIG (3)
# - test_change_resume_json_envelope_shape — --json keys: change_id /
#                                            current_state / recent_events /
#                                            pending_sensors=[]
# - test_change_resume_recent_events_chronological_and_capped
#                                          — chronological (oldest→newest) +
#                                            capped at 20 + filtered by slug
# - test_change_resume_empty_scope_renders_none
#                                          — scope=={} → markdown renders
#                                            "(none)" placeholder, not "{}"


def test_change_resume_text(tmp_path: Path):
    """Plan-prescribed test (slug c1 → ch1 fix, matching Task 2.3 convention).

    The plan's verbatim test uses slug "c1" which fails Task 2.2's min-length-3
    slug validation. We use "ch1" — same fix Task 2.3 applied. Plan bug, not
    a Task 2.5 deviation.
    """
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])
    r = runner.invoke(main, ["--workspace", str(tmp_path), "change", "resume", "ch1"])
    assert r.exit_code == 0
    assert "INTENT_DECLARED" in r.output
    assert "ch1" in r.output


def test_change_resume_rejects_invalid_slug(tmp_path: Path):
    """Symmetric with start/abandon: bad slug → EXIT_VALIDATION at command entry.

    Without this fix, an invalid slug fell through to the "unknown change" error
    path (also exit 2, but with a misleading message that points to
    `change list` instead of slug-rules).
    """
    _init(tmp_path)
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "change", "resume", "Has Spaces",
    ])
    assert r.exit_code == 2  # EXIT_VALIDATION
    # Verify it's the SLUG error path, not the "unknown change" path
    assert "cli-command-surface §2.3" in r.stderr


def test_change_resume_unknown_slug(tmp_path: Path):
    """Unknown slug → exit 2 (validation) with an actionable error.

    Different semantics from `status`/`change list` (which return empty + exit 0):
    resume's purpose is to dump context FOR THIS SLUG. If the slug doesn't
    exist, there's no context to dump — that's a user error, not an empty
    query result.
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "resume", "ch-missing"],
    )
    assert r.exit_code == 2
    assert "ch-missing" in r.stderr


def test_change_resume_no_harness(tmp_path: Path):
    """No `.harness/` → HarnessNotInitialized → exit 3 (EXIT_NO_CONFIG)."""
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "resume", "ch1"],
    )
    assert r.exit_code == 3


def test_change_resume_json_envelope_shape(tmp_path: Path):
    """`--json` returns the standard envelope with the documented context keys."""
    _init(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])
    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "change", "resume", "ch1"],
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["command"] == "change resume"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["errors"] == []
    data = payload["data"]
    assert data["change_id"] == "ch1"
    assert data["current_state"] == "INTENT_DECLARED"
    # scope is dict (per Task 2.4 review finding); empty at INTENT_DECLARED.
    assert data["scope"] == {}
    # v0.1 contract: pending_sensors is always [] — Phase 8+ wires sensor backlog.
    assert data["pending_sensors"] == []
    # recent_events: list of event dicts for this slug, in chronological order.
    assert isinstance(data["recent_events"], list)
    assert len(data["recent_events"]) == 1
    assert data["recent_events"][0]["type"] == "intent_declared"
    assert data["recent_events"][0]["change_id"] == "ch1"


def test_change_resume_recent_events_chronological_and_capped(tmp_path: Path):
    """Recent events: filtered by slug, chronological (oldest→newest), max 20.

    Strategy: write 25 raw events for slug "ch1" + 5 noise events for "other"
    directly to events.jsonl (bypasses lifecycle validation — we're testing the
    tailer, not the writer). Verify resume returns exactly 20 events, all for
    "ch1", in append order.
    """
    _init(tmp_path)
    runner = CliRunner()
    # We need ch1 to exist in derived state so `resume ch1` doesn't 404. The
    # cleanest way is to start it once via the CLI, then append raw events.
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])

    events_file = tmp_path / ".harness" / "events.jsonl"
    # Append 24 more "intent_redeclared" events for ch1 (legal repeat per
    # transitions table) — we just need ANY known event for the tail count.
    extra_lines = []
    for i in range(24):
        extra_lines.append(json.dumps({
            "event_id": f"01H000000000000000000000{i:02d}",
            "type": "intent_redeclared",
            "change_id": "ch1",
            "timestamp": f"2026-05-27T10:{i:02d}:00Z",
            "actor": {"type": "human", "identifier": "test"},
            "framework": "plain",
            "payload": {"reason": f"redo-{i}"},
        }))
    # 5 noise events for a different change — must NOT appear in resume output.
    for i in range(5):
        extra_lines.append(json.dumps({
            "event_id": f"01H000000000000000000099{i:01d}",
            "type": "intent_declared",
            "change_id": "other",
            "timestamp": f"2026-05-27T11:{i:02d}:00Z",
            "actor": {"type": "human", "identifier": "test"},
            "framework": "plain",
            "payload": {},
        }))
    with events_file.open("a") as f:
        f.write("\n".join(extra_lines) + "\n")

    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "change", "resume", "ch1"],
    )
    assert r.exit_code == 0
    recent = json.loads(r.output)["data"]["recent_events"]
    # Capped at 20.
    assert len(recent) == 20
    # All for ch1 — no noise from "other".
    assert all(e["change_id"] == "ch1" for e in recent)
    # Chronological: oldest first. The 25 ch1 events were appended in order
    # (intent_declared first, then redeclared 0..23). After tailing to 20,
    # we expect redeclared 4..23 (oldest of the kept slice = "redo-4").
    assert recent[0]["payload"]["reason"] == "redo-4"
    assert recent[-1]["payload"]["reason"] == "redo-23"


def test_change_resume_empty_scope_renders_none(tmp_path: Path):
    """A change at INTENT_DECLARED has scope=={}; markdown renders '(none)'.

    Guards against `repr(dict)` regression — empty dict must not appear as `{}`
    in the human-facing text. Phase 3+ will populate scope from `plan_ready`.
    """
    _init(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])
    r = runner.invoke(
        main, ["--workspace", str(tmp_path), "change", "resume", "ch1"]
    )
    assert r.exit_code == 0
    assert "## Scope" in r.output
    assert "(none)" in r.output
    # Defensive: empty dict literal must not leak into human output.
    assert "{}" not in r.output


def test_change_resume_scope_with_newlines_escapes_safely(tmp_path: Path):
    """Scope values containing newlines must not break the Markdown bullet structure.

    Phase 3 `plan_ready` events populate `cs.scope` from agent-authored payload
    (description, rationale, etc.). A multi-line value naively rendered as
    `f"- {key}: {value}"` would physically break the bullet list:
        - rationale: line1
        line2                <- now a continuation, not a bullet
    and corrupt any downstream Markdown parser (the inject_context consumer in
    adapter-architecture §3.5). `_render_resume_markdown` defends by using
    `repr(value)` so the newline becomes the literal escape `\\n` inside a
    quoted Python string, preserving one-bullet-per-key.

    Setup: start ch1 via CLI (so it exists in derived state + transitions table
    is happy), then append a raw `plan_ready` event with a multi-line scope
    value directly to events.jsonl (same pattern as
    test_change_resume_recent_events_chronological_and_capped — bypasses
    emit-time validation which we're not testing here).
    """
    _init(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])

    events_file = tmp_path / ".harness" / "events.jsonl"
    plan_ready_event = json.dumps({
        "event_id": "01H000000000000000000PLAN1",
        "type": "plan_ready",
        "change_id": "ch1",
        "timestamp": "2026-05-27T12:00:00Z",
        "actor": {"type": "agent", "identifier": "test-agent"},
        "framework": "plain",
        "payload": {
            "scope": {
                "rationale": "line1\nline2",
                "files": ["a.py", "b.py"],
            },
            "tier_hint": "normal",
        },
    })
    with events_file.open("a") as f:
        f.write(plan_ready_event + "\n")

    r = runner.invoke(
        main, ["--workspace", str(tmp_path), "change", "resume", "ch1"]
    )
    assert r.exit_code == 0
    # The bullet line for `rationale` must be exactly one line — the raw newline
    # inside the value must NOT have been emitted into the output.
    scope_section = r.output.split("## Scope", 1)[1]
    rationale_lines = [ln for ln in scope_section.splitlines() if "rationale" in ln]
    assert len(rationale_lines) == 1, (
        f"rationale should occupy exactly one bullet line; got {rationale_lines!r}"
    )
    # The literal escape sequence `\n` (two characters: backslash + n) must
    # appear inside the quoted repr — that's how repr() renders a real newline.
    assert "\\n" in rationale_lines[0], (
        f"expected repr-escaped newline in {rationale_lines[0]!r}"
    )
    # Defensive: a bare "line2" continuation line (the corruption symptom) must
    # NOT exist as its own line in the scope section.
    assert not any(
        ln.strip() == "line2" for ln in scope_section.splitlines()
    ), "multi-line scope value leaked as a continuation line — escaping regressed"
