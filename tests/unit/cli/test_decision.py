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
