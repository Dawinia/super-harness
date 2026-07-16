from pathlib import Path

from super_harness.core.gate_blocks import GateBlockRecord, read_blocks, record_block
from super_harness.core.paths import gate_blocks_path


def test_record_block_appends_one_json_line(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    record_block(
        tmp_path, change_id="c1", state="INTENT_DECLARED",
        tool="Write", file="src/a.py", reason="plan not drafted yet",
    )
    recs = read_blocks(gate_blocks_path(tmp_path))
    assert len(recs) == 1
    r = recs[0]
    assert isinstance(r, GateBlockRecord)
    assert (r.change_id, r.state, r.tool, r.file) == (
        "c1", "INTENT_DECLARED", "Write", "src/a.py",
    )
    assert r.reason == "plan not drafted yet"
    assert r.gate == "pre-tool-use"
    assert r.ts  # ISO stamp present


def test_record_block_appends_not_overwrites(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    record_block(tmp_path, change_id="c1", state="INTENT_DECLARED",
                 tool="Edit", file="a.py", reason="x")
    record_block(tmp_path, change_id="c1", state="INTENT_DECLARED",
                 tool="Edit", file="b.py", reason="x")
    assert len(read_blocks(gate_blocks_path(tmp_path))) == 2


def test_record_block_never_raises_when_dir_missing(tmp_path: Path) -> None:
    # No .harness/ dir → open('a') would raise; must be swallowed, not propagated.
    record_block(tmp_path, change_id="c1", state="INTENT_DECLARED",
                 tool="Write", file="a.py", reason="x")
    assert read_blocks(gate_blocks_path(tmp_path)) == []


def test_record_block_never_raises_when_path_unwritable(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".harness").mkdir()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    # Must not raise.
    record_block(tmp_path, change_id="c1", state="INTENT_DECLARED",
                 tool="Write", file="a.py", reason="x")


def test_read_blocks_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_blocks(gate_blocks_path(tmp_path)) == []


def test_read_blocks_skips_malformed_lines(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    p = gate_blocks_path(tmp_path)
    p.write_text(
        '{"ts":"2026-07-16T00:00:00Z","change_id":"c1","state":"S","tool":"Write",'
        '"file":"a.py","reason":"r","gate":"pre-tool-use"}\n'
        "not json\n"
        "[1,2,3]\n"                                    # non-object
        '{"ts":"x"}\n'                                 # missing required fields
        '{"ts":"2026-07-16T00:00:01Z","change_id":"c2","state":"S","tool":"Edit",'
        '"file":null,"reason":"r","gate":"g"}\n',
        encoding="utf-8",
    )
    recs = read_blocks(p)
    assert [r.change_id for r in recs] == ["c1", "c2"]
    assert recs[1].file is None  # null file preserved as None
