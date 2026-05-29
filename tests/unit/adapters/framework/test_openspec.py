from __future__ import annotations

import json
from pathlib import Path

from super_harness.adapters.framework.openspec import OpenSpecAdapter, scan_changes
from super_harness.core.events import Event, serialize_event


def _make_change(
    workspace: Path,
    slug: str,
    *,
    proposal: str | None = None,
    tasks: str | None = None,
    specs_delta: bool = False,
    archived: bool = False,
) -> Path:
    """Build a synthetic openspec change dir under workspace; return its path."""
    base = workspace / "openspec" / "changes"
    if archived:
        base = base / "archive"
    change_dir = base / slug
    change_dir.mkdir(parents=True, exist_ok=True)
    if proposal is not None:
        (change_dir / "proposal.md").write_text(proposal)
    if tasks is not None:
        (change_dir / "tasks.md").write_text(tasks)
    if specs_delta:
        deltas = change_dir / "specs" / "some-cap"
        deltas.mkdir(parents=True, exist_ok=True)
        (deltas / "spec.md").write_text("## ADDED Requirements\n")
    return change_dir


def test_detect_returns_true_when_both_dirs_exist(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    assert OpenSpecAdapter().detect(tmp_path) is True


def test_detect_returns_false_when_changes_missing(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_specs_missing(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_both_missing(tmp_path: Path) -> None:
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_changes_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "openspec").mkdir(parents=True)
    (tmp_path / "openspec" / "changes").write_text("not a dir")
    (tmp_path / "openspec" / "specs").mkdir()
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_specs_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "openspec").mkdir(parents=True)
    (tmp_path / "openspec" / "changes").mkdir()
    (tmp_path / "openspec" / "specs").write_text("not a dir")
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_for_nonexistent_workspace() -> None:
    assert OpenSpecAdapter().detect(Path("/nonexistent/workspace")) is False


def test_name_and_version() -> None:
    assert OpenSpecAdapter.name == "openspec"
    assert OpenSpecAdapter.version == "0.1.0"


def test_is_fallback_is_false() -> None:
    assert OpenSpecAdapter.is_fallback is False
    assert OpenSpecAdapter().is_fallback is False


def test_observe_yields_nothing_on_empty_workspace(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    assert list(OpenSpecAdapter().observe(tmp_path)) == []


def test_get_state_returns_none_when_change_absent(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    assert OpenSpecAdapter(workspace=tmp_path).get_state("nope") is None


# ---------------------------------------------------------------------------
# scan_changes — the pure parse-and-emit core (Task 10.2)
# ---------------------------------------------------------------------------


def test_scan_proposal_only_emits_intent_declared(tmp_path: Path) -> None:
    _make_change(tmp_path, "add-widget", proposal="# Add widget\n")
    events = scan_changes(tmp_path, seen=set())
    assert [e.type for e in events] == ["intent_declared"]
    ev = events[0]
    assert ev.change_id == "add-widget"
    assert ev.framework == "openspec"
    assert ev.actor.type == "adapter"
    assert ev.actor.identifier == "openspec-adapter"
    assert ev.framework_state is not None
    assert ev.framework_state["proposal"] is True
    assert ev.framework_state["tasks"] is False


def test_scan_tasks_added_emits_plan_ready(tmp_path: Path) -> None:
    _make_change(
        tmp_path, "add-widget", proposal="# Add widget\n", tasks="- [ ] do thing\n"
    )
    # intent_declared already seen -> only plan_ready is delta.
    events = scan_changes(tmp_path, seen={("add-widget", "intent_declared")})
    assert [e.type for e in events] == ["plan_ready"]
    ev = events[0]
    assert ev.change_id == "add-widget"
    # scope.files is intentionally NOT mined from tasks.md (category error):
    # plan_ready payload carries no scope -> reducer leaves cs.scope at default.
    assert "scope" not in ev.payload


def test_scan_both_at_once_intent_before_plan(tmp_path: Path) -> None:
    _make_change(
        tmp_path, "add-widget", proposal="# Add widget\n", tasks="- [ ] step\n"
    )
    events = scan_changes(tmp_path, seen=set())
    # Emit-time validation requires intent_declared to precede plan_ready.
    assert [e.type for e in events] == ["intent_declared", "plan_ready"]
    assert all(e.change_id == "add-widget" for e in events)


def test_scan_skips_archive_dir(tmp_path: Path) -> None:
    _make_change(
        tmp_path,
        "old-change",
        proposal="# Old\n",
        tasks="- [ ] done\n",
        archived=True,
    )
    assert scan_changes(tmp_path, seen=set()) == []


def test_scan_dedup_skips_already_seen(tmp_path: Path) -> None:
    _make_change(
        tmp_path, "add-widget", proposal="# Add widget\n", tasks="- [ ] step\n"
    )
    seen = {("add-widget", "intent_declared"), ("add-widget", "plan_ready")}
    assert scan_changes(tmp_path, seen=seen) == []


def test_scan_description_from_why_heading(tmp_path: Path) -> None:
    proposal = (
        "# Add widget\n\n"
        "## Why\n\n"
        "Users need a widget to frobnicate.\n\n"
        "## What Changes\n\n- add widget\n"
    )
    _make_change(tmp_path, "add-widget", proposal=proposal)
    ev = scan_changes(tmp_path, seen=set())[0]
    assert ev.payload["description"] == "Users need a widget to frobnicate."


def test_scan_description_falls_back_to_dirname(tmp_path: Path) -> None:
    # No `## Why` heading -> description defaults to the directory name.
    _make_change(tmp_path, "add-widget", proposal="# Add widget\n\nsome prose\n")
    ev = scan_changes(tmp_path, seen=set())[0]
    assert ev.payload["description"] == "add-widget"


def test_scan_empty_why_section_falls_back_to_dirname(tmp_path: Path) -> None:
    # `## Why` heading with no prose before the next heading -> dirname fallback.
    proposal = "## Why\n\n## What Changes\n\n- thing\n"
    _make_change(tmp_path, "add-widget", proposal=proposal)
    ev = scan_changes(tmp_path, seen=set())[0]
    assert ev.payload["description"] == "add-widget"


def test_scan_no_changes_dir_yields_nothing(tmp_path: Path) -> None:
    assert scan_changes(tmp_path, seen=set()) == []


def test_scan_multiple_changes(tmp_path: Path) -> None:
    _make_change(tmp_path, "alpha", proposal="# A\n")
    _make_change(tmp_path, "beta", proposal="# B\n", tasks="- [ ] x\n")
    events = scan_changes(tmp_path, seen=set())
    by_change: dict[str, list[str]] = {}
    for e in events:
        by_change.setdefault(e.change_id, []).append(e.type)
    assert by_change["alpha"] == ["intent_declared"]
    assert by_change["beta"] == ["intent_declared", "plan_ready"]


# ---------------------------------------------------------------------------
# observe — synchronous scan that computes `seen` from events.jsonl
# ---------------------------------------------------------------------------


def test_observe_yields_all_when_no_events_file(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    _make_change(
        tmp_path, "add-widget", proposal="# Add widget\n", tasks="- [ ] step\n"
    )
    events = list(OpenSpecAdapter().observe(tmp_path))
    assert [e.type for e in events] == ["intent_declared", "plan_ready"]


def test_observe_dedups_against_events_file(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    _make_change(
        tmp_path, "add-widget", proposal="# Add widget\n", tasks="- [ ] step\n"
    )
    # Pre-seed events.jsonl with the intent_declared so only plan_ready is new.
    harness = tmp_path / ".harness"
    harness.mkdir()
    from super_harness.core.events import Actor

    prior = Event(
        event_id="ev_x",
        type="intent_declared",
        change_id="add-widget",
        timestamp="2026-05-29T00:00:00Z",
        actor=Actor(type="adapter", identifier="openspec-adapter"),
        framework="openspec",
    )
    (harness / "events.jsonl").write_text(serialize_event(prior) + "\n")
    events = list(OpenSpecAdapter().observe(tmp_path))
    assert [e.type for e in events] == ["plan_ready"]


def test_observe_tolerates_malformed_events_line(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    _make_change(tmp_path, "add-widget", proposal="# Add widget\n")
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "events.jsonl").write_text("not json\n{bad\n")
    # Malformed lines are skipped (warn+skip, reducer policy) -> seen stays empty.
    events = list(OpenSpecAdapter().observe(tmp_path))
    assert [e.type for e in events] == ["intent_declared"]


def test_observe_does_not_write_events(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    _make_change(tmp_path, "add-widget", proposal="# Add widget\n")
    list(OpenSpecAdapter().observe(tmp_path))
    # observe is read-only: it must NOT create/append events.jsonl.
    assert not (tmp_path / ".harness" / "events.jsonl").exists()


def test_observe_emitted_events_are_schema_valid(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    _make_change(
        tmp_path, "add-widget", proposal="# Add widget\n", tasks="- [ ] step\n"
    )
    from super_harness.core.events import parse_event_line

    for ev in OpenSpecAdapter().observe(tmp_path):
        # Round-trips through serialize -> parse without EventSchemaError.
        reparsed = parse_event_line(serialize_event(ev))
        assert reparsed.change_id == "add-widget"
        assert reparsed.framework == "openspec"
        assert reparsed.actor.type == "adapter"
        assert json.loads(serialize_event(ev))["actor"]["identifier"] == "openspec-adapter"


# ---------------------------------------------------------------------------
# get_state — derive from file presence (Task 10.2)
# ---------------------------------------------------------------------------


def test_get_state_present_proposal_only(tmp_path: Path) -> None:
    _make_change(tmp_path, "add-widget", proposal="# Add widget\n")
    state = OpenSpecAdapter(workspace=tmp_path).get_state("add-widget")
    assert state is not None
    assert state["proposal"] is True
    assert state["tasks"] is False
    assert state["specs_delta"] is False
    assert state["change_id"] == "add-widget"
    assert "proposal_path" in state


def test_get_state_present_with_tasks_and_deltas(tmp_path: Path) -> None:
    _make_change(
        tmp_path,
        "add-widget",
        proposal="# Add widget\n",
        tasks="- [ ] step\n",
        specs_delta=True,
    )
    state = OpenSpecAdapter(workspace=tmp_path).get_state("add-widget")
    assert state is not None
    assert state["proposal"] is True
    assert state["tasks"] is True
    assert state["specs_delta"] is True


def test_get_state_absent_returns_none(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    assert OpenSpecAdapter(workspace=tmp_path).get_state("missing") is None


def test_get_state_raises_when_no_workspace() -> None:
    # Registry-built instances (cls()) have workspace=None. Falling back to cwd
    # would silently return None for every change (daemon chdir's to "/"), so
    # get_state must raise RuntimeError instead of silently misbehaving.
    import pytest

    with pytest.raises(RuntimeError, match=r"OpenSpecAdapter\.get_state requires a workspace"):
        OpenSpecAdapter().get_state("any-change")


# ---------------------------------------------------------------------------
# watch_paths (Task 10.2)
# ---------------------------------------------------------------------------


def test_watch_paths_points_at_changes_dir(tmp_path: Path) -> None:
    paths = OpenSpecAdapter().watch_paths(tmp_path)
    assert paths == [tmp_path / "openspec" / "changes"]


def test_verification_checks_returns_empty_list() -> None:
    assert OpenSpecAdapter().verification_checks() == []


def test_agents_md_subsection_returns_string() -> None:
    assert isinstance(OpenSpecAdapter().agents_md_subsection(), str)


def test_on_uninstall_default_noop(tmp_path: Path) -> None:
    assert OpenSpecAdapter().on_uninstall(tmp_path) is None
