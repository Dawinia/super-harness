# tests/unit/cli/test_decision.py
import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.decisions import compute_body_hash, parse_decision_file


def _init(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _seed_clean_src(root):
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/app.py").write_text("clean = True\n", encoding="utf-8")


TIER1 = (
    "Passwords never stored with MD5.\n\n"
    "```check\n! grep -rIn \"md5(.*password\" src/\n```\n\n"
    "```counterexample path=src/legacy.py\npw = md5(user.password)\n```\n"
)


def test_ratify_accepts_when_check_bites(tmp_path):
    root = _init(tmp_path)
    _seed_clean_src(root)
    _w(root / "docs/decisions/d-pw.md", f"---\nid: d-pw\nstatus: proposed\n---\n{TIER1}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-pw"])
    assert r.exit_code == 0, r.output
    assert parse_decision_file(root / "docs/decisions/d-pw.md").status == "ratified"


def test_reratify_tier1_rejected_when_code_now_violates(tmp_path):
    root = _init(tmp_path)
    _seed_clean_src(root)
    _w(root / "docs/decisions/d-pw.md", f"---\nid: d-pw\nstatus: proposed\n---\n{TIER1}")
    ok = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-pw"])
    assert ok.exit_code == 0
    # code now violates the check
    (root / "src/legacy.py").write_text("pw = md5(user.password)\n")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-pw"])
    assert r.exit_code == 2 and "current code" in r.output


def test_ratify_rejects_malformed_counterexample_path(tmp_path):
    root = _init(tmp_path)
    _seed_clean_src(root)
    body = ("x.\n\n```check\n! grep -rIn md5 src/\n```\n\n"
            "```counterexample path=../escape.py\nbad\n```\n")
    _w(root / "docs/decisions/d-bad.md", f"---\nid: d-bad\nstatus: proposed\n---\n{body}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-bad"])
    assert r.exit_code == 2 and "malformed" in r.output  # clean error, NOT a traceback


def test_ratify_rejects_hollow_check(tmp_path):
    root = _init(tmp_path)
    _seed_clean_src(root)
    body = "Be safe.\n\n```check\ntrue\n```\n\n```counterexample path=src/x.py\nbad\n```\n"
    _w(root / "docs/decisions/d-h.md", f"---\nid: d-h\nstatus: proposed\n---\n{body}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-h"])
    assert r.exit_code == 2
    assert "did not bite" in r.output
    assert parse_decision_file(root / "docs/decisions/d-h.md").status == "proposed"


def test_ratify_rejects_check_without_counterexample(tmp_path):
    root = _init(tmp_path)
    _seed_clean_src(root)
    body = "No md5.\n\n```check\n! grep -rIn md5 src/\n```\n"
    _w(root / "docs/decisions/d-n.md", f"---\nid: d-n\nstatus: proposed\n---\n{body}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-n"])
    assert r.exit_code == 2 and "counterexample" in r.output


def test_dry_run_does_not_change_status(tmp_path):
    root = _init(tmp_path)
    _seed_clean_src(root)
    _w(root / "docs/decisions/d-pw.md", f"---\nid: d-pw\nstatus: proposed\n---\n{TIER1}")
    r = CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "ratify", "d-pw", "--dry-run"])
    assert r.exit_code == 0 and "bites" in r.output
    assert parse_decision_file(root / "docs/decisions/d-pw.md").status == "proposed"


def test_tier3_decision_ratifies_without_bite_test(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "new", "d-c",
               "--text", "Code should be elegant."])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-c"])
    assert r.exit_code == 0


def test_new_creates_proposed(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                                  "d-auth", "--text", "Auth must be stateless."])
    assert r.exit_code == 0, r.output
    f = root / "docs/decisions/d-auth.md"
    assert f.exists()
    assert "status: proposed" in f.read_text()
    assert "Auth must be stateless." in f.read_text()


def test_new_rejects_bad_id(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                                  "d-Auth", "--text", "x"])
    assert r.exit_code == 2


def test_new_refuses_casefold_collision(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                              "d-a", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                                  "d-A", "--text", "y"])
    assert r.exit_code == 2


def test_ratify_stamps_identity_and_time(tmp_path, monkeypatch):
    root = _init(tmp_path)
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "alice@example.com")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                              "d-a", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 0, r.output
    text = (root / "docs/decisions/d-a.md").read_text()
    assert "status: ratified" in text
    assert "ratified_by: alice@example.com" in text
    assert "ratified_at:" in text


