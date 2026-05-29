"""Integration tests for the generalized `adapter` CLI (Task 6.4).

Covers the registry-driven ``install`` / ``uninstall`` / ``list`` surface that
generalizes the Phase-5 minimal ``adapter install claude-code``. This file is
ADDITIVE — it coexists with ``test_adapter_install.py`` (the Phase-5 claude-code
tests that must stay green); it does NOT re-assert that file's claude-code
happy-path mechanics, only the NEW behaviour:

- ``install plain`` (framework): adapters.yaml entry, no verification.yaml side
  effect; AGENTS.md is NOT created when absent (install never creates a bare
  AGENTS.md — that is `init`'s job).
- ``install claude-code`` (agent): settings.json hook AND adapters.yaml entry.
- AGENTS.md injection (AC-4 / F13): after `init`, installing an agent consumes
  the no-agent anchor; uninstall restores it; round-trip re-install lands again.
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
    # verification.yaml is NOT touched (empty-safe no-op). AGENTS.md is NOT
    # created: this workspace never ran `init`, and install must never create a
    # bare AGENTS.md mid-install (AC-4 / F13 — injection only into an existing file).
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
    """`--enabled-only` drops a manually-disabled entry; filter-aware empty message."""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")
    # Manually flip the entry to disabled to exercise the filter.
    path = _adapters_yaml(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["adapters"][0]["enabled"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    r = _run(tmp_path, "list", "--enabled-only")
    assert r.exit_code == 0, r.output
    # Adapters ARE installed; the filter just matched none — message is filter-aware.
    assert "No adapters match the given filter." in r.output
    assert "No adapters installed." not in r.output


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


# --- new Fix-1: on_uninstall OSError is caught, registration preserved --------


def test_uninstall_on_uninstall_oserror_exits_generic_registration_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_uninstall raising OSError → exit 1, no traceback, format_error on stderr,
    and the adapters.yaml entry is STILL present (registration intact on failure)."""
    import shutil

    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    # Install claude-code so it's registered.
    install_result = _run(tmp_path, "install", "claude-code")
    assert install_result.exit_code == 0, install_result.output
    assert len(_entries(tmp_path)) == 1

    # Monkeypatch on_uninstall to raise OSError deterministically (no chmod needed).
    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

    def _raise_oserror(self: ClaudeCodeAdapter, root: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(ClaudeCodeAdapter, "on_uninstall", _raise_oserror)

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "claude-code"],
    )

    # Exit code must be EXIT_GENERIC (1), not 0.
    assert r.exit_code == 1, r.output
    # No raw traceback must escape.
    assert "Traceback" not in r.stderr, r.stderr
    # A format_error-shaped message must be present on stderr.
    assert "super-harness adapter uninstall:" in r.stderr, r.stderr
    assert "failed to clean up" in r.stderr, r.stderr
    assert "boom" in r.stderr, r.stderr
    # Registration must be intact — entry still in adapters.yaml.
    entries = _entries(tmp_path)
    assert any(e.get("name") == "claude-code" for e in entries), (
        f"claude-code entry was removed despite on_uninstall failure; entries={entries}"
    )


# --- new Fix-2: filter-aware empty message ------------------------------------


def test_list_type_filter_empty_shows_filter_message_not_not_installed(
    tmp_path: Path,
) -> None:
    """`list --type agent` when only `plain` (framework) is installed shows the
    filter-aware empty message, not the misleading 'No adapters installed.'"""
    (tmp_path / ".harness").mkdir()
    _run(tmp_path, "install", "plain")

    r = _run(tmp_path, "list", "--type", "agent")

    assert r.exit_code == 0, r.output
    assert "No adapters match the given filter." in r.output
    assert "No adapters installed." not in r.output


