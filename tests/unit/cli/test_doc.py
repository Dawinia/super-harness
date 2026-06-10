# tests/unit/cli/test_doc.py
import json
import sys
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def _ws(tmp_path: Path, entries: list[tuple[str, str]]) -> Path:
    (tmp_path / ".harness").mkdir()
    body = "derived_docs:\n" + "".join(
        f"  - path: {p}\n    command: {c}\n" for p, c in entries)
    (tmp_path / ".harness/derived-docs.yaml").write_text(body)
    return tmp_path


def _emit(text: str) -> str:
    return f'{sys.executable} -c "import sys;sys.stdout.write({text!r})"'


def test_check_in_sync_json(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("x\n")
    _ws(tmp_path, [("docs/a.md", _emit("x\n"))])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 0, r.output
    env = json.loads(r.output)
    assert env["command"] == "doc check" and env["status"] == "pass"
    assert env["data"]["in_sync"] == ["docs/a.md"]
    assert env["data"]["drift"] == []
    assert env["data"]["failed"] == []


def test_check_in_sync_non_json_exit0(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("x\n")
    _ws(tmp_path, [("docs/a.md", _emit("x\n"))])
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 0, r.output
    assert "clean" in r.output


def test_check_drift_exit_2(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("stale\n")
    _ws(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 2


def test_check_drift_json(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("stale\n")
    _ws(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 2
    env = json.loads(r.output)
    assert env["command"] == "doc check" and env["status"] == "fail"
    assert [d["path"] for d in env["data"]["drift"]] == ["docs/a.md"]


def test_fix_writes_and_exits_0(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("stale\n")
    _ws(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "doc", "check", "--fix"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "docs/a.md").read_text() == "fresh\n"
    assert "fixed" in r.output


def test_malformed_registry_exit_3(tmp_path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness/derived-docs.yaml").write_text("derived_docs: 7\n")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 3


def test_malformed_registry_json(tmp_path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness/derived-docs.yaml").write_text("derived_docs: 7\n")
    r = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 3
    env = json.loads(r.output)
    assert env["status"] == "fail"
    assert env["errors"] and env["errors"][0]["code"] == "malformed_registry"


def test_no_harness_exit_3(tmp_path):
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 3


def test_failed_bucket_json(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("x\n")
    _ws(tmp_path, [("docs/a.md", f'{sys.executable} -c "import sys;sys.exit(7)"')])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 4
    env = json.loads(r.output)
    assert env["status"] == "fail"
    assert env["data"]["failed"][0]["path"] == "docs/a.md"
    assert env["data"]["failed"][0]["error"] == "exit 7"
    assert "docs/a.md" in [f["path"] for f in env["data"]["failed"]]
