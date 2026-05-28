"""Integration tests for the generalized `adapter` CLI (Task 6.4).

Covers the registry-driven ``install`` / ``uninstall`` / ``list`` surface that
generalizes the Phase-5 minimal ``adapter install claude-code``. This file is
ADDITIVE — it coexists with ``test_adapter_install.py`` (the Phase-5 claude-code
tests that must stay green); it does NOT re-assert that file's claude-code
happy-path mechanics, only the NEW behaviour:

- ``install plain`` (framework): adapters.yaml entry, no verification.yaml /
  AGENTS.md side effects.
- ``install claude-code`` (agent): settings.json hook AND adapters.yaml entry.
- ``install <unknown>``: EXIT_GENERIC (1), NOT click's exit 2; no adapters.yaml.
- idempotent re-install: no duplicate entry.
- ``install`` with no ``.harness/``: EXIT_NO_CONFIG (3).
- ``uninstall``: removes the entry, ``--quiet`` skips the confirm; not-installed
  → non-zero with a clear message, no crash.
- ``list``: installed-only (uninstalled built-ins absent), ``--type`` /
  ``--enabled-only`` filters, ``--json`` envelope, framework capabilities
  degrade gracefully.

``shutil.which`` is monkeypatched (as in ``test_adapter_install.py``) so the
real ``super-harness-hook`` binary need not be on PATH for the agent path.
"""
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from super_harness.cli import main

_FAKE_HOOK = "/usr/local/bin/super-harness-hook"


def _adapters_yaml(ws: Path) -> Path:
    return ws / ".harness" / "adapters.yaml"


def _verification_yaml(ws: Path) -> Path:
    return ws / ".harness" / "verification.yaml"


def _agents_md(ws: Path) -> Path:
    return ws / "AGENTS.md"


def _settings(ws: Path) -> Path:
    return ws / ".claude" / "settings.json"


def _entries(ws: Path) -> list[dict]:
    data = yaml.safe_load(_adapters_yaml(ws).read_text()) or {}
    return data.get("adapters") or []


def _run(ws: Path, *args: str, input_text: str | None = None):
    return CliRunner().invoke(
        main, ["--workspace", str(ws), "adapter", *args], input=input_text
    )


# --- install ----------------------------------------------------------------


def test_install_plain_writes_framework_entry_no_side_effects(tmp_path: Path) -> None:
    """`install plain` → exit 0; framework/builtin entry; no verification/AGENTS.md."""
    (tmp_path / ".harness").mkdir()
    r = _run(tmp_path, "install", "plain")

    assert r.exit_code == 0, r.output
    entries = _entries(tmp_path)
    assert entries == [
        {
            "name": "plain",
            "type": "framework",
            "builtin": True,
            "version": "0.1.0",
            "enabled": True,
        }
    ]
    # verification.yaml is NOT touched (empty-safe no-op), AGENTS.md not written.
    assert not _verification_yaml(tmp_path).exists()
    assert not _agents_md(tmp_path).exists()


def test_install_claude_code_writes_hook_and_agent_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`install claude-code` → settings.json hook AND an agent adapters.yaml entry."""
    import shutil

    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    r = _run(tmp_path, "install", "claude-code")

    assert r.exit_code == 0, r.output
    # Phase-5 hook still registered.
    assert _settings(tmp_path).exists()
    settings = json.loads(_settings(tmp_path).read_text())
    commands = [
        h["command"]
        for entry in settings["hooks"]["PreToolUse"]
        for h in entry["hooks"]
    ]
    assert commands == [f"{_FAKE_HOOK} --agent claude-code"]
    # New: adapters.yaml records it as an agent built-in.
    assert _entries(tmp_path) == [
        {
            "name": "claude-code",
            "type": "agent",
            "builtin": True,
            "version": "0.1.0",
            "enabled": True,
        }
    ]
    # verification.yaml still untouched (claude-code contributes no checks).
    assert not _verification_yaml(tmp_path).exists()


def test_install_unknown_exits_generic_not_two(tmp_path: Path) -> None:
    """`install <unknown>` → EXIT_GENERIC (1), NOT click's exit 2; no adapters.yaml."""
    (tmp_path / ".harness").mkdir()
    r = _run(tmp_path, "install", "no-such-adapter")

    assert r.exit_code == 1, r.output
    assert "super-harness adapter install:" in r.stderr
    assert "unknown adapter" in r.stderr
    assert not _adapters_yaml(tmp_path).exists()


