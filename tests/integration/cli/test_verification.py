"""Integration tests for the `verification register` CLI (Task 10.5).

Covers the new ``super-harness verification register <adapter-name> <yaml-file>``
surface that merges externally-defined checks into ``.harness/verification.yaml``'s
``adapter_provided`` region via the SHARED conflict-checked merge helper:

- happy path: a list-shaped check file lands with provided_by stamped to the arg.
- mapping-shaped file (``checks:`` key) is also accepted.
- provided_by in the file is OVERWRITTEN by the <adapter-name> arg (authoritative).
- idempotent re-register (same adapter) → one row, replaced in place (no dup).
- conflict (same id, different provided_by already present) → EXIT_VALIDATION (2).
- no ``.harness/`` → EXIT_NO_CONFIG (3); missing/corrupt file → clean errors.
"""
from pathlib import Path

import yaml
from click.testing import CliRunner

from super_harness.cli import main


def _verification_yaml(ws: Path) -> Path:
    return ws / ".harness" / "verification.yaml"


def _adapter_provided(ws: Path) -> list[dict]:
    data = yaml.safe_load(_verification_yaml(ws).read_text()) or {}
    return data.get("adapter_provided") or []


def _write_checks_file(ws: Path, content: object) -> Path:
    p = ws / "checks.yaml"
    p.write_text(yaml.safe_dump(content))
    return p


def _run(ws: Path, *args: str):
    return CliRunner().invoke(
        main, ["--workspace", str(ws), "verification", *args]
    )


def test_register_list_file_stamps_provided_by(tmp_path: Path) -> None:
    """A list-shaped file registers each check with provided_by = <adapter-name>."""
    (tmp_path / ".harness").mkdir()
    cf = _write_checks_file(
        tmp_path,
        [{"id": "custom-lint", "command": "lint --all", "must_pass": True}],
    )

    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 0, r.output
    rows = _adapter_provided(tmp_path)
    assert [c["id"] for c in rows] == ["custom-lint"]
    assert rows[0]["provided_by"] == "my-adapter"


def test_register_mapping_file_with_checks_key(tmp_path: Path) -> None:
    """A mapping carrying a `checks:` list is also accepted."""
    (tmp_path / ".harness").mkdir()
    cf = _write_checks_file(
        tmp_path, {"checks": [{"id": "c1", "command": "run"}]}
    )

    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 0, r.output
    assert [c["id"] for c in _adapter_provided(tmp_path)] == ["c1"]


def test_register_overwrites_file_provided_by_with_arg(tmp_path: Path) -> None:
    """A provided_by written in the file is overwritten by the authoritative arg."""
    (tmp_path / ".harness").mkdir()
    cf = _write_checks_file(
        tmp_path,
        [{"id": "c1", "command": "run", "provided_by": "spoofed"}],
    )

    r = _run(tmp_path, "register", "real-owner", str(cf))

    assert r.exit_code == 0, r.output
    assert _adapter_provided(tmp_path)[0]["provided_by"] == "real-owner"


def test_register_idempotent_reregister_no_duplicate(tmp_path: Path) -> None:
    """Re-registering the SAME adapter replaces its row in place — one row, not two."""
    (tmp_path / ".harness").mkdir()
    cf = _write_checks_file(tmp_path, [{"id": "c1", "command": "v1"}])
    assert _run(tmp_path, "register", "my-adapter", str(cf)).exit_code == 0

    cf2 = _write_checks_file(tmp_path, [{"id": "c1", "command": "v2"}])
    r = _run(tmp_path, "register", "my-adapter", str(cf2))

    assert r.exit_code == 0, r.output
    rows = _adapter_provided(tmp_path)
    assert len(rows) == 1
    assert rows[0]["command"] == "v2"