def test_list_no_filter_nothing_installed_shows_not_installed(tmp_path: Path) -> None:
    """With no filter and nothing installed, the message is 'No adapters installed.'"""
    (tmp_path / ".harness").mkdir()
    r = _run(tmp_path, "list")

    assert r.exit_code == 0, r.output
    assert "No adapters installed." in r.output
    assert "No adapters match the given filter." not in r.output


# --- AGENTS.md injection / removal (AC-4 / F13) -------------------------------

_NO_AGENT_ANCHOR = "<!-- super-harness no-agent-adapter-installed -->"
_CLAUDE_BEGIN = "<!-- super-harness agent: claude-code -->"
_CLAUDE_END = "<!-- /super-harness agent: claude-code -->"


def _init(ws: Path) -> object:
    """Run `super-harness init` so a real AGENTS.md (with the no-agent anchor) exists."""
    return CliRunner().invoke(main, ["--workspace", str(ws), "init"])


def _install_claude(ws: Path, monkeypatch: pytest.MonkeyPatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    return _run(ws, "install", "claude-code")


def test_install_agent_injects_subsection_consuming_no_agent_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` → `install claude-code` injects the real agent block, consuming the anchor."""
    init_result = _init(tmp_path)
    assert init_result.exit_code == 0, init_result.output
    # init leaves the no-agent anchor (no agent adapter installed yet).
    assert _NO_AGENT_ANCHOR in _agents_md(tmp_path).read_text()

    r = _install_claude(tmp_path, monkeypatch)
    assert r.exit_code == 0, r.output

    text = _agents_md(tmp_path).read_text()
    # The real claude-code block is present and the anchor was consumed.
    assert _CLAUDE_BEGIN in text
    assert _CLAUDE_END in text
    assert _NO_AGENT_ANCHOR not in text


def test_reinstall_agent_replaces_block_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-installing claude-code leaves exactly one claude-code block (replace in place)."""
    assert _init(tmp_path).exit_code == 0
    assert _install_claude(tmp_path, monkeypatch).exit_code == 0
    assert _install_claude(tmp_path, monkeypatch).exit_code == 0

    text = _agents_md(tmp_path).read_text()
    assert text.count(_CLAUDE_BEGIN) == 1
    assert text.count(_CLAUDE_END) == 1


def test_install_agent_absent_agents_md_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install when AGENTS.md is ABSENT (no `init`) → succeeds, AGENTS.md still absent."""
    (tmp_path / ".harness").mkdir()
    assert not _agents_md(tmp_path).exists()

    r = _install_claude(tmp_path, monkeypatch)

    assert r.exit_code == 0, r.output
    # install must never create a bare AGENTS.md.
    assert not _agents_md(tmp_path).exists()


def test_uninstall_agent_removes_block_restores_no_agent_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uninstalling the only agent removes its block AND restores the no-agent anchor."""
    assert _init(tmp_path).exit_code == 0
    assert _install_claude(tmp_path, monkeypatch).exit_code == 0
    before = _agents_md(tmp_path).read_text()
    assert _CLAUDE_BEGIN in before
    # The plain framework block (written by init) is preserved across uninstall.
    assert "super-harness framework: plain" in before

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "claude-code"],
    )
    assert r.exit_code == 0, r.output

    text = _agents_md(tmp_path).read_text()
    # claude-code block gone; no-agent anchor restored (last agent removed).
    assert _CLAUDE_BEGIN not in text
    assert _CLAUDE_END not in text
    assert _NO_AGENT_ANCHOR in text
    # Other content preserved: outer section markers + the plain framework block.
    assert "super-harness section begin" in text
    assert "super-harness framework: plain" in text


def test_round_trip_install_uninstall_reinstall_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init → install → uninstall → install ends with the claude-code block present.

    Regression for the no-anchor hole: after uninstall restores the no-agent
    anchor, the SECOND install must find it and re-inject (not silently no-op).
    """
    assert _init(tmp_path).exit_code == 0
    assert _install_claude(tmp_path, monkeypatch).exit_code == 0

    uninstall = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "claude-code"],
    )
    assert uninstall.exit_code == 0, uninstall.output

    # Re-install: must land the block again via the restored no-agent anchor.
    assert _install_claude(tmp_path, monkeypatch).exit_code == 0

    text = _agents_md(tmp_path).read_text()
    assert text.count(_CLAUDE_BEGIN) == 1
    assert _NO_AGENT_ANCHOR not in text


# --- I-1: AGENTS.md write failure on install/uninstall → clean error envelope -


def test_install_agents_md_write_failure_exits_generic_with_format_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the AGENTS.md inject raises OSError mid-install, the command surfaces a
    clean format_error (exit 1, no traceback) instead of a raw crash.

    We force a portable OSError by replacing the AGENTS.md path with a DIRECTORY
    after `init`: the injector's read (`AGENTS.md/`.read_text()) raises
    IsADirectoryError (an OSError subclass) on every platform. The adapters.yaml
    entry was already persisted (we assert it survived — re-install is the
    idempotent recovery contract, no rollback)."""
    import shutil

    assert _init(tmp_path).exit_code == 0
    # Replace the file AGENTS.md with a directory at the same path → any
    # read/write through it raises OSError deterministically (cross-platform).
    agents = _agents_md(tmp_path)
    agents.unlink()
    agents.mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)

    r = _run(tmp_path, "install", "claude-code")

    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness adapter install:" in r.stderr, r.stderr
    assert "failed to update AGENTS.md" in r.stderr, r.stderr
    assert "Hint:" in r.stderr, r.stderr
    # The yaml entry was recorded BEFORE the AGENTS.md write — it must survive so
    # the idempotent re-install can recover (no rollback in v0.1).
    assert any(e.get("name") == "claude-code" for e in _entries(tmp_path)), (
        f"entry lost despite idempotent-retry contract; entries={_entries(tmp_path)}"
    )


def test_uninstall_agents_md_remove_failure_exits_generic_with_format_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If removing the AGENTS.md subsection raises OSError on uninstall, the
    command surfaces a clean format_error (exit 1, no traceback).

    We monkeypatch `remove_subsection` (as imported into the adapter module) to
    raise OSError deterministically — replacing AGENTS.md with a directory would
    fail the not-installed precheck path differently, so a targeted patch keeps
    this test about the remove step specifically."""
    assert _init(tmp_path).exit_code == 0
    assert _install_claude(tmp_path, monkeypatch).exit_code == 0

    import super_harness.cli.adapter as adapter_mod

    def _raise_oserror(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(adapter_mod, "remove_subsection", _raise_oserror)

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "claude-code"],
    )

    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness adapter uninstall:" in r.stderr, r.stderr
    assert "failed to remove the AGENTS.md subsection" in r.stderr, r.stderr


# --- scan-once ---------------------------------------------------------------


def test_scan_once_agent_adapter_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scan-once claude-code` (an AgentAdapter) → EXIT_GENERIC (1), no traceback.

    Agent adapters have no ``observe`` — scan-once is a FrameworkAdapter-only
    operation, so the command must reject an agent with a clean format_error.
    """
    import shutil

    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)

    r = _run(tmp_path, "scan-once", "claude-code")

    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness adapter scan-once:" in r.stderr, r.stderr
    # The message must name WHY (agent adapters have no observe()).
    assert "observe" in r.stderr.lower() or "framework" in r.stderr.lower(), r.stderr


def test_scan_once_unknown_adapter_exits_generic(tmp_path: Path) -> None:
    """`scan-once <unknown>` → EXIT_GENERIC (1) via the shared resolver path."""
    (tmp_path / ".harness").mkdir()
    r = _run(tmp_path, "scan-once", "nope")

    assert r.exit_code == 1, r.output
    assert "super-harness adapter scan-once:" in r.stderr, r.stderr
    assert "unknown adapter" in r.stderr, r.stderr


def test_scan_once_no_harness_exits_no_config(tmp_path: Path) -> None:
    """No `.harness/` → EXIT_NO_CONFIG (3)."""
    r = _run(tmp_path, "scan-once", "plain")

    assert r.exit_code == 3, r.output
    assert "super-harness adapter scan-once:" in r.stderr, r.stderr


def test_scan_once_emit_validation_failure_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed openspec change (tasks.md but NO proposal.md) yields a lone
    ``plan_ready`` whose intent_declared emit-time precondition fails.

    scan-once must surface that EmitPreconditionError via format_error +
    EXIT_VALIDATION (2) — never a raw traceback, and naming the failing change.
    """
    (tmp_path / ".harness").mkdir()
    changes = tmp_path / "openspec" / "changes"
    # `bad` has tasks.md but no proposal.md → scan yields a lone plan_ready.
    (changes / "bad").mkdir(parents=True)
    (changes / "bad" / "tasks.md").write_text("- [ ] do a thing\n", encoding="utf-8")
    (tmp_path / "openspec" / "specs").mkdir(parents=True)

    r = _run(tmp_path, "scan-once", "openspec")

    assert r.exit_code == 2, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness adapter scan-once:" in r.stderr, r.stderr
    # The message names the offending change so the operator can fix it.
    assert "bad" in r.stderr, r.stderr


# --- Task 10.5: verification.yaml adapter_provided merge (OI-3 + dedup + AC-5) -


def _read_adapter_provided(ws: Path) -> list[dict]:
    data = yaml.safe_load(_verification_yaml(ws).read_text()) or {}
    return data.get("adapter_provided") or []


def _openspec_workspace(ws: Path) -> None:
    """Create a .harness/ + a detectable openspec layout so install openspec works."""
    (ws / ".harness").mkdir()
    (ws / "openspec" / "changes").mkdir(parents=True)
    (ws / "openspec" / "specs").mkdir(parents=True)


def test_install_openspec_writes_single_adapter_provided_row(tmp_path: Path) -> None:
    """Installing openspec lands exactly ONE openspec-validate adapter_provided row."""
    _openspec_workspace(tmp_path)
    r = _run(tmp_path, "install", "openspec")

    assert r.exit_code == 0, r.output
    rows = _read_adapter_provided(tmp_path)
    ids = [c["id"] for c in rows]
    assert ids == ["openspec-validate"]
    assert rows[0]["provided_by"] == "openspec-adapter"


def test_install_openspec_twice_no_duplicate_accumulation(tmp_path: Path) -> None:
    """Re-installing openspec REPLACES its row in place — exactly ONE row, not two.

    Regression for the duplicate-accumulation bug: the old bare `provided.extend`
    appended another identical openspec-validate row on every re-install.
    """
    _openspec_workspace(tmp_path)
    first = _run(tmp_path, "install", "openspec")
    second = _run(tmp_path, "install", "openspec")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    rows = _read_adapter_provided(tmp_path)
    assert [c["id"] for c in rows] == ["openspec-validate"], rows


def test_install_conflicting_check_id_exits_validation_two(tmp_path: Path) -> None:
    """OI-3: installing openspec when a DIFFERENT provided_by already owns the id
    → EXIT_VALIDATION (2) with a clean format_error, not a silent double-land."""
    _openspec_workspace(tmp_path)
    # Pre-seed verification.yaml with the SAME id but a DIFFERENT provided_by so
    # the openspec built-in's openspec-validate check collides on merge.
    _verification_yaml(tmp_path).write_text(
        yaml.safe_dump(
            {
                "adapter_provided": [
                    {
                        "id": "openspec-validate",
                        "command": "something else",
                        "provided_by": "some-other-adapter",
                    }
                ]
            }
        )
    )

    r = _run(tmp_path, "install", "openspec")

    assert r.exit_code == 2, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness adapter install:" in r.stderr, r.stderr
    assert "openspec-validate" in r.stderr, r.stderr
    # The conflicting row is untouched (we rejected — never partially merged).
    rows = _read_adapter_provided(tmp_path)
    assert rows == [
        {
            "id": "openspec-validate",
            "command": "something else",
            "provided_by": "some-other-adapter",
        }
    ]


def test_uninstall_openspec_removes_only_its_adapter_provided_row(
    tmp_path: Path,
) -> None:
    """AC-5: uninstall openspec drops its adapter_provided row but leaves user
    `checks` AND another adapter's rows untouched — pruned by (provided_by, id),
    so it works even if the row's other fields drifted since install."""
    _openspec_workspace(tmp_path)
    assert _run(tmp_path, "install", "openspec").exit_code == 0

    # Drift the command field + add a user check and a foreign adapter_provided row.
    data = yaml.safe_load(_verification_yaml(tmp_path).read_text())
    for row in data["adapter_provided"]:
        if row["id"] == "openspec-validate":
            row["command"] = "openspec validate DRIFTED"
    data.setdefault("checks", []).append({"id": "tests", "command": "npm test"})
    data["adapter_provided"].append(
        {"id": "other-check", "command": "x", "provided_by": "another-adapter"}
    )
    _verification_yaml(tmp_path).write_text(yaml.safe_dump(data, sort_keys=False))

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "openspec"],
    )
    assert r.exit_code == 0, r.output

    after = yaml.safe_load(_verification_yaml(tmp_path).read_text())
    ap_ids = [c["id"] for c in after.get("adapter_provided") or []]
    # openspec-validate gone (despite the drifted command); the foreign row stays.
    assert ap_ids == ["other-check"], after
    # User `checks` untouched.
    assert [c["id"] for c in after.get("checks") or []] == ["tests"]


