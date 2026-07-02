import textwrap
import threading
import time
from pathlib import Path

from super_harness.core.authoring_check import (
    _CLEANUP_MARGIN,
    _MIN_SLICE,
    Verdict,
    Violation,
    _per_check,
    _run_checks_parallel,
    _to_verdict,
    run_authoring_check,
)
from super_harness.core.check_runner import CheckRun
from super_harness.core.decisions import Decision


def _write_decision(root: Path, id: str, check: str, authoring: bool):
    d = root / "docs" / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    at = "authoring_time: true\n" if authoring else ""
    # Build at column 0 — do NOT dedent an interpolated conditional line (the inserted
    # `\n` collapses the common-prefix and leaves the frontmatter malformed).
    (d / f"{id}.md").write_text(
        f"---\nid: {id}\nstatus: ratified\n{at}---\n"
        f"body\n```check\n{check}\n```\n"
        f"```counterexample path=src/_ce.py\nx = 1\n```\n"
    )


def _dec(id: str, check: str = "true") -> Decision:
    return Decision(id=id, status="ratified", body="b", check=check)


# --- end-to-end via run_authoring_check (real subprocess checks) -------------------

def test_failing_opted_in_check_is_a_violation(tmp_path: Path):
    _write_decision(tmp_path, "d-fail", check="false", authoring=True)
    v = run_authoring_check(tmp_path)
    assert [x.decision_id for x in v.violations] == ["d-fail"]
    assert v.violations[0].decision_doc_path == "docs/decisions/d-fail.md"


def test_not_opted_in_is_skipped(tmp_path: Path):
    _write_decision(tmp_path, "d-fail", check="false", authoring=False)
    assert run_authoring_check(tmp_path).violations == []


def test_passing_check_is_clean(tmp_path: Path):
    _write_decision(tmp_path, "d-ok", check="true", authoring=True)
    assert run_authoring_check(tmp_path).violations == []


def test_integrity_violated_decision_is_skipped(tmp_path: Path):
    # A ratified, opted-in decision whose body no longer matches its ratified hash must
    # NOT have its arbitrary shell check run in the interactive loop (trust control).
    d = tmp_path / "docs" / "decisions"
    d.mkdir(parents=True)
    (d / "d-tampered.md").write_text(
        textwrap.dedent("""\
        ---
        id: d-tampered
        status: ratified
        authoring_time: true
        ratified_text_hash: sha256:deadbeef
        ---
        tampered body
        ```check
        false
        ```
        ```counterexample path=src/_ce.py
        x = 1
        ```
        """)
    )
    assert run_authoring_check(tmp_path).violations == []


def test_core_module_is_adapter_free():
    import super_harness.core.authoring_check as m

    src = Path(m.__file__).read_text()
    assert "adapters" not in src  # core must not import the adapter layer (core-is-base)


# --- Verdict shape ----------------------------------------------------------------

def test_verdict_and_violation_are_plain_data():
    v = Verdict(violations=[Violation("d", "detail", "docs/decisions/d.md")])
    assert v.violations[0].decision_id == "d"


def test_verdict_unavailable_defaults_empty():
    assert Verdict(violations=[]).unavailable == []


def test_verdict_carries_unavailable():
    assert Verdict(violations=[], unavailable=["d-slow"]).unavailable == ["d-slow"]


# --- _to_verdict classification ---------------------------------------------------

def test_to_verdict_splits_violations_and_unavailable():
    # satisfied -> nothing; -1 (timeout/spawn) and 127 (tool missing) -> unavailable;
    # a real nonzero (1) -> violation. Sorted by id.
    results = [
        (_dec("d-ok"), CheckRun(True, 0, "ok")),
        (_dec("d-timeout"), CheckRun(False, -1, "timeout")),
        (_dec("d-missing"), CheckRun(False, 127, "not found")),
        (_dec("d-real"), CheckRun(False, 1, "boom")),
    ]
    v = _to_verdict(results)
    assert [x.decision_id for x in v.violations] == ["d-real"]
    assert v.unavailable == ["d-missing", "d-timeout"]


# --- _per_check pure policy (no clock / no threads) -------------------------------

def test_per_check_pure():
    # comfortable budget -> remaining - margin
    assert _per_check(10.0, 0.0) == 10.0 - _CLEANUP_MARGIN
    # just enough (remaining just above min_slice + margin) -> a positive slice
    now = 10.0 - (_MIN_SLICE + _CLEANUP_MARGIN) - 0.01
    assert _per_check(10.0, now) == (10.0 - now) - _CLEANUP_MARGIN
    # exactly at the threshold -> None (not enough to run + clean up)
    assert _per_check(10.0, 10.0 - (_MIN_SLICE + _CLEANUP_MARGIN)) is None
    # past the deadline -> None
    assert _per_check(10.0, 11.0) is None


# --- orchestrator concurrency / boundedness / fail-open ---------------------------

def test_checks_run_concurrently(tmp_path: Path):
    # TRUE simultaneity gauge: a serialized regression keeps peak == 1 and fails loudly
    # (never hangs — the Barrier times out). No wall-clock timing assertion.
    n = 3
    barrier = threading.Barrier(n, timeout=3)
    cur = {"n": 0, "peak": 0}
    lk = threading.Lock()

    def fake(cmd, *, cwd, timeout):
        with lk:
            cur["n"] += 1
            cur["peak"] = max(cur["peak"], cur["n"])
        try:
            barrier.wait()
        finally:
            with lk:
                cur["n"] -= 1
        return CheckRun(True, 0, "ok")

    decs = [_dec(f"d-{i}", check=f"C{i}") for i in range(n)]
    _run_checks_parallel(tmp_path, decs, deadline=time.monotonic() + 10, run_one=fake)
    assert cur["peak"] == n


def test_startup_stops_when_already_expired(tmp_path: Path):
    # Spawn guard: clock is already past the deadline, so the loop breaks on iteration 1
    # and NO worker ever runs (hence no worker/main clock race). Every runnable decision
    # is still represented, all unavailable.
    called = {"n": 0}

    def fake(cmd, *, cwd, timeout):
        called["n"] += 1
        return CheckRun(True, 0, "ok")

    decs = [_dec(f"d-{i}", check=f"C{i}") for i in range(5)]
    res = _run_checks_parallel(tmp_path, decs, deadline=1.0, run_one=fake, clock=lambda: 100.0)
    assert called["n"] == 0
    assert len(res) == 5
    assert all(r.exit_code == -1 for _, r in res)


def test_setup_failure_degrades_observably(tmp_path: Path, monkeypatch, capsys):
    # An unexpected orchestration error must fail open to an all-unavailable verdict
    # (never crash the agent's Stop) AND emit a stderr diagnostic (observable in logs).
    import super_harness.core.authoring_check as m

    _write_decision(tmp_path, "d-armed", check="true", authoring=True)

    def boom(*a, **k):
        raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(m, "_run_checks_parallel", boom)
    v = run_authoring_check(tmp_path)
    assert v.violations == []
    assert v.unavailable == ["d-armed"]
    assert "failed open" in capsys.readouterr().err