def test_register_conflict_exits_validation_two(tmp_path: Path) -> None:
    """Same id owned by a DIFFERENT provided_by already present → EXIT_VALIDATION (2)."""
    (tmp_path / ".harness").mkdir()
    # Pre-seed an adapter_provided row owned by someone else.
    _verification_yaml(tmp_path).write_text(
        yaml.safe_dump(
            {
                "adapter_provided": [
                    {"id": "shared", "command": "x", "provided_by": "other-owner"}
                ]
            }
        )
    )
    cf = _write_checks_file(tmp_path, [{"id": "shared", "command": "y"}])

    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 2, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness verification register:" in r.stderr, r.stderr
    assert "shared" in r.stderr, r.stderr
    # Conflicting row untouched (rejected before any partial merge).
    assert _adapter_provided(tmp_path) == [
        {"id": "shared", "command": "x", "provided_by": "other-owner"}
    ]


def test_register_preserves_user_checks(tmp_path: Path) -> None:
    """register only touches adapter_provided; user `checks` are preserved."""
    (tmp_path / ".harness").mkdir()
    _verification_yaml(tmp_path).write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "checks": [{"id": "tests", "command": "npm test"}],
            }
        )
    )
    cf = _write_checks_file(tmp_path, [{"id": "c1", "command": "run"}])

    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 0, r.output
    data = yaml.safe_load(_verification_yaml(tmp_path).read_text())
    assert data["schema_version"] == 1
    assert [c["id"] for c in data["checks"]] == ["tests"]
    assert [c["id"] for c in data["adapter_provided"]] == ["c1"]


def test_register_no_harness_exits_no_config(tmp_path: Path) -> None:
    """No `.harness/` → EXIT_NO_CONFIG (3)."""
    cf = _write_checks_file(tmp_path, [{"id": "c1", "command": "run"}])
    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 3, r.output
    assert "super-harness verification register:" in r.stderr


def test_register_missing_file_errors_clean(tmp_path: Path) -> None:
    """A missing check file → EXIT_GENERIC (1), clean message, no traceback."""
    (tmp_path / ".harness").mkdir()
    r = _run(tmp_path, "register", "my-adapter", str(tmp_path / "nope.yaml"))

    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness verification register:" in r.stderr, r.stderr


def test_register_wrong_shape_file_errors_clean(tmp_path: Path) -> None:
    """A file that is neither a list nor a mapping-with-checks → EXIT_GENERIC (1)."""
    (tmp_path / ".harness").mkdir()
    cf = tmp_path / "checks.yaml"
    cf.write_text("just a string\n")
    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 1, r.output
    assert "super-harness verification register:" in r.stderr, r.stderr


def test_register_corrupt_verification_yaml_exits_no_config(tmp_path: Path) -> None:
    """A corrupt existing verification.yaml → EXIT_NO_CONFIG (3), clean message."""
    (tmp_path / ".harness").mkdir()
    _verification_yaml(tmp_path).write_text(":\n  - [unclosed")
    cf = _write_checks_file(tmp_path, [{"id": "c1", "command": "run"}])

    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 3, r.output
    assert "super-harness verification register:" in r.stderr, r.stderr
    assert "corrupt" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_register_non_utf8_verification_yaml_exits_no_config(tmp_path: Path) -> None:
    """A non-UTF-8 verification.yaml → EXIT_NO_CONFIG (3), clean message, no traceback.

    Regression: UnicodeDecodeError subclasses ValueError (NOT OSError), so it
    was NOT caught by the old bare ``except yaml.YAMLError``.
    """
    (tmp_path / ".harness").mkdir()
    # Write a file with an invalid UTF-8 byte so read_text() (default UTF-8) raises
    # UnicodeDecodeError before yaml ever parses it.
    _verification_yaml(tmp_path).write_bytes(
        b"adapter_provided:\n  - id: x\n\xe9\xff bad\n"
    )
    cf = _write_checks_file(tmp_path, [{"id": "c1", "command": "run"}])

    r = _run(tmp_path, "register", "my-adapter", str(cf))

    assert r.exit_code == 3, r.output
    assert "super-harness verification register:" in r.stderr, r.stderr
    assert "corrupt" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
