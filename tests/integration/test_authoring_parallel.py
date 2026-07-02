"""Integration coverage for the parallel authoring-time conformance path.

Real-time / real-subprocess tests kept out of the fast unit suite:
- the end-to-end dogfood bite (a real grep check surfaced via run_authoring_check);
- the started-then-unfinished join-timeout degradation (a ~3s real-clock test).
"""
import threading
import time
from pathlib import Path

from super_harness.core.authoring_check import _run_checks_parallel, run_authoring_check
from super_harness.core.check_runner import CheckRun
from super_harness.core.decisions import Decision, compute_body_hash


def _arm(root: Path, id: str, check: str) -> None:
    """Write a ratified, authoring_time decision with a VALID body hash so
    _integrity_ok passes and the check actually runs."""
    ddir = root / "docs" / "decisions"
    ddir.mkdir(parents=True, exist_ok=True)
    body = (
        f"Rule.\n\n```check\n{check}\n```\n\n"
        "```counterexample path=src/_ce.py\n"
        'import requests; requests.get("https://api.github.com/x")\n```\n'
    )
    h = compute_body_hash(body)
    (ddir / f"{id}.md").write_text(
        f"---\nid: {id}\nstatus: ratified\nauthoring_time: true\n"
        f"ratified_text_hash: {h}\n---\n{body}",
        encoding="utf-8",
    )


def test_end_to_end_grep_violation_and_clean(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("print('hi')\n")
    _arm(tmp_path, "d-x", r"! grep -rn 'api\.github\.com' src/")

    # clean tree -> silent
    assert run_authoring_check(tmp_path).violations == []

    # poisoned tree -> a violation on d-x, surfaced through the parallel path end to end
    (tmp_path / "src" / "bad.py").write_text(
        'requests.get("https://api.github.com/repos/o/r")\n'
    )
    v = run_authoring_check(tmp_path)
    assert [x.decision_id for x in v.violations] == ["d-x"]


def test_started_check_unfinished_degrades_to_unavailable(tmp_path: Path):
    # Covers the JOIN-TIMEOUT path: a worker that STARTS, runs, and is still unfinished
    # when the main join deadline passes -> its result is the -1 'unavailable' sentinel
    # (never a hard kill). Real clock + a generous deadline so the worker samples in
    # time and starts even under CI load; ~3s wall-clock.
    started = threading.Event()
    release = threading.Event()

    def fake(cmd, *, cwd, timeout):
        started.set()
        release.wait(timeout=10)  # "still running" until released
        return CheckRun(True, 0, "late")

    dec = Decision(id="d-slow", status="ratified", body="b", check="S")
    deadline = time.monotonic() + 3.0  # remaining ~3.0 >> _MIN_SLICE + _CLEANUP_MARGIN (1.5)
    res = _run_checks_parallel(tmp_path, [dec], deadline=deadline, run_one=fake)
    release.set()  # let the daemon worker unwind

    assert started.is_set()  # the worker actually STARTED and ran (not the never-started path)
    assert res[0][1].exit_code == -1  # unfinished at the deadline -> unavailable
