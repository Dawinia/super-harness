"""`plan ready` records marked-`.md` plan artifacts from the declared scope.

The recorder is ungameable for source: only a scope entry that is `.md` BEFORE and
AFTER canonicalization (so a symlink to a `.py` is rejected) and whose frontmatter
`change:` matches the slug is recorded. Asserted end-to-end via the derived
`ChangeState.plan_artifacts` (reducer already wired).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter


def _seed_intent(root: Path, slug: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    ev = Event(
        event_id=new_event_id(), type="intent_declared", change_id=slug,
        actor=Actor(type="human", identifier="t"), framework="plain",
        timestamp="2026-07-16T00:00:00Z", payload={"description": "d"},
    )
    EventWriter(events_path(root)).emit(ev, skip_validation=True)


def _plan_artifacts(root: Path, slug: str) -> list[str]:
    return derive_state(events_path(root))[slug].plan_artifacts


@pytest.mark.skipif(sys.platform == "win32", reason="symlink perms differ on Windows")
def test_records_only_marked_md_from_scope(tmp_path: Path) -> None:
    _seed_intent(tmp_path, "c")
    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    # (a) marked .md → recorded
    (tmp_path / "docs/plans/c.md").write_text("---\nchange: c\n---\n# plan\n")
    # (b) source .py with a fake frontmatter header → NOT .md → excluded
    (tmp_path / "src/x.py").write_text("---\nchange: c\n---\n")
    # (c) evil.md symlink → a marked source file → excluded by post-resolution .md guard
    (tmp_path / "src/evil.py").write_text("---\nchange: c\n---\n")
    (tmp_path / "docs/plans/evil.md").symlink_to(tmp_path / "src/evil.py")
    # (d) unmarked .md → excluded (frontmatter change != slug)
    (tmp_path / "docs/plans/other.md").write_text("---\nchange: zzz\n---\n")

    scope = "[docs/plans/c.md, src/x.py, docs/plans/evil.md, docs/plans/other.md]"
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c", "--scope", scope],
    )
    assert r.exit_code == 0, r.output
    assert _plan_artifacts(tmp_path, "c") == ["docs/plans/c.md"]


def test_no_scope_records_nothing(tmp_path: Path) -> None:
    _seed_intent(tmp_path, "c")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "plan", "ready", "c"]
    )
    assert r.exit_code == 0, r.output
    assert _plan_artifacts(tmp_path, "c") == []


def test_uppercase_md_extension_recorded(tmp_path: Path) -> None:
    _seed_intent(tmp_path, "c")
    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "docs/plans/Design.MD").write_text("---\nchange: c\n---\n")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c",
         "--scope", "[docs/plans/Design.MD]"],
    )
    assert r.exit_code == 0, r.output
    assert _plan_artifacts(tmp_path, "c") == ["docs/plans/Design.MD"]
