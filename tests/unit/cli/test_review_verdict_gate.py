"""Emit-time verdict teeth for `review approve --reviewer code-reviewer`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.paths import events_path
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _repo_change(tmp_path: Path) -> Path:
    from super_harness.core.events import Actor, Event
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    for t, p in [("intent_declared", {}), ("plan_ready", {"scope": {"files": ["src/"]}}),
                 ("plan_approved", {}), ("implementation_started", {}),
                 ("verification_passed", {}), ("implementation_complete", {})]:
        EventWriter(events_path(tmp_path)).emit(Event(
            event_id=new_event_id(), type=t, change_id="c",
            timestamp="2026-06-23T00:00:00Z",
            actor=Actor(type="human", identifier="cli"), framework="plain", payload=p))
    refresh_state_after_emit(tmp_path)
    return tmp_path


def _good_verdict(ws: Path, digest: str) -> Path:
    p = ws / "verdict.yaml"
    items = "\n".join(f"  - item: {i}\n    status: pass"
                      for i in ["spec-compliance", "scope-adherence", "code-quality",
                                "edge-cases", "doc-impact"])
    p.write_text(f"bundle_digest: {digest}\nchecklist:\n{items}\nfindings: []\n")
    return p


def _set_independent_policy(ws: Path, *, min_independent: int = 2) -> None:
    (ws / ".harness" / "policy.yaml").write_text(
        "reviewers:\n"
        "  sources: [subagent, external]\n"
        "  code-reviewer:\n"
        f"    min_independent: {min_independent}\n"
    )


def _prepare_digest(ws: Path) -> str:
    r = CliRunner().invoke(main, ["--json", "--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    return json.loads(r.output)["data"]["bundle_digest"]


def test_bare_approve_rejected(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "verdict" in r.output.lower()


def test_incomplete_checklist_rejected(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    p = ws / "v.yaml"
    p.write_text(
        f"bundle_digest: {digest}\nchecklist:\n"
        "  - item: spec-compliance\n    status: pass\nfindings: []\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "scope-adherence" in r.output  # names a missing item


def test_stale_digest_rejected(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    _prepare_digest(ws)
    p = _good_verdict(ws, "stale-does-not-match")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "stale" in r.output.lower() or "digest" in r.output.lower()


def test_complete_fresh_verdict_passes_and_inlines(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output
    # verdict inlined into the emitted event payload
    last = [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()][-1]
    assert last["type"] == "code_review_passed"
    assert last["payload"]["verdict"]["bundle_digest"] == digest


def test_independent_code_review_first_source_records_partial_only(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    _set_independent_policy(ws)
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "approve", "c", "--reviewer", "code-reviewer",
         "--verdict-file", str(p), "--source", "subagent"],
    )
    assert r.exit_code == EXIT_OK, r.output
    last = [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()][-1]
    assert last["type"] == "review_verdict_recorded"
    assert last["payload"]["source"] == "subagent"


def test_independent_code_review_second_source_emits_milestone(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    _set_independent_policy(ws)
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    first = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "approve", "c", "--reviewer", "code-reviewer",
         "--verdict-file", str(p), "--source", "subagent"],
    )
    assert first.exit_code == EXIT_OK, first.output
    second = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "approve", "c", "--reviewer", "code-reviewer",
         "--verdict-file", str(p), "--source", "external"],
    )
    assert second.exit_code == EXIT_OK, second.output
    events = [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()]
    assert [e["type"] for e in events[-2:]] == ["review_verdict_recorded", "code_review_passed"]
    assert events[-1]["payload"]["independent_sources"] == ["external", "subagent"]


def test_independent_code_review_stale_partial_does_not_count_after_reject(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    _set_independent_policy(ws)
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    first = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "approve", "c", "--reviewer", "code-reviewer",
         "--verdict-file", str(p), "--source", "subagent"],
    )
    assert first.exit_code == EXIT_OK, first.output
    _to_rejected(ws)
    p2 = _verdict_with_prior(
        ws, digest, "prior_findings:\n  - id: f-001\n    disposition: resolved\n")
    second = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "approve", "c", "--reviewer", "code-reviewer",
         "--verdict-file", str(p2), "--source", "external"],
    )
    assert second.exit_code == EXIT_OK, second.output
    events = [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()]
    assert events[-1]["type"] == "review_verdict_recorded"
    assert not any(e["type"] == "code_review_passed" for e in events)


def _to_rejected(ws: Path, finding_id: str = "f-001") -> None:
    """Drive c from AWAITING_CODE_REVIEW into CODE_REVIEW_REJECTED with one finding."""
    from super_harness.core.events import Actor, Event
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    EventWriter(events_path(ws)).emit(Event(
        event_id=new_event_id(), type="code_review_failed", change_id="c",
        timestamp="2026-06-23T00:01:00Z",
        actor=Actor(type="human", identifier="cli"), framework="plain",
        payload={"verdict": {"findings": [
            {"id": finding_id, "severity": "blocker", "file": "src/a.py", "summary": "boom"}]}}))
    refresh_state_after_emit(ws)


def _verdict_with_prior(ws: Path, digest: str, prior: str) -> Path:
    p = ws / "v_prior.yaml"
    items = "\n".join(f"  - item: {i}\n    status: pass"
                      for i in ["spec-compliance", "scope-adherence", "code-quality",
                                "edge-cases", "doc-impact"])
    p.write_text(f"bundle_digest: {digest}\nchecklist:\n{items}\nfindings: []\n{prior}")
    return p


def test_approve_from_rejected_blocks_undisposed_finding(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    _to_rejected(ws)
    p = _good_verdict(ws, digest)  # no prior_findings → f-001 undisposed
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "f-001" in r.output


def test_approve_from_rejected_passes_when_disposed(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    _to_rejected(ws)
    p = _verdict_with_prior(
        ws, digest, "prior_findings:\n  - id: f-001\n    disposition: resolved\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output


def test_approve_from_awaiting_does_not_require_prior(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)  # state AWAITING_CODE_REVIEW, no reject
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output  # D inert from AWAITING_CODE_REVIEW


def test_dead_doc_ref_blocks_code_review_approve(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    # commit a doc with a backtick symbol that resolves nowhere in source scope
    (ws / "docs").mkdir()
    (ws / "docs" / "guide.md").write_text("the old `_totally_gone` helper is removed\n")
    _git(ws, "add", "docs/guide.md")
    _git(ws, "commit", "-qm", "doc")
    digest = _prepare_digest(ws)   # digest is over in-scope src/ diff, unaffected by the doc
    p = _good_verdict(ws, digest)  # full verdict → only the dead ref can fail it
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "_totally_gone" in r.output    # blocked specifically on the dead ref


def test_clean_docs_allow_code_review_approve(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    (ws / "docs").mkdir()
    (ws / "docs" / "guide.md").write_text("see `src` for details\n")  # no code-shaped symbol
    _git(ws, "add", "docs/guide.md")
    _git(ws, "commit", "-qm", "doc")
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output


def _fail_verdict(ws: Path, digest: str) -> Path:
    """Full-coverage verdict whose `code-quality` item is FAIL (+ required finding)."""
    p = ws / "fail-verdict.yaml"
    items = []
    for i in ["spec-compliance", "scope-adherence", "code-quality", "edge-cases", "doc-impact"]:
        status = "fail" if i == "code-quality" else "pass"
        items.append(f"  - item: {i}\n    status: {status}")
    p.write_text(
        f"bundle_digest: {digest}\nchecklist:\n" + "\n".join(items)
        + "\nfindings:\n  - id: f1\n    severity: major\n    file: src/a.py\n"
          "    summary: broken thing\n")
    return p


def test_approve_rejected_when_checklist_has_fail(tmp_path: Path) -> None:
    # F1 (review 2026-07-02): a verdict whose own checklist says FAIL must not
    # be recordable as code_review_passed.
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    p = _fail_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "code-quality" in r.output          # names the failing item
    assert "review reject" in r.output         # points at the honest verb


def test_reject_still_accepts_fail_verdict(tmp_path: Path) -> None:
    # The same verdict shape must stay VALID for `review reject`.
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    p = _fail_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "reject", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output
