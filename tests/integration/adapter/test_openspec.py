"""Integration test for the OpenSpec FrameworkAdapter wiring (Task 10.4).

Drives the REAL CLI surface end-to-end against a temp workspace that looks like
an openspec project:

  1. ``super-harness init``                  → scaffolds ``.harness/`` + AGENTS.md
  2. ``super-harness adapter install openspec``
                                             → registers the framework adapter +
                                                injects the AGENTS.md subsection
  3. ``super-harness adapter scan-once openspec``
                                             → one synchronous ``observe()`` pass
                                                that emits unseen lifecycle events

Asserts:
- the openspec adapter is now resolvable through the registry (install succeeds);
- a real ``openspec/changes/foo/proposal.md`` yields an ``intent_declared`` event
  for ``foo`` in ``events.jsonl``;
- the repo-root ``AGENTS.md`` gained the
  ``<!-- super-harness framework: openspec -->`` subsection;
- a change under ``openspec/changes/archive/<x>/`` emits NOTHING (archived
  changes are skipped by ``scan_changes``).

⚠ Like the sibling ``tests/integration/adapter/test_claude_code.py`` this SPAWNS
nothing extra but DOES drive the real ``super-harness`` CLI through ``CliRunner``;
no ``shutil.which`` mock is needed for a FRAMEWORK adapter (no hook binaries).
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main

_OPENSPEC_BEGIN = "<!-- super-harness framework: openspec -->"
_OPENSPEC_END = "<!-- /super-harness framework: openspec -->"


def _init(ws: Path):
    return CliRunner().invoke(main, ["--workspace", str(ws), "init"])


def _install(ws: Path):
    return CliRunner().invoke(
        main, ["--workspace", str(ws), "adapter", "install", "openspec"]
    )


def _scan_once(ws: Path):
    return CliRunner().invoke(
        main, ["--workspace", str(ws), "adapter", "scan-once", "openspec"]
    )


def _events(ws: Path) -> list[dict]:
    path = ws / ".harness" / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _make_openspec_workspace(ws: Path) -> None:
    """Lay down a minimal-but-real openspec tree so ``detect()`` passes.

    openspec@1.3.1 detection requires BOTH ``openspec/changes/`` and
    ``openspec/specs/``. ``foo`` has a proposal (→ intent_declared); the archived
    change must NOT re-emit.
    """
    changes = ws / "openspec" / "changes"
    (changes / "foo").mkdir(parents=True)
    (changes / "foo" / "proposal.md").write_text(
        "## Why\nBecause foo needs doing.\n", encoding="utf-8"
    )
    # specs/ presence is part of the detect() heuristic.
    (ws / "openspec" / "specs").mkdir(parents=True)
    # An ARCHIVED change — scan_changes skips the whole archive subdir.
    (changes / "archive" / "bar").mkdir(parents=True)
    (changes / "archive" / "bar" / "proposal.md").write_text(
        "## Why\nAlready archived.\n", encoding="utf-8"
    )


def test_install_then_scan_once_emits_intent_and_injects_agents_md(
    tmp_path: Path,
) -> None:
    """init → install openspec → scan-once: intent_declared for `foo` + AGENTS.md
    subsection; archived change emits nothing."""
    _make_openspec_workspace(tmp_path)

    assert _init(tmp_path).exit_code == 0

    install_result = _install(tmp_path)
    assert install_result.exit_code == 0, install_result.output

    # AGENTS.md gained the openspec framework subsection.
    agents = (tmp_path / "AGENTS.md").read_text()
    assert _OPENSPEC_BEGIN in agents
    assert _OPENSPEC_END in agents

    scan_result = _scan_once(tmp_path)
    assert scan_result.exit_code == 0, scan_result.output

    events = _events(tmp_path)
    # `foo` got an intent_declared (real proposal.md present).
    foo_intents = [
        e
        for e in events
        if e["change_id"] == "foo" and e["type"] == "intent_declared"
    ]
    assert len(foo_intents) == 1, events
    assert foo_intents[0]["framework"] == "openspec"

    # The archived `bar` change emitted NOTHING (archive subdir skipped).
    assert not any(e["change_id"] == "bar" for e in events), events


def test_scan_once_no_new_events_is_a_noop_success(tmp_path: Path) -> None:
    """A second scan-once re-finds only already-seen artifacts → exit 0, no dup."""
    _make_openspec_workspace(tmp_path)
    assert _init(tmp_path).exit_code == 0
    assert _install(tmp_path).exit_code == 0

    assert _scan_once(tmp_path).exit_code == 0
    first = _events(tmp_path)

    # Second scan: `foo`'s intent_declared is already in events.jsonl → skipped.
    second_result = _scan_once(tmp_path)
    assert second_result.exit_code == 0, second_result.output
    assert _events(tmp_path) == first, "second scan must not re-emit seen events"
