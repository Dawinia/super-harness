"""Unit tests for the SuperpowersAdapter (framework adapter).

The adapter discovers superpowers design/plan artifacts by a super-harness-owned
frontmatter marker (`change:` / `stage:`), NOT by superpowers' version-specific
paths/filenames — see docs/plans/2026-06-02-superpowers-framework-adapter-design.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.adapters.framework.superpowers import (
    SuperpowersAdapter,
    _parse_frontmatter,
)
from super_harness.core.events import Actor, Event, serialize_event
from super_harness.core.paths import events_path


def _seed_event(workspace: Path, change: str, event_type: str) -> None:
    """Append one event line to events.jsonl so observe()'s `seen` dedup sees it."""
    (workspace / ".harness").mkdir(parents=True, exist_ok=True)
    ev = Event(
        event_id="ev_seed",
        type=event_type,
        change_id=change,
        timestamp="2026-06-02T00:00:00Z",
        actor=Actor(type="adapter", identifier="superpowers-adapter"),
        framework="superpowers",
        payload={},
    )
    p = events_path(workspace)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(serialize_event(ev) + "\n")


def _write(workspace: Path, rel: str, body: str) -> Path:
    """Write `body` to `workspace/rel`, creating parents. Return the path."""
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _marked(change: str, *, stage: str | None = None, extra: str = "") -> str:
    """A minimal artifact body carrying the `change:` (and optional `stage:`) marker."""
    fm = f"change: {change}\n"
    if stage is not None:
        fm += f"stage: {stage}\n"
    fm += extra
    return f"---\n{fm}---\n# {change}\n"


class TestParseFrontmatter:
    def test_leading_block_parsed_as_mapping(self) -> None:
        text = "---\nchange: foo\nstage: plan\n---\n# Body\n"
        assert _parse_frontmatter(text) == {"change": "foo", "stage": "plan"}

    def test_no_frontmatter_returns_empty(self) -> None:
        assert _parse_frontmatter("# Just a heading\n\nprose\n") == {}

    def test_malformed_yaml_returns_empty_no_raise(self) -> None:
        # Unterminated flow mapping inside the block → YAMLError → {}.
        assert _parse_frontmatter("---\nchange: {unterminated\n---\n") == {}

    def test_non_mapping_frontmatter_returns_empty(self) -> None:
        # A YAML list as frontmatter is not a mapping → {}.
        assert _parse_frontmatter("---\n- a\n- b\n---\n") == {}

    def test_unclosed_block_returns_empty(self) -> None:
        # Opening `---` with no closing fence → treat as no frontmatter.
        assert _parse_frontmatter("---\nchange: foo\nno closing fence\n") == {}


