# tests/unit/cli/test_decision.py
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


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


def test_ratify_only_from_proposed(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 2  # already ratified


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