# --- non-UTF-8 verification.yaml regression (UnicodeDecodeError is ValueError) -


def test_install_openspec_non_utf8_verification_yaml_exits_no_config(
    tmp_path: Path,
) -> None:
    """A non-UTF-8 verification.yaml → `install openspec` exits EXIT_NO_CONFIG (3)
    with a ``format_error``-shaped message on stderr, NOT a raw traceback.

    Regression: UnicodeDecodeError subclasses ValueError (NOT OSError), so it
    was NOT caught by the old bare ``except yaml.YAMLError``.
    """
    _openspec_workspace(tmp_path)
    # Write a valid YAML structure with an invalid UTF-8 byte embedded so the
    # file fails on read_text() (default UTF-8) before yaml ever sees it.
    _verification_yaml(tmp_path).write_bytes(
        b"adapter_provided:\n  - id: x\n\xe9\xff bad\n"
    )

    r = _run(tmp_path, "install", "openspec")

    assert r.exit_code == 3, r.output
    assert "super-harness adapter install:" in r.stderr, r.stderr
    assert "corrupt" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_uninstall_openspec_non_utf8_verification_yaml_exits_no_config(
    tmp_path: Path,
) -> None:
    """A non-UTF-8 verification.yaml → `uninstall openspec` exits EXIT_NO_CONFIG (3)
    with a ``format_error``-shaped message on stderr, NOT a raw traceback.

    Regression: UnicodeDecodeError subclasses ValueError (NOT OSError), so it
    was NOT caught by the old bare ``except yaml.YAMLError``.
    """
    _openspec_workspace(tmp_path)
    # Install openspec so it appears in adapters.yaml (uninstall checks this).
    assert _run(tmp_path, "install", "openspec").exit_code == 0

    # Now corrupt verification.yaml with a non-UTF-8 byte.
    _verification_yaml(tmp_path).write_bytes(
        b"adapter_provided:\n  - id: x\n\xe9\xff bad\n"
    )

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "adapter", "uninstall", "openspec"],
    )

    assert r.exit_code == 3, r.output
    assert "super-harness adapter uninstall:" in r.stderr, r.stderr
    assert "corrupt" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
