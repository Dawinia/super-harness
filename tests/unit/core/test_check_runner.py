from super_harness.core.check_runner import (
    CheckFailure,
    CheckRun,
    bite_test,
    build_sandbox,
    run_executable_checks,
    run_one_check,
    select_changed,
)
from super_harness.core.decisions import Counterexample, Decision


def _ratified(did, check):
    return Decision(id=did, status="ratified", check=check,
                    counterexample=Counterexample(path="src/x.py", content="bad"))


def test_select_changed_keeps_only_touched_anchors():
    decisions = [_ratified("d-a", "true"), _ratified("d-b", "true")]
    anchor_map = {"d-a": [("src/a.py", 1)], "d-b": [("src/b.py", 1)]}
    changed = {"src/a.py"}
    ids = {d.id for d in select_changed(decisions, anchor_map, changed)}
    assert ids == {"d-a"}


def test_run_executable_checks_flags_violation(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/bad.py").write_text("pw = md5(user.password)\n")
    decisions = [_ratified("d-pw", '! grep -rIn "md5(.*password" src/')]
    failures = run_executable_checks(tmp_path, decisions)
    assert [f.id for f in failures] == ["d-pw"]
    assert isinstance(failures[0], CheckFailure)


def test_run_executable_checks_clean_is_empty(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/ok.py").write_text("clean = True\n")
    decisions = [_ratified("d-pw", '! grep -rIn "md5(.*password" src/')]
    assert run_executable_checks(tmp_path, decisions) == []


def test_run_executable_checks_sorts_failures_by_id(tmp_path):
    (tmp_path / "src").mkdir()
    decisions = [_ratified("d-b", "false"), _ratified("d-a", "false")]  # both fail, reverse order
    failures = run_executable_checks(tmp_path, decisions)
    assert [f.id for f in failures] == ["d-a", "d-b"]


def test_only_ratified_tier1_run(tmp_path):
    proposed = Decision(id="d-p", status="proposed", check="false",
                        counterexample=Counterexample("src/x.py", "b"))
    tier3 = Decision(id="d-c", status="ratified", check=None)
    assert run_executable_checks(tmp_path, [proposed, tier3]) == []


def test_changed_files_none_on_non_git(tmp_path):
    from super_harness.core.check_runner import changed_files
    assert changed_files(tmp_path) is None


def test_zero_exit_is_satisfied(tmp_path):
    r = run_one_check("true", cwd=tmp_path)
    assert isinstance(r, CheckRun)
    assert r.satisfied is True and r.exit_code == 0


def test_nonzero_exit_is_not_satisfied(tmp_path):
    r = run_one_check("false", cwd=tmp_path)
    assert r.satisfied is False and r.exit_code != 0


def test_grep_runs_in_given_cwd(tmp_path):
    (tmp_path / "f.py").write_text("pw = md5(user.password)\n")
    r = run_one_check('! grep -rIn "md5(.*password" .', cwd=tmp_path)
    assert r.satisfied is False


def test_timeout_is_not_satisfied(tmp_path):
    r = run_one_check("sleep 5", cwd=tmp_path, timeout=1)
    assert r.satisfied is False and "timeout" in r.detail.lower()


def test_non_utf8_output_is_not_satisfied(tmp_path):
    # a check emitting invalid UTF-8 must NOT raise -> fail-closed
    r = run_one_check(r"printf '\xff\xfe' >&2; exit 1", cwd=tmp_path)
    assert r.satisfied is False


def test_bad_cwd_is_not_satisfied(tmp_path):
    r = run_one_check("true", cwd=tmp_path / "nope")
    assert r.satisfied is False and r.exit_code == -1


def test_sandbox_copies_inscope_and_injects(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("clean = True\n")
    ce = Counterexample(path="src/auth/legacy.py", content="pw = md5(user.password)")
    with build_sandbox(tmp_path, ce) as sb:
        assert (sb / "src/app.py").read_text() == "clean = True\n"   # copied
        assert (sb / "src/auth/legacy.py").read_text() == "pw = md5(user.password)\n"
    assert not sb.exists()        # cleaned up on context exit


def test_sandbox_excludes_dot_dirs(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv/x.py").write_text("junk\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("ok\n")
    ce = Counterexample(path="src/bad.py", content="bad")
    with build_sandbox(tmp_path, ce) as sb:
        assert not (sb / ".venv").exists()


def test_sandbox_excludes_docs_via_exclude_glob(tmp_path):
    import subprocess

    def sh(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)

    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("ok\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/foo.md").write_text("```counterexample\nbad\n```\n")
    sh("init")
    sh("config", "user.email", "t@t")
    sh("config", "user.name", "t")
    sh("add", "-A")
    sh("commit", "-m", "x")
    ce = Counterexample(path="src/bad.py", content="bad")
    with build_sandbox(tmp_path, ce) as sb:
        assert (sb / "src/app.py").exists()          # in-scope copied
        assert not (sb / "docs/foo.md").exists()     # excluded by docs/** glob (the linchpin)


CHECK = '! grep -rIn "md5(.*password" src/'
CE = Counterexample(path="src/auth/legacy.py", content="pw = md5(user.password)")


def _clean_repo(root):
    (root / "src").mkdir()
    (root / "src/app.py").write_text("clean = True\n")


def test_bite_test_accepts_a_real_check(tmp_path):
    _clean_repo(tmp_path)
    v = bite_test(tmp_path, CHECK, CE)
    assert v.ok is True
    assert v.pass_side.satisfied is True      # clean code passes
    assert v.bite_side.satisfied is False     # counterexample makes it fail


def test_bite_test_rejects_hollow_check(tmp_path):
    _clean_repo(tmp_path)
    v = bite_test(tmp_path, "true", CE)       # always-passing -> never bites
    assert v.ok is False and "did not bite" in v.reason


def test_bite_test_rejects_check_failing_on_clean_code(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("ok\n")
    v = bite_test(tmp_path, "false", CE)
    assert v.ok is False and "current code" in v.reason


def test_bite_test_detects_pollution(tmp_path):
    _clean_repo(tmp_path)
    (tmp_path / "docs/decisions").mkdir(parents=True)
    (tmp_path / "docs/decisions/d-pw.md").write_text(
        "```counterexample\npw = md5(user.password)\n```\n"
    )
    wide = '! grep -rIn "md5(.*password" .'   # scans docs/ too
    v = bite_test(tmp_path, wide, CE)
    assert v.ok is False and "current code" in v.reason   # pass side fails on real tree