def test_install_is_idempotent_no_duplicate_entry(tmp_path: Path) -> None:
    """Re-installing `plain` rewrites the entry in place — never appends a dup."""
    (tmp_path / ".harness").mkdir()
    first = _run(tmp_path, "install", "plain")
    second = _run(tmp_path, "install", "plain")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert len(_entries(tmp_path)) == 1


def test_install_no_harness_exits_no_config(tmp_path: Path) -> None:
    """No `.harness/` → EXIT_NO_CONFIG (3)."""
    r = _run(tmp_path, "install", "plain")

    assert r.exit_code == 3, r.output
    assert "super-harness adapter install:" in r.stderr
    assert "Hint:" in r.stderr


def test_install_writes_header_comment_on_create(tmp_path: Path) -> None:
    """Creating adapters.yaml writes the AUTO-MANAGED header comment."""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")

    text = _adapters_yaml(tmp_path).read_text()
    assert "AUTO-MANAGED by super-harness" in text


# --- uninstall ---------------------------------------------------------------


def test_uninstall_quiet_removes_entry_skips_confirm(tmp_path: Path) -> None:
    """`uninstall --quiet plain` removes the entry without prompting."""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")
    assert len(_entries(tmp_path)) == 1

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "plain"],
    )

    assert r.exit_code == 0, r.output
    # Entry gone, file left with an empty adapters list (not deleted).
    assert _entries(tmp_path) == []


def test_uninstall_interactive_confirm_yes(tmp_path: Path) -> None:
    """Without --quiet, a `y` confirmation proceeds with the uninstall."""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")

    r = _run(tmp_path, "uninstall", "plain", input_text="y\n")

    assert r.exit_code == 0, r.output
    assert _entries(tmp_path) == []


def test_uninstall_interactive_confirm_no_aborts(tmp_path: Path) -> None:
    """Without --quiet, declining the confirm aborts and leaves the entry."""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")

    r = _run(tmp_path, "uninstall", "plain", input_text="n\n")

    assert r.exit_code != 0
    assert len(_entries(tmp_path)) == 1


def test_uninstall_not_installed_errors_clearly(tmp_path: Path) -> None:
    """Uninstalling a name that isn't installed → non-zero, clear message, no crash."""
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "plain"],
    )

    assert r.exit_code == 1, r.output
    assert "is not installed" in r.stderr
    assert "Traceback" not in r.stderr


def test_uninstall_no_harness_exits_no_config(tmp_path: Path) -> None:
    """`uninstall` with no `.harness/` → EXIT_NO_CONFIG (3)."""
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "plain"],
    )
    assert r.exit_code == 3, r.output


# --- list --------------------------------------------------------------------


def test_list_shows_only_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After installing plain + claude-code, both appear; uninstalled builtins do not.

    Only `plain` and `claude-code` are built-ins, so we prove "installed-only" by
    installing one and asserting the OTHER built-in is absent from the listing.
    """
    import shutil

    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    _run(tmp_path, "install", "plain")
    _run(tmp_path, "install", "claude-code")

    r = _run(tmp_path, "list")
    assert r.exit_code == 0, r.output
    assert "plain" in r.output
    assert "claude-code" in r.output

    # Now a workspace with ONLY plain installed must NOT list claude-code.
    other = tmp_path / "other"
    (other / ".harness").mkdir(parents=True)
    _run(other, "install", "plain")
    r2 = _run(other, "list")
    assert "plain" in r2.output
    assert "claude-code" not in r2.output


def test_list_empty_when_nothing_installed(tmp_path: Path) -> None:
    """No adapters.yaml → empty listing, exit 0."""
    (tmp_path / ".harness").mkdir()
    r = _run(tmp_path, "list")

    assert r.exit_code == 0, r.output
    assert "No adapters installed." in r.output


def test_list_type_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--type framework` lists plain but not the claude-code agent."""
    import shutil

    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    _run(tmp_path, "install", "plain")
    _run(tmp_path, "install", "claude-code")

    r = _run(tmp_path, "list", "--type", "framework")
    assert r.exit_code == 0, r.output
    assert "plain" in r.output
    assert "claude-code" not in r.output


