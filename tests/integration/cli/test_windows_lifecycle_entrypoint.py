"""Native-Windows lifecycle reachability through the installed console script."""

from __future__ import annotations

import json
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows")


def _run(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    executable = Path(sysconfig.get_path("scripts")) / "super-harness.exe"
    assert executable.exists()
    return subprocess.run(
        [executable, "--workspace", str(workspace), *args],
        input="",
        text=True,
        capture_output=True,
        check=False,
    )


def test_installed_entrypoint_drives_first_lifecycle_transitions(tmp_path: Path) -> None:
    """Regression: lifecycle imports and writes must work without POSIX ``fcntl``."""
    workspace = tmp_path / "external windows project"
    workspace.mkdir()

    initialized = _run(workspace, "init", "--no-agent")
    assert initialized.returncode == 0, initialized.stderr or initialized.stdout

    started = _run(workspace, "change", "start", "windows-lifecycle-smoke")
    assert started.returncode == 0, started.stderr or started.stdout

    ready = _run(workspace, "plan", "ready", "windows-lifecycle-smoke")
    assert ready.returncode == 0, ready.stderr or ready.stdout

    event_objects = [
        json.loads(line)
        for line in (workspace / ".harness" / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert [event["type"] for event in event_objects] == [
        "intent_declared",
        "plan_ready",
    ]

    state = yaml.safe_load(
        (workspace / ".harness" / "state.yaml").read_text(encoding="utf-8")
    )
    assert (
        state["changes"]["windows-lifecycle-smoke"]["current_state"]
        == "AWAITING_PLAN_REVIEW"
    )
