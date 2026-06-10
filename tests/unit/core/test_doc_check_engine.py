import sys
from pathlib import Path

from super_harness.core import doc_check
from super_harness.core.doc_check import run_doc_check


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _reg(root: Path, entries: list[tuple[str, str]]) -> None:
    body = "derived_docs:\n" + "".join(
        f"  - path: {p}\n    command: {c}\n" for p, c in entries
    )
    _w(root / ".harness/derived-docs.yaml", body)


def _emit(text: str) -> str:
    # a generator command that prints exactly `text`
    return f'{sys.executable} -c "import sys;sys.stdout.write({text!r})"'


def test_in_sync(tmp_path):
    _w(tmp_path / "docs/a.md", "hello\n")
    _reg(tmp_path, [("docs/a.md", _emit("hello\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.in_sync] == ["docs/a.md"]
    assert r.drift == [] and r.failed == [] and r.errors == []
    assert r.exit_code == 0


def test_drift(tmp_path):
    _w(tmp_path / "docs/a.md", "stale\n")
    _reg(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.drift] == ["docs/a.md"]
    assert "fresh" in r.drift[0].diff and r.exit_code == 2


def test_missing_file_is_drift(tmp_path):
    _reg(tmp_path, [("docs/a.md", _emit("x\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.drift] == ["docs/a.md"] and r.exit_code == 2


def test_generator_nonzero_is_failed(tmp_path):
    _w(tmp_path / "docs/a.md", "x\n")
    _reg(tmp_path, [("docs/a.md", f'{sys.executable} -c "import sys;sys.exit(3)"')])
    r = run_doc_check(tmp_path)
    assert [f.path for f in r.failed] == ["docs/a.md"] and r.exit_code == 4


def test_malformed_registry_dominates(tmp_path):
    _w(tmp_path / ".harness/derived-docs.yaml", "derived_docs: 7\n")
    r = run_doc_check(tmp_path)
    assert r.exit_code == 3 and r.errors and not r.in_sync and not r.drift


def test_crlf_normalized_not_drift(tmp_path):
    _w(tmp_path / "docs/a.md", "a\nb\n")
    _reg(tmp_path, [("docs/a.md", _emit("a\r\nb\r\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.in_sync] == ["docs/a.md"]


def test_coexistence_precedence_4_over_2(tmp_path):
    _w(tmp_path / "docs/a.md", "stale\n")
    _reg(tmp_path, [("docs/a.md", _emit("fresh\n")),
                    ("docs/b.md", f'{sys.executable} -c "import sys;sys.exit(1)"')])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.drift] == ["docs/a.md"]
    assert [f.path for f in r.failed] == ["docs/b.md"]
    assert r.exit_code == 4


def test_fix_writes_drift_resolves_to_zero(tmp_path):
    _w(tmp_path / "docs/a.md", "stale\n")
    _reg(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = run_doc_check(tmp_path, fix=True)
    assert (tmp_path / "docs/a.md").read_text() == "fresh\n"
    assert r.exit_code == 0


def test_fix_does_not_write_failed(tmp_path):
    _w(tmp_path / "docs/a.md", "keep\n")
    _reg(tmp_path, [("docs/a.md", f'{sys.executable} -c "import sys;sys.exit(2)"')])
    r = run_doc_check(tmp_path, fix=True)
    assert (tmp_path / "docs/a.md").read_text() == "keep\n"   # untouched
    assert r.exit_code == 4


def test_timeout_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_check, "_GENERATOR_TIMEOUT_S", 1)
    _w(tmp_path / "docs/a.md", "x\n")
    _reg(tmp_path, [("docs/a.md", f'{sys.executable} -c "import time;time.sleep(5)"')])
    r = run_doc_check(tmp_path)
    assert [f.path for f in r.failed] == ["docs/a.md"] and r.exit_code == 4