class TestDetect:
    def test_true_for_marked_doc_in_docs_plans(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo"))
        assert SuperpowersAdapter().detect(tmp_path) is True

    def test_true_for_marked_doc_in_legacy_superpowers_dir(self, tmp_path: Path) -> None:
        # Older superpowers layout — still found, because the marker (not the
        # path) is the anchor.
        _write(tmp_path, "docs/superpowers/specs/foo.md", _marked("foo"))
        assert SuperpowersAdapter().detect(tmp_path) is True

    def test_false_when_md_has_no_change_marker(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/notes.md", "# notes\n\nno marker here\n")
        assert SuperpowersAdapter().detect(tmp_path) is False

    def test_false_when_no_candidate_dirs(self, tmp_path: Path) -> None:
        assert SuperpowersAdapter().detect(tmp_path) is False


class TestObserve:
    def _types(self, ws: Path) -> list[str]:
        return [e.type for e in SuperpowersAdapter().observe(ws)]

    def test_design_emits_intent_only(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo-design.md", _marked("foo", stage="design"))
        evs = list(SuperpowersAdapter().observe(tmp_path))
        assert [e.type for e in evs] == ["intent_declared"]
        assert evs[0].change_id == "foo"
        assert evs[0].framework == "superpowers"
        assert evs[0].actor.identifier == "superpowers-adapter"

    def test_plan_emits_intent_then_plan(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo", stage="plan"))
        assert self._types(tmp_path) == ["intent_declared", "plan_ready"]

    def test_omitted_stage_defaults_to_plan(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo"))
        assert self._types(tmp_path) == ["intent_declared", "plan_ready"]

    def test_design_plus_plan_one_change_intent_once(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo-design.md", _marked("foo", stage="design"))
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo", stage="plan"))
        assert self._types(tmp_path) == ["intent_declared", "plan_ready"]

    def test_seen_intent_not_reemitted(self, tmp_path: Path) -> None:
        _seed_event(tmp_path, "foo", "intent_declared")
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo", stage="plan"))
        # intent already on disk → only plan_ready is new.
        assert self._types(tmp_path) == ["plan_ready"]

    def test_intent_description_from_heading(self, tmp_path: Path) -> None:
        body = "---\nchange: foo\nstage: design\n---\n# My feature\n"
        _write(tmp_path, "docs/plans/2026-06-02-foo-design.md", body)
        evs = list(SuperpowersAdapter().observe(tmp_path))
        assert evs[0].payload.get("description") == "My feature"


class TestPlanPayload:
    def _plan_ready(self, ws: Path) -> Event:
        return next(e for e in SuperpowersAdapter().observe(ws) if e.type == "plan_ready")

    def test_scope_tier_into_payload(self, tmp_path: Path) -> None:
        body = (
            "---\n"
            "change: foo\n"
            "stage: plan\n"
            "scope:\n  files:\n    - src/a.py\n"
            "tier_hint: Normal\n"
            "---\n# Foo\n"
        )
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", body)
        p = self._plan_ready(tmp_path)
        assert p.payload["scope"] == {"files": ["src/a.py"]}
        assert p.payload["tier_hint"] == "Normal"

    def test_no_fields_empty_payload(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo", stage="plan"))
        assert self._plan_ready(tmp_path).payload == {}


class TestOtherAbcMethods:
    def test_get_state_raises_without_workspace(self) -> None:
        # Registry-built (cls()) has no workspace → must fail loud, not return None.
        with pytest.raises(RuntimeError):
            SuperpowersAdapter().get_state("foo")

    def test_get_state_returns_dict_for_present_change(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo-design.md", _marked("foo", stage="design"))
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo", stage="plan"))
        state = SuperpowersAdapter(workspace=tmp_path).get_state("foo")
        assert state is not None
        assert state["change_id"] == "foo"
        assert state["design"] is True
        assert state["plan"] is True

    def test_get_state_none_for_absent_change(self, tmp_path: Path) -> None:
        assert SuperpowersAdapter(workspace=tmp_path).get_state("nope") is None

    def test_spec_paths_resolves_found_artifacts(self, tmp_path: Path) -> None:
        d = _write(tmp_path, "docs/plans/2026-06-02-foo-design.md", _marked("foo", stage="design"))
        p = _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo", stage="plan"))
        paths = SuperpowersAdapter().spec_paths(tmp_path, "foo")
        assert paths["spec"] == str(d)
        assert paths["plan"] == str(p)

    def test_watch_paths_lists_existing_candidate_dirs(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo"))
        watched = SuperpowersAdapter().watch_paths(tmp_path)
        assert tmp_path / "docs" / "plans" in watched

    def test_verification_checks_empty(self) -> None:
        assert SuperpowersAdapter().verification_checks() == []

    def test_agents_md_subsection_marks_framework_no_branch_mandate(self) -> None:
        md = SuperpowersAdapter().agents_md_subsection()
        assert "framework: superpowers" in md
        assert "change:" in md  # documents the frontmatter marker convention
        # Must NOT mandate branch naming (slug is decoupled from branch).
        assert "MUST be named" not in md
