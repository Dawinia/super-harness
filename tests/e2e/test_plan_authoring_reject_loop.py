"""E2E: the PLAN_REJECTED reject-loop needs no shell bypass (HG-PLAN-AUTHORING).

Drives the real `super-harness-hook` binary (positional + Claude shim) against a
seeded PLAN_REJECTED change whose plan artifact is recorded. Editing the artifact
through the normal edit-tool path is ALLOWed; editing a source file is BLOCKed. This
is the honest live proof — it does NOT claim this change proved it on its own plan
phase (see the design's Bootstrap disclosure).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _seed(root: Path) -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(
        "changes:\n  c1:\n    change_id: c1\n    current_state: PLAN_REJECTED\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n"
        "    plan_artifacts:\n      - docs/plans/c.md\n",
        encoding="utf-8",
    )
    (root / "docs/plans").mkdir(parents=True)
    (root / "docs/plans/c.md").write_text("---\nchange: c1\n---\n# plan\n")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("x = 1\n")


def _hook(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["super-harness-hook", *args], cwd=str(root), input=stdin,
        capture_output=True, text=True,
    )


def test_positional_allows_artifact_blocks_source(tmp_path: Path) -> None:
    _seed(tmp_path)
    artifact = str(tmp_path / "docs/plans/c.md")
    source = str(tmp_path / "src/x.py")
    assert _hook(tmp_path, "Write", artifact).returncode == 0  # ALLOW
    assert _hook(tmp_path, "Write", source).returncode == 1  # BLOCK


def test_claude_shim_allows_artifact_blocks_source(tmp_path: Path) -> None:
    _seed(tmp_path)

    def payload(path: str) -> str:
        return json.dumps({"tool_name": "Write", "tool_input": {"file_path": path}})

    allow = _hook(tmp_path, "--agent", "claude-code",
                  stdin=payload(str(tmp_path / "docs/plans/c.md")))
    block = _hook(tmp_path, "--agent", "claude-code",
                  stdin=payload(str(tmp_path / "src/x.py")))
    assert allow.returncode == 0  # Claude shim: 0 = allow
    assert block.returncode == 2  # Claude shim: 2 = block
