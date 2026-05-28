import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.cli import main
from super_harness.version import __version__

_FAKE_HOOK = "/usr/local/bin/super-harness-hook"


def test_init_creates_harness_dir(tmp_path: Path):
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    assert (tmp_path / ".harness").is_dir()
    assert (tmp_path / ".harness" / "events.jsonl").exists()
    assert (tmp_path / ".harness" / "policy.yaml").exists()
    assert (tmp_path / ".harness" / "sensors.yaml").exists()
    assert (tmp_path / ".harness" / "verification.yaml").exists()
    assert (tmp_path / ".harness" / "source-paths.yaml").exists()


def test_init_idempotent_without_force(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r2.exit_code == 3  # EXIT_NO_CONFIG-style for already-init
    # I-3: verify the --force hint reaches stderr (Click 8.4 exposes r.stderr
    # directly on the Result; CliRunner no longer takes mix_stderr).
    assert "Hint: Pass `--force` to overwrite the existing directory." in r2.stderr


def test_init_force_overwrites(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    (tmp_path / ".harness" / "policy.yaml").write_text("# user-edit\n")
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0


def test_init_creates_all_subdirs(tmp_path: Path):
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    for d in (
        "anchors",
        "sensor-results",
        "verification-results",
        "operation-logs",
        "pending-l1-updates",
        "pending-reviews",
    ):
        assert (tmp_path / ".harness" / d).is_dir(), f"missing subdir: {d}"


def test_init_creates_gates_and_conventions(tmp_path: Path):
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert (tmp_path / ".harness" / "gates.yaml").exists()
    assert (tmp_path / ".harness" / "conventions.md").exists()


def test_init_refuses_when_partial_harness_exists(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 3
    assert not (tmp_path / ".harness" / "events.jsonl").exists()


def test_init_accepts_noop_flags_silently(tmp_path: Path):
    """v0.1: --setup-github / --framework are accepted but produce no runtime notice.

    Help text carries the placeholder caveat (Phase 4 / Phase 11 will wire these).
    Locks in the Phase 1 convention so a future regression that re-introduces
    a runtime stderr notice would be caught.
    """
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--setup-github",
            "--framework",
            "openspec",
        ],
    )
    assert r.exit_code == 0
    assert "no-op" not in r.stderr.lower()
    assert "not yet implemented" not in r.stderr.lower()


def test_init_help_advertises_v01_caveat(tmp_path: Path):
    r = CliRunner().invoke(main, ["init", "--help"])
    assert r.exit_code == 0
    assert "v0.1" in r.output  # caveat is in --help for at least one no-op flag


# --------------------------------------------------------------------------- #
# AGENTS.md outer-section wiring (engineering-integration §2.2 / §3.2)
# --------------------------------------------------------------------------- #


def test_init_writes_agents_md_fresh_repo(tmp_path: Path):
    """Fresh repo (no AGENTS.md): init writes the version-stamped section,
    the plain framework block, and the no-agent anchor — and leaves NO
    literal [*_SECTION_AUTO_INSERTED] placeholders (§3.2)."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    agents = tmp_path / "AGENTS.md"
    assert agents.exists()
    text = agents.read_text()
    # version-stamped begin marker
    assert (
        f"<!-- super-harness section begin · v{__version__} · DO NOT EDIT MANUALLY -->"
        in text
    )
    assert "<!-- super-harness section end -->" in text
    # plain framework block injected in place of the framework placeholder
    assert PlainAdapter().agents_md_subsection().rstrip("\n") in text
    # no-agent anchor present
    assert "<!-- super-harness no-agent-adapter-installed -->" in text
    # zero literal placeholders remain
    assert "[FRAMEWORK_SECTION_AUTO_INSERTED]" not in text
    assert "[AGENT_SECTION_AUTO_INSERTED]" not in text


def test_init_preserves_existing_agents_md_user_content(tmp_path: Path):
    """Existing AGENTS.md with user content (no super-harness section): init
    appends our section while preserving the user's content verbatim."""
    agents = tmp_path / "AGENTS.md"
    user_content = "# My project\n\nSome existing agent guidance.\n"
    agents.write_text(user_content)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    text = agents.read_text()
    assert user_content.rstrip() in text
    assert (
        f"<!-- super-harness section begin · v{__version__} · DO NOT EDIT MANUALLY -->"
        in text
    )
    assert PlainAdapter().agents_md_subsection().rstrip("\n") in text


def test_init_force_does_not_duplicate_agents_md_section(tmp_path: Path):
    """Re-running init with --force re-renders exactly one super-harness
    section (no duplicate blocks)."""
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.count("<!-- super-harness section begin ") == 1
    assert text.count("<!-- super-harness section end -->") == 1
    # framework block still present exactly once after re-render
    assert text.count("<!-- super-harness framework: plain -->") == 1


def test_init_does_not_write_agents_md_on_error_path(tmp_path: Path):
    """When .harness/ exists without --force, init errors and must NOT write
    AGENTS.md."""
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 3
    assert not (tmp_path / "AGENTS.md").exists()


def test_init_agents_md_write_failure_exits_generic_with_format_error(tmp_path: Path):
    """If the AGENTS.md write raises OSError, init surfaces a clean format_error
    (exit 1, no traceback) instead of a raw crash.

    We force a portable OSError by pre-creating a DIRECTORY at the AGENTS.md path:
    `inject_section`'s read (`AGENTS.md/`.read_text()) raises IsADirectoryError
    (an OSError subclass) on every platform. .harness/ has already been scaffolded
    by this point, so the message must reflect that and that `--force` re-runs."""
    # Pre-create AGENTS.md as a directory so the injector's read/write fails.
    (tmp_path / "AGENTS.md").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr, r.stderr
    assert "failed to write AGENTS.md" in r.stderr, r.stderr
    assert "Hint:" in r.stderr, r.stderr
    # .harness/ was scaffolded before the AGENTS.md write — it must survive so the
    # `--force` re-run is the documented recovery.
    assert (tmp_path / ".harness").is_dir()


def test_init_force_warns_when_adapter_installed_but_only_resets_agents_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`init` → `adapter install claude-code` → `init --force` re-renders the
    AGENTS.md super-harness section back to defaults (dropping the claude-code
    agent block / restoring the no-agent anchor) AND emits a non-fatal advisory,
    while the adapters.yaml entry + the settings.json hooks stay intact.

    The reset is by-design for v0.1 (a future `sync` re-renders preserving
    installed adapters); this test guards that the footgun is no longer SILENT.

    `adapter install claude-code` resolves `super-harness-hook` via
    ``shutil.which``; we monkeypatch it to a fake absolute path so the real
    binary need not be on PATH — matching the pattern in
    ``tests/integration/cli/test_adapter.py``.
    """
    runner = CliRunner()
    no_agent_anchor = "<!-- super-harness no-agent-adapter-installed -->"
    claude_begin = "<!-- super-harness agent: claude-code -->"

    # init → real AGENTS.md (with the no-agent anchor) exists.
    assert runner.invoke(main, ["--workspace", str(tmp_path), "init"]).exit_code == 0

    # install claude-code → consumes the anchor, injects the agent block, and
    # records the adapter + settings.json hooks.
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    install = runner.invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "claude-code"]
    )
    assert install.exit_code == 0, install.output
    agents = tmp_path / "AGENTS.md"
    assert claude_begin in agents.read_text()
    assert no_agent_anchor not in agents.read_text()

    # init --force → re-renders the section back to defaults.
    forced = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert forced.exit_code == 0, forced.output

    # 1) AGENTS.md reset: claude-code block gone, no-agent anchor back.
    text = agents.read_text()
    assert claude_begin not in text
    assert no_agent_anchor in text

    # 2) adapters.yaml STILL lists claude-code.
    adapters = yaml.safe_load((tmp_path / ".harness" / "adapters.yaml").read_text())
    names = [e.get("name") for e in (adapters.get("adapters") or [])]
    assert "claude-code" in names, f"claude-code dropped from adapters.yaml: {adapters}"

    # 2b) settings.json STILL has our PreToolUse + SessionStart hooks.
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    pre_commands = [
        h["command"]
        for entry in settings["hooks"]["PreToolUse"]
        for h in entry["hooks"]
    ]
    assert pre_commands == [f"{_FAKE_HOOK} --agent claude-code"]
    session_commands = [
        h["command"]
        for entry in settings["hooks"]["SessionStart"]
        for h in entry["hooks"]
    ]
    assert session_commands == [f"{_FAKE_HOOK} change resume"]

    # 3) the `init --force` run emitted the advisory (names the reset + recovery).
    combined = forced.stderr + forced.output
    assert "was reset" in combined, combined
    assert "claude-code" in combined, combined
