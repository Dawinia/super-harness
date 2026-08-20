"""Cross-platform contracts for the workspace sentinel lock interface."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from super_harness.core.file_lock import exclusive_file_lock


def test_exclusive_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    sentinel = tmp_path / "nested" / ".test.lock"

    with exclusive_file_lock(sentinel):
        assert sentinel.exists()
    assert sentinel.read_bytes() == b"\0"
    with exclusive_file_lock(sentinel):
        assert sentinel.exists()
    assert sentinel.read_bytes() == b"\0"


def test_exclusive_lock_blocks_another_process(tmp_path: Path) -> None:
    sentinel = tmp_path / ".process.lock"
    ready = tmp_path / "child-ready"
    acquired = tmp_path / "child-acquired"
    package_src = Path(__file__).resolve().parents[3] / "src"
    child = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from super_harness.core.file_lock import exclusive_file_lock

sentinel, ready, acquired = map(Path, sys.argv[2:])
ready.touch()
with exclusive_file_lock(sentinel):
    acquired.touch()
"""

    with exclusive_file_lock(sentinel):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                child,
                str(package_src),
                str(sentinel),
                str(ready),
                str(acquired),
            ],
            env={**os.environ},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child did not reach the lock attempt"
        assert process.poll() is None, process.stderr.read() if process.stderr else ""
        assert not acquired.exists()

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr or stdout
    assert acquired.exists()
