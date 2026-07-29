"""End-to-end through the real hook entry point: plan-path config on disk →
allow/block (design 2026-07-29). Drives the installed `super-harness-hook`
console script as a real subprocess, matching the convention in
`test_hook_entry.py` — the click-less binary IS the hot path these tests
exercise, not an import of `main()` in-process.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml


def _hook(root: Path, tool: str, file: str) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": file}})
    proc = subprocess.run(
        ["super-harness-hook", "--agent", "claude-code"],
        cwd=str(root), input=payload, capture_output=True, text=True,
    )
    return proc.returncode


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / ".harness" / "state.yaml").write_text(
        yaml.safe_dump({
            "changes": {
                "my-change": {
                    "change_id": "my-change",
                    "current_state": "INTENT_DECLARED",
                    "last_event_at": "2026-07-29T00:00:00Z",
                    "plan_artifacts": [],
                }
            }
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_default_config_allows_docs_plans(repo: Path) -> None:
    assert _hook(repo, "Write", "docs/plans/2026-07-29-my-change-design.md") == 0


def test_default_config_still_blocks_source(repo: Path) -> None:
    assert _hook(repo, "Edit", "src/api.py") == 2


def test_corrupt_config_fails_closed(repo: Path) -> None:
    (repo / ".harness" / "plan-paths.yaml").write_text("plan_paths: [oops\n", encoding="utf-8")
    assert _hook(repo, "Write", "docs/plans/2026-07-29-my-change-design.md") == 2


def test_scratch_area_allowed(repo: Path) -> None:
    (repo / ".harness" / "scratch" / "my-change").mkdir(parents=True)
    assert _hook(repo, "Write", ".harness/scratch/my-change/notes.md") == 0


def test_kill_switch_path_still_blocked(repo: Path) -> None:
    assert _hook(repo, "Write", ".harness/gate-disabled") == 2
