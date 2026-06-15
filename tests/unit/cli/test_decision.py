# tests/unit/cli/test_decision.py
import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.decisions import compute_body_hash, parse_decision_file


def _init(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


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
    inv = lambda *a: CliRunner().invoke(main, ["--workspace", str(root), "decision", *a])
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
