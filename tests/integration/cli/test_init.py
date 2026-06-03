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
    # HG-02.C: policy.yaml ships a discoverable reviewers block so a user can
    # switch a reviewer to `human` when token budget rules out subagent review.
    policy = (tmp_path / ".harness" / "policy.yaml").read_text()
    assert "reviewers:" in policy
    assert "plan-reviewer:" in policy
    assert "code-reviewer:" in policy
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
    """v0.1: --framework is accepted but produces no runtime notice.

    Help text carries the placeholder caveat (Phase 4 will wire --framework).
    Locks in the Phase 1 convention so a future regression that re-introduces
    a runtime stderr notice would be caught. (--setup-github is now wired in
    Phase 12 — its behavior is covered by test_init_setup_github.py.)
    """
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
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
# Agent gate hook auto-install (one-command onboarding)
# --------------------------------------------------------------------------- #


def test_init_auto_installs_agent_hook_when_claude_dir_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init with `.claude/` present installs the PreToolUse hook into
    settings.local.json and registers claude-code in adapters.yaml."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    settings = tmp_path / ".claude" / "settings.local.json"
    assert settings.exists()
    assert "--agent claude-code" in settings.read_text()
    adapters = (tmp_path / ".harness" / "adapters.yaml").read_text()
    assert "claude-code" in adapters
    # success advisory on stdout (security-relevant side effect surfaced).
    assert "registered PreToolUse gate hook" in result.output


def test_init_agent_install_yaml_error_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``yaml.YAMLError`` from ``_persist_install_entry`` (corrupt/unreadable
    ``.harness/adapters.yaml``) is non-fatal: the hook is installed but
    registration fails, init still completes, and an advisory is printed."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )

    def _raise(*args: object, **kwargs: object) -> None:
        raise yaml.YAMLError("boom")

    monkeypatch.setattr(
        "super_harness.cli.init._persist_install_entry", _raise
    )
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    # init still completes: .harness/ scaffolded.
    assert (tmp_path / ".harness").is_dir()
    assert (tmp_path / ".harness" / "events.jsonl").exists()
    # advisory text appears (hook installed but could not be registered).
    assert "could not be registered" in result.output


def test_init_agent_install_runtime_error_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `super-harness-hook` is off PATH, ``install_hooks`` raises its
    documented ``RuntimeError`` — init must treat this as NON-fatal: scaffold
    `.harness/`, skip the gate (no settings.local.json written), and surface an
    advisory. ``install_hooks`` checks the hook binary before the CLI binary, so
    returning None for the hook is enough to trigger the RuntimeError."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: None,
    )
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    # Non-fatal: init still completes.
    assert result.exit_code == 0, result.output
    # .harness/ scaffolded.
    assert (tmp_path / ".harness").is_dir()
    # Install failed before writing the gate hook — settings.local.json absent.
    assert not (tmp_path / ".claude" / "settings.local.json").exists()
    # Advisory text surfaced (hook not installed / not found on PATH).
    assert (
        "gate hook not installed" in result.output
        or "not found on PATH" in result.output
    ), result.output


def test_init_no_agent_flag_skips_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--no-agent"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_init_no_claude_dir_is_agent_noop(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()


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


def test_init_force_reinjects_installed_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`init` → `adapter install claude-code` → `init --force` re-renders the
    AGENTS.md super-harness section AND re-injects every installed adapter's
    subsection, so a re-render never loses adapter guidance (full --force loop
    closure). The adapters.yaml entry + the settings.local.json hooks stay intact and
    are NOT touched by init.

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
    # records the adapter + settings.local.json hooks.
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    install = runner.invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "claude-code"]
    )
    assert install.exit_code == 0, install.output
    agents = tmp_path / "AGENTS.md"
    assert claude_begin in agents.read_text()
    assert no_agent_anchor not in agents.read_text()

    # init --force → re-renders the section AND re-injects installed adapters.
    forced = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert forced.exit_code == 0, forced.output

    # 1) AGENTS.md preserved the claude-code agent block (re-injected, NOT reset
    #    to the no-agent anchor); exactly ONE block (no duplicate); the outer
    #    section + plain framework block are present.
    text = agents.read_text()
    assert claude_begin in text
    assert text.count(claude_begin) == 1
    assert no_agent_anchor not in text
    assert "<!-- super-harness section begin " in text
    assert text.count("<!-- super-harness framework: plain -->") == 1

    # 2) adapters.yaml STILL lists claude-code (init never touches it).
    adapters = yaml.safe_load((tmp_path / ".harness" / "adapters.yaml").read_text())
    names = [e.get("name") for e in (adapters.get("adapters") or [])]
    assert "claude-code" in names, f"claude-code dropped from adapters.yaml: {adapters}"

    # 2b) settings.local.json STILL has our PreToolUse + SessionStart hooks (unchanged).
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
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

    # 3) the `init --force` run did NOT emit the old "was reset" advisory.
    combined = forced.stderr + forced.output
    assert "was reset" not in combined, combined


