from pathlib import Path

from click.testing import CliRunner

from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.cli import main
from super_harness.version import __version__


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
