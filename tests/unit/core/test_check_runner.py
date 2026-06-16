from super_harness.core.check_runner import CheckRun, build_sandbox, run_one_check
from super_harness.core.decisions import Counterexample


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