@pytest.mark.parametrize(
    "bad_yaml",
    [
        # wrong-shape but valid YAML → `load_adapters` raises ValueError.
        "adapters: not-a-list\n",
        # syntactically broken YAML → `yaml.safe_load` raises yaml.YAMLError.
        "{ this is: not: valid: yaml\n",
        ":\n  - [unclosed\n",
    ],
    ids=["wrong-shape-valueerror", "broken-flow-mapping-yamlerror", "unclosed-seq-yamlerror"],
)
def test_init_force_corrupt_adapters_yaml_emits_advisory_and_exits_ok(
    tmp_path: Path,
    bad_yaml: str,
):
    """A corrupt `.harness/adapters.yaml` makes `init --force` re-injection a
    NON-FATAL advisory (couldn't re-inject) + still exit 0 with a valid base
    AGENTS.md section (the outer section + plain block + no-agent anchor).

    Covers BOTH failure families: a wrong-shape (valid YAML, `ValueError`) and
    a syntactically-broken file (`yaml.YAMLError`) — both must route to the
    same best-effort advisory, never a raw traceback."""
    runner = CliRunner()
    assert runner.invoke(main, ["--workspace", str(tmp_path), "init"]).exit_code == 0

    (tmp_path / ".harness" / "adapters.yaml").write_text(bad_yaml)

    forced = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "couldn't re-inject installed adapters" in forced.stderr, forced.stderr
    # Never a raw traceback for either failure family.
    combined = forced.stderr + forced.output
    assert "Traceback" not in combined, combined

    # Base AGENTS.md section is still valid (init did not crash on the bad yaml).
    text = (tmp_path / "AGENTS.md").read_text()
    assert "<!-- super-harness section begin " in text
    assert text.count("<!-- super-harness framework: plain -->") == 1
    assert "<!-- super-harness no-agent-adapter-installed -->" in text


def test_init_fresh_does_not_reinject_or_warn(tmp_path: Path):
    """Fresh init (no adapters.yaml): re-injection is a no-op (load_adapters
    returns ([],[])) and emits no advisory — the no-agent anchor stays put."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    assert "couldn't re-inject" not in r.stderr
    assert "was reset" not in r.stderr
    text = (tmp_path / "AGENTS.md").read_text()
    assert "<!-- super-harness no-agent-adapter-installed -->" in text
    assert "<!-- super-harness agent:" not in text


# --------------------------------------------------------------------------- #
# .gitignore management (S2 fix — OPEN-ITEMS #6)
# --------------------------------------------------------------------------- #


_GITIGNORE_BEGIN = "# >>> super-harness gitignore (do not edit between markers)"
_GITIGNORE_END = "# <<< super-harness gitignore"
_CANONICAL_GITIGNORE_PATHS = (
    ".harness/state.yaml",
    ".harness/events.jsonl",
    ".harness/sensor-results/",
    ".harness/verification-results/",
    ".harness/operation-logs/",
    ".harness/anchors/index.yaml",
    ".harness/pending-l1-updates/",
    ".harness/pending-reviews/",
    ".harness/gate-disabled",
    ".claude/settings.local.json",
)


def test_init_writes_gitignore_block_fresh_repo(tmp_path: Path):
    """Fresh repo (no .gitignore): init writes the marker-bounded block with
    the canonical `.harness/` runtime + per-machine `.claude/` paths."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0, r.output
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    text = gitignore.read_text()
    assert _GITIGNORE_BEGIN in text
    assert _GITIGNORE_END in text
    for p in _CANONICAL_GITIGNORE_PATHS:
        assert p in text, f"missing canonical path: {p}"


def test_init_preserves_existing_gitignore_user_content(tmp_path: Path):
    """Existing .gitignore (no super-harness block): init appends the block
    while preserving the user's content verbatim."""
    gitignore = tmp_path / ".gitignore"
    user_content = "# User-written\n*.pyc\nnode_modules/\n.env\n"
    gitignore.write_text(user_content)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0, r.output
    text = gitignore.read_text()
    # User content preserved.
    assert "# User-written" in text
    assert "*.pyc" in text
    assert "node_modules/" in text
    assert ".env" in text
    # Block appended after user content.
    assert _GITIGNORE_BEGIN in text
    user_idx = text.index("node_modules/")
    block_idx = text.index(_GITIGNORE_BEGIN)
    assert user_idx < block_idx


def test_init_force_does_not_duplicate_gitignore_block(tmp_path: Path):
    """Re-running init with --force does not duplicate the marker block."""
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0, r2.output
    text = (tmp_path / ".gitignore").read_text()
    assert text.count(_GITIGNORE_BEGIN) == 1
    assert text.count(_GITIGNORE_END) == 1


def test_init_gitignore_multiple_blocks_fails_loud(tmp_path: Path):
    """An existing .gitignore with ≥2 super-harness marker blocks fails loud
    (never splices) and leaves the file untouched (Phase 7/9/12 marker
    discipline)."""
    gitignore = tmp_path / ".gitignore"
    bad = (
        f"{_GITIGNORE_BEGIN}\n"
        ".harness/state.yaml\n"
        f"{_GITIGNORE_END}\n"
        "\n"
        f"{_GITIGNORE_BEGIN}\n"
        ".harness/events.jsonl\n"
        f"{_GITIGNORE_END}\n"
    )
    gitignore.write_text(bad)
    before = gitignore.read_text()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr
    # File left untouched (never spliced).
    assert gitignore.read_text() == before