def test_ratify_missing_decision(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-x"])
    assert r.exit_code == 2


def test_ratify_rejects_superseded_and_retired(tmp_path):
    root = _init(tmp_path)
    # retired arm
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "retire", "d-a"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 2  # retired cannot be re-ratified

    # superseded arm: supersede d-old by a ratified d-new → d-old becomes superseded
    _new_ratified(root, "d-old")
    _new_ratified(root, "d-new")
    CliRunner().invoke(main, ["--workspace", str(root), "decision",
                              "supersede", "d-old", "--by", "d-new"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-old"])
    assert r.exit_code == 2  # superseded cannot be re-ratified


def test_reratify_restamps_all_three(tmp_path, monkeypatch):
    root = _init(tmp_path)
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "alice@example.com")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "v1"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    first = parse_decision_file(root / "docs/decisions/d-a.md")
    # edit the body, then re-ratify under a different actor → fresh hash + identity + time
    p = root / "docs/decisions/d-a.md"
    p.write_text(p.read_text().replace("v1", "v2"), encoding="utf-8")
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "bob@example.com")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 0, r.output
    second = parse_decision_file(p)
    assert second.ratified_text_hash == compute_body_hash("v2") != first.ratified_text_hash
    assert second.ratified_by == "bob@example.com"
    assert second.ratified_at != first.ratified_at


def _new_ratified(root, did):
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", did, "--text", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", did])


def test_supersede_links_both(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-old")
    _new_ratified(root, "d-new")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision",
                                  "supersede", "d-old", "--by", "d-new"])
    assert r.exit_code == 0, r.output
    old = (root / "docs/decisions/d-old.md").read_text()
    new = (root / "docs/decisions/d-new.md").read_text()
    assert "status: superseded" in old and "superseded_by: d-new" in old
    assert "supersedes: d-old" in new


def test_supersede_requires_ratified_successor(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-old")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-new", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision",
                                  "supersede", "d-old", "--by", "d-new"])
    assert r.exit_code == 2  # d-new not ratified


def test_retire_sets_retired(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "retire", "d-a"])
    assert r.exit_code == 0, r.output
    assert "status: retired" in (root / "docs/decisions/d-a.md").read_text()


def test_retire_missing(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "retire", "d-x"])
    assert r.exit_code == 2


def test_list_shows_status(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-b", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "list"])
    assert r.exit_code == 0
    assert "d-a" in r.output and "ratified" in r.output
    assert "d-b" in r.output and "proposed" in r.output


def test_list_filter_status(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-b", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "list",
                                  "--status", "proposed"])
    assert "d-b" in r.output and "d-a" not in r.output


def test_list_dangling_shows_down(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")  # ratified, no anchor → dangling down
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "list", "--dangling"])
    assert r.exit_code == 0 and "d-a" in r.output


def test_show_lists_fields_and_anchors(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-a\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "show", "d-a"])
    assert r.exit_code == 0, r.output
    assert "d-a" in r.output and "ratified" in r.output
    assert "src/x.py:1" in r.output


def test_show_missing(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "show", "d-x"])
    assert r.exit_code == 2


