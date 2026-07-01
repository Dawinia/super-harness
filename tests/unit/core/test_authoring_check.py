from pathlib import Path
import textwrap

from super_harness.core.authoring_check import (
    Verdict,
    Violation,
    _to_violations,
    run_authoring_check,
)
from super_harness.core.check_runner import CheckFailure


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


def test_unavailable_is_not_a_violation():
    # `unavailable` = timeout/spawn (runner returns exit_code -1) OR the check TOOL is
    # missing/not-executable (shell exits 126/127 — e.g. `lint-imports` not installed).
    # A real nonzero (e.g. 1) IS a violation. Assert the filter directly.
    fails = [
        CheckFailure(id="d-timeout", exit_code=-1, detail="timeout"),
        CheckFailure(id="d-missing", exit_code=127, detail="lint-imports: not found"),
        CheckFailure(id="d-b", exit_code=1, detail="real"),
    ]
    ids = [v.decision_id for v in _to_violations(fails)]
    assert ids == ["d-b"]  # -1 (timeout/spawn) and 126/127 (tool missing) filtered out


def test_integrity_violated_decision_is_skipped(tmp_path: Path):
    # A ratified, opted-in decision whose body no longer matches its ratified hash must
    # NOT have its arbitrary shell check run in the interactive loop (design §4 trust).
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


def test_verdict_and_violation_are_plain_data():
    v = Verdict(violations=[Violation("d", "detail", "docs/decisions/d.md")])
    assert v.violations[0].decision_id == "d"