def test_list_enabled_only_filter(tmp_path: Path) -> None:
    """`--enabled-only` drops a manually-disabled entry."""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")
    # Manually flip the entry to disabled to exercise the filter.
    path = _adapters_yaml(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["adapters"][0]["enabled"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    r = _run(tmp_path, "list", "--enabled-only")
    assert r.exit_code == 0, r.output
    assert "No adapters installed." in r.output


def test_list_json_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--json` → valid 6-key envelope with data.adapters; framework caps degrade."""
    import shutil

    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    _run(tmp_path, "install", "plain")
    _run(tmp_path, "install", "claude-code")

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "adapter", "list"]
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["command"] == "adapter list"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert "errors" in payload

    adapters = {a["name"]: a for a in payload["data"]["adapters"]}
    assert set(adapters) == {"plain", "claude-code"}
    # Framework row's capabilities degrades gracefully (null, no crash).
    assert adapters["plain"]["type"] == "framework"
    assert adapters["plain"]["capabilities"] is None
    # Agent row carries its capability map.
    assert adapters["claude-code"]["type"] == "agent"
    assert isinstance(adapters["claude-code"]["capabilities"], dict)
    assert adapters["claude-code"]["capabilities"]["pre_tool_use_hook"] is True


def test_list_no_harness_exits_no_config(tmp_path: Path) -> None:
    """`list` with no `.harness/` → EXIT_NO_CONFIG (3)."""
    r = _run(tmp_path, "list")
    assert r.exit_code == 3, r.output


# --- robustness (corrupt yaml, unknown uninstall, key preservation) ----------


def test_corrupt_adapters_yaml_list_exits_no_config_with_format_error(
    tmp_path: Path,
) -> None:
    """A corrupt adapters.yaml → `list` exits EXIT_NO_CONFIG (3) with a
    ``format_error``-shaped message on stderr, NOT a raw traceback."""
    (tmp_path / ".harness").mkdir()
    _adapters_yaml(tmp_path).write_text(":\n  - [unclosed")

    r = _run(tmp_path, "list")

    assert r.exit_code == 3, r.output
    assert "super-harness adapter list:" in r.stderr
    assert "corrupt" in r.stderr
    assert "Traceback" not in r.stderr


def test_corrupt_adapters_yaml_install_exits_no_config_with_format_error(
    tmp_path: Path,
) -> None:
    """A corrupt adapters.yaml → `install` exits EXIT_NO_CONFIG (3) with a
    ``format_error``-shaped message on stderr, NOT a raw traceback."""
    (tmp_path / ".harness").mkdir()
    _adapters_yaml(tmp_path).write_text(":\n  - [unclosed")

    r = _run(tmp_path, "install", "plain")

    assert r.exit_code == 3, r.output
    assert "super-harness adapter install:" in r.stderr
    assert "corrupt" in r.stderr
    assert "Traceback" not in r.stderr


def test_uninstall_unknown_name_errors_clearly(tmp_path: Path) -> None:
    """`uninstall <unknown-name>` → EXIT_GENERIC (1) with a clear error message."""
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "no-such-adapter"],
    )

    assert r.exit_code == 1, r.output
    assert "super-harness adapter uninstall:" in r.stderr
    assert "unknown adapter" in r.stderr
    assert "Traceback" not in r.stderr


def test_adapters_yaml_preserves_top_level_keys(tmp_path: Path) -> None:
    """install preserves unrelated top-level keys in adapters.yaml (no silent drop)."""
    (tmp_path / ".harness").mkdir()
    # Write an adapters.yaml that already has an extra top-level key.
    _adapters_yaml(tmp_path).write_text(
        "schema_version: 1\nadapters: []\n"
    )

    r = _run(tmp_path, "install", "plain")

    assert r.exit_code == 0, r.output
    data = yaml.safe_load(_adapters_yaml(tmp_path).read_text())
    assert data.get("schema_version") == 1, (
        "Top-level key 'schema_version' was dropped by the round-trip write"
    )
    assert len(data.get("adapters", [])) == 1