def test_check_clean_exit0(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-a\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0, r.output


def test_check_dangling_up_exit2(tmp_path):
    root = _init(tmp_path)
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-ghost\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 2
    assert "d-ghost" in r.output


def test_check_dangling_down_is_warn_exit0(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")  # ratified, no anchor
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0
    assert "d-a" in r.output  # warning surfaced


def test_check_malformed_exit3(tmp_path):
    root = _init(tmp_path)
    (root / "docs/decisions").mkdir(parents=True)
    (root / "docs/decisions/d-a.md").write_text("no frontmatter\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 3


def test_check_error_dominates_dangling_up(tmp_path):
    # precedence: a record error (3) wins over a dangling-up (2)
    root = _init(tmp_path)
    (root / "docs/decisions").mkdir(parents=True)
    (root / "docs/decisions/d-a.md").write_text("no frontmatter\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-ghost\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 3


def test_check_json_envelope(tmp_path):
    # --json is the GLOBAL flag (root position) → frozen 6-key envelope.
    root = _init(tmp_path)
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-ghost\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(r.output)
    assert payload["command"] == "decision check"
    assert payload["status"] == "fail"
    assert payload["exit_code"] == 2
    assert payload["data"]["dangling_up"] == [{"id": "d-ghost", "file": "src/x.py", "line": 1}]
    assert payload["data"]["dangling_down"] == []
    assert payload["errors"] == []


def test_ratify_stamps_text_hash(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                              "d-pw", "--text", "Passwords never stored with MD5."])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-pw"])
    assert r.exit_code == 0, r.output
    d = parse_decision_file(root / "docs/decisions/d-pw.md")
    assert d.status == "ratified"
    assert d.ratified_text_hash == compute_body_hash("Passwords never stored with MD5.")


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


TAMPERED = ("---\nid: d-pw\nstatus: ratified\nratified_by: a@b.com\n"
            "ratified_text_hash: sha256:deadbeef\n---\nClaim.\n")


def test_check_blocks_on_integrity_violation(tmp_path):
    root = _init(tmp_path)
    _w(root / "docs/decisions/d-pw.md", TAMPERED)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 2                       # EXIT_VALIDATION
    assert "INTEGRITY" in r.output


def test_check_json_lists_integrity_violations(tmp_path):
    root = _init(tmp_path)
    _w(root / "docs/decisions/d-pw.md", TAMPERED)
    r = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(r.output)
    assert payload["data"]["integrity_violations"] == [
        {"id": "d-pw", "file": "docs/decisions/d-pw.md"}
    ]
    assert payload["status"] == "fail"


UNHASHED = ("---\nid: d-old\nstatus: ratified\nratified_by: a@b.com\n"
            "---\nlegacy claim.\n")


def test_check_warns_not_blocks_on_unhashed_ratified(tmp_path):
    root = _init(tmp_path)
    _w(root / "docs/decisions/d-old.md", UNHASHED)
    # text path: warns, exit 0, mentions the id
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0, r.output
    assert "d-old" in r.output and "warning" in r.output.lower()
    # json path: status warning, key populated, not an integrity violation
    rj = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(rj.output)
    assert payload["status"] == "warning"
    assert payload["data"]["unhashed_ratified"] == ["d-old"]
    assert payload["data"]["integrity_violations"] == []


def test_text_lock_full_lifecycle(tmp_path):
    root = _init(tmp_path)
    def inv(*a):
        return CliRunner().invoke(main, ["--workspace", str(root), "decision", *a])

    # 1. author + ratify → clean
    inv("new", "d-pw", "--text", "Passwords never stored with MD5.")
    inv("ratify", "d-pw")
    assert inv("check").exit_code == 0

    # 2. tamper the claim (soften it) → check blocks
    p = root / "docs/decisions/d-pw.md"
    p.write_text(p.read_text().replace(
        "never stored with MD5", "preferably not MD5"), encoding="utf-8")
    assert inv("check").exit_code == 2

    # 3. human re-ratifies → fresh hash → clean again
    inv("ratify", "d-pw")
    assert inv("check").exit_code == 0


def _ratify_tier1(root, did="d-pw"):
    _seed_clean_src(root)
    _w(root / f"docs/decisions/{did}.md", f"---\nid: {did}\nstatus: proposed\n---\n{TIER1}")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", did])


def test_check_blocks_when_code_violates_decision(tmp_path):
    root = _init(tmp_path)
    _ratify_tier1(root)
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")   # violate
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 2
    assert "CHECK-FAILED" in r.output and "d-pw" in r.output


def test_check_green_when_code_honors_decision(tmp_path):
    root = _init(tmp_path)
    _ratify_tier1(root)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0


def test_check_json_has_failures_and_ratio(tmp_path):
    root = _init(tmp_path)
    _ratify_tier1(root)
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")
    r = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(r.output)
    assert payload["data"]["check_failures"][0]["id"] == "d-pw"
    assert payload["data"]["hard_context"] == {"hard": 1, "context": 0}
    assert payload["status"] == "fail"


def test_changed_nongit_falls_back_to_full(tmp_path):
    root = _init(tmp_path)
    _ratify_tier1(root)
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--changed"])
    assert r.exit_code == 2   # fallback-to-full caught it


def test_changed_runs_touched_anchor_and_skips_untouched(tmp_path):
    """Real git --changed: a per-file check anchored in a-file runs only when a-file
    moved; a violation committed (untouched vs HEAD) is skipped (the honest miss)."""
    import subprocess
    root = _init(tmp_path)
    (root / "src").mkdir()

    def mk(did, fname):
        body = (f"No BAD in {fname}.\n\n```check\n! grep -rIn BAD src/{fname}\n```\n\n"
                f"```counterexample path=src/{fname}\nBAD\n```\n")
        (root / f"src/{fname}").write_text(f"# @decision:{did}\nclean = True\n")
        _w(root / f"docs/decisions/{did}.md", f"---\nid: {did}\nstatus: proposed\n---\n{body}")

    def sh(*a):
        return subprocess.run(["git", *a], cwd=root, capture_output=True, check=True)

    def inv(*a):
        return CliRunner().invoke(main, ["--workspace", str(root), "decision", *a])

    mk("d-a", "a.py")
    mk("d-b", "b.py")
    sh("init")
    sh("config", "user.email", "t@t")
    sh("config", "user.name", "t")
    sh("add", "-A")
    sh("commit", "-m", "clean")
    inv("ratify", "d-a")
    inv("ratify", "d-b")
    (root / "src/b.py").write_text("# @decision:d-b\nBAD\n")
    sh("add", "src/b.py")
    sh("commit", "-m", "b")
    (root / "src/a.py").write_text("# @decision:d-a\nBAD\n")          # uncommitted -> in diff HEAD
    r = inv("check", "--changed")
    assert r.exit_code == 2 and "d-a" in r.output       # touched anchor's check ran + caught
    assert "d-b" not in r.output                         # committed (untouched vs HEAD) -> skipped
    assert "d-b" in inv("check").output                  # full run catches both


def test_check_ratio_zero_when_no_ratified(tmp_path):
    root = _init(tmp_path)
    # a proposed (not ratified) decision -> hard=0, context=0
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-x", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert "hard:context = 0:0" in r.output
    assert "% hard" not in r.output   # no percent suffix when total is zero
