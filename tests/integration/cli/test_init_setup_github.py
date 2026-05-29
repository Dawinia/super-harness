"""Integration tests for `super-harness init --setup-github` (engineering-integration §2.6/§3.1).

`gh` is ALWAYS mocked — never real gh, never network. stdlib unittest.mock only
(no pytest-mock). Uses Click's CliRunner, mirroring tests/integration/cli/test_init.py.

Covers the Task 12.2 binding behaviors:
  (a) check_gh raises GhNotInstalled -> init exits 4 + NO .github/ file written
  (b) success path -> pull_request_template.md written (§2.6 verbatim) + repo-settings
      helper called
  (c) repo-settings helper raises GhError -> init still exits 0 + an
      operation-logs/setup-github/*.log entry + advisory on stderr
  (d) re-run idempotency (template not duplicated) + existing block_count>=2 fails loud
  (e) WITHOUT --setup-github -> check_gh / gh never called (regression guard)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.engineering.gh import GhError, GhNotInstalled


def _template_path(root: Path) -> Path:
    return root / ".github" / "pull_request_template.md"


def _op_logs(root: Path) -> list[Path]:
    d = root / ".harness" / "operation-logs" / "setup-github"
    return list(d.glob("*.log")) if d.is_dir() else []


# --------------------------------------------------------------------------- #
# (a) check_gh failure aborts with exit 4, BEFORE any .github/ write
# --------------------------------------------------------------------------- #


def test_setup_github_gh_not_installed_exits_4_no_github_written(tmp_path: Path):
    with patch(
        "super_harness.cli.init.check_gh",
        side_effect=GhNotInstalled("gh CLI not found. Install: brew install gh"),
    ) as mock_check, patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ) as mock_settings:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )

    assert r.exit_code == 4, r.output
    assert mock_check.called
    # never reaches repo-settings on a check failure
    assert not mock_settings.called
    # NO .github/ file written
    assert not _template_path(tmp_path).exists()
    assert not (tmp_path / ".github").exists()
    # actionable hint in stderr (install / auth / refresh)
    assert "super-harness init:" in r.stderr
    assert "brew install gh" in r.stderr or "gh auth login" in r.stderr


# --------------------------------------------------------------------------- #
# (b) success path: template written + repo-settings helper called
# --------------------------------------------------------------------------- #


def test_setup_github_success_writes_template_and_calls_settings(tmp_path: Path):
    with patch("super_harness.cli.init.check_gh") as mock_check, patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ) as mock_settings:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )

    assert r.exit_code == 0, r.output
    assert mock_check.called
    assert mock_settings.called

    tpl = _template_path(tmp_path)
    assert tpl.exists()
    text = tpl.read_text()
    # §2.6 verbatim content
    assert "## Summary" in text
    assert "<!-- describe your change here -->" in text
    assert "## Test plan" in text
    assert "<!-- how was this verified -->" in text
    assert "## Related issues" in text
    assert "<!-- closes #123, refs #456 -->" in text
    assert "\n---\n" in text
    assert "<!-- super-harness:metadata -->" in text
    assert (
        "<!-- auto-filled by super-harness PR-decorator sensor; do not edit manually -->"
        in text
    )
    assert "<!-- /super-harness:metadata -->" in text
    # no operation-log on the happy path
    assert _op_logs(tmp_path) == []


def test_setup_github_template_matches_bundled_verbatim(tmp_path: Path):
    """The written template is byte-for-byte the bundled §2.6 template."""
    from importlib.resources import files

    bundled = files("super_harness.templates").joinpath("pull_request_template.md").read_text()

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )
    assert r.exit_code == 0, r.output
    assert _template_path(tmp_path).read_text() == bundled


# --------------------------------------------------------------------------- #
# (c) repo-settings failure -> non-fatal: exit 0 + operation-log + advisory
# --------------------------------------------------------------------------- #


def test_setup_github_settings_failure_is_nonfatal_logs_and_advises(tmp_path: Path):
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings",
        side_effect=GhError("gh api -X PATCH /repos/{owner}/{repo} failed (exit 1)"),
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )

    # non-fatal: init still succeeds
    assert r.exit_code == 0, r.output
    # template still written (the failure is AFTER the template write)
    assert _template_path(tmp_path).exists()
    # operation-log entry exists under operation-logs/setup-github/
    logs = _op_logs(tmp_path)
    assert len(logs) == 1, logs
    body = logs[0].read_text()
    # plain-text body: attempted command + captured stderr + outcome
    assert "allow_auto_merge" in body or "gh api" in body
    # advisory on stderr (manual-config hint)
    assert "auto-merge" in r.stderr.lower() or "allow auto-merge" in r.stderr.lower()


def test_setup_github_settings_failure_log_filename_has_no_colon(tmp_path: Path):
    """Operation-log filename must not contain ':' (portable across filesystems)."""
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings",
        side_effect=GhError("non-admin"),
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )
    assert r.exit_code == 0, r.output
    logs = _op_logs(tmp_path)
    assert len(logs) == 1
    assert ":" not in logs[0].name, logs[0].name


# --------------------------------------------------------------------------- #
# (d) re-run idempotency + fail-loud on ambiguity
# --------------------------------------------------------------------------- #


def test_setup_github_rerun_does_not_duplicate_metadata_block(tmp_path: Path):
    runner = CliRunner()
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r1 = runner.invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )
        # second run requires --force (.harness/ now exists) + auto-confirm the
        # template-overwrite prompt via -q (global quiet skips the confirm).
        r2 = runner.invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github", "--force"],
        )

    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    text = _template_path(tmp_path).read_text()
    assert text.count("<!-- super-harness:metadata -->") == 1, text
    assert text.count("<!-- /super-harness:metadata -->") == 1, text


def test_setup_github_existing_file_without_block_gets_placeholder_appended(tmp_path: Path):
    """An existing user template lacking the metadata block gets exactly one
    placeholder appended (header preserved)."""
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    user_tpl = gh_dir / "pull_request_template.md"
    user_tpl.write_text("## My custom template\n\nPlease describe.\n")

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github"],
        )
    assert r.exit_code == 0, r.output
    text = user_tpl.read_text()
    # user header preserved
    assert "## My custom template" in text
    assert "Please describe." in text
    # exactly one placeholder block appended
    assert text.count("<!-- super-harness:metadata -->") == 1, text
    assert text.count("<!-- /super-harness:metadata -->") == 1, text


def test_setup_github_existing_file_with_one_block_is_noop(tmp_path: Path):
    """An existing template that already has exactly one placeholder block is
    left untouched (idempotent no-op)."""
    from importlib.resources import files

    bundled = files("super_harness.templates").joinpath("pull_request_template.md").read_text()
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    user_tpl = gh_dir / "pull_request_template.md"
    user_tpl.write_text(bundled)

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github"],
        )
    assert r.exit_code == 0, r.output
    assert user_tpl.read_text() == bundled


def test_setup_github_existing_file_two_blocks_fails_loud(tmp_path: Path):
    """An existing template with >=2 metadata blocks fails loud (never splice)."""
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    user_tpl = gh_dir / "pull_request_template.md"
    user_tpl.write_text(
        "## Summary\n\n"
        "<!-- super-harness:metadata -->\n"
        "<!-- /super-harness:metadata -->\n\n"
        "<!-- super-harness:metadata -->\n"
        "<!-- /super-harness:metadata -->\n"
    )
    before = user_tpl.read_text()

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github"],
        )
    assert r.exit_code != 0, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr
    # never spliced — file left untouched
    assert user_tpl.read_text() == before


def test_setup_github_existing_file_decline_overwrite_leaves_untouched(tmp_path: Path):
    """Without -q, modifying an EXISTING template prompts; declining leaves the
    file untouched and is non-fatal (exit 0)."""
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    user_tpl = gh_dir / "pull_request_template.md"
    user_tpl.write_text("## My custom template\n\nPlease describe.\n")
    before = user_tpl.read_text()

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
            input="n\n",  # decline the confirm
        )
    assert r.exit_code == 0, r.output
    assert user_tpl.read_text() == before


# --------------------------------------------------------------------------- #
# (e) WITHOUT --setup-github -> check_gh / gh never called (regression guard)
# --------------------------------------------------------------------------- #


def test_init_without_setup_github_never_calls_gh(tmp_path: Path):
    with patch("super_harness.cli.init.check_gh") as mock_check, patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ) as mock_settings:
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert r.exit_code == 0, r.output
    assert not mock_check.called
    assert not mock_settings.called
    # no .github/ created on a plain init
    assert not (tmp_path / ".github").exists()


# --------------------------------------------------------------------------- #
# (f) error-family hardening (whole-branch review findings)
# --------------------------------------------------------------------------- #


def test_setup_github_non_utf8_existing_template_friendly_error(tmp_path: Path):
    """A non-UTF-8 existing pull_request_template.md → friendly error (exit 1),
    never a raw UnicodeDecodeError traceback (UnicodeDecodeError is a ValueError,
    not OSError — the project's recurring error-family bug class)."""
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    (gh_dir / "pull_request_template.md").write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "-q", "init", "--setup-github"]
        )
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr
    assert "could not read" in r.stderr


def test_setup_github_existing_no_block_noninteractive_no_quiet_is_nonfatal(tmp_path: Path):
    """Existing template w/o block, non-interactive (no input), no --quiet:
    cannot prompt → leave untouched + exit 0 (NOT Click's Abort/exit 1)."""
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    user_tpl = gh_dir / "pull_request_template.md"
    user_tpl.write_text("## My custom template\n\nPlease describe.\n")
    before = user_tpl.read_text()
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        # No `input=` → empty/non-interactive stdin → click.confirm raises Abort;
        # the fix catches it and degrades to "leave untouched, non-fatal".
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )
    assert r.exit_code == 0, r.output + r.stderr
    assert "Aborted" not in (r.output + r.stderr)
    assert "Traceback" not in r.stderr
    assert user_tpl.read_text() == before
    assert "non-interactive" in r.stderr.lower() or "skipped" in r.stderr.lower()


def test_setup_github_log_write_failure_is_nonfatal(tmp_path: Path):
    """If the operation-log write itself fails (path blocked), the non-fatal
    repo-settings degradation STILL exits 0 — never a hard crash (AC-7)."""
    runner = CliRunner()
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r1 = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r1.exit_code == 0
    # Block the log dir: a FILE where the setup-github log dir would go → mkdir fails.
    blocker = tmp_path / ".harness" / "operation-logs" / "setup-github"
    blocker.write_text("i am a file, not a dir")
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings",
        side_effect=GhError("non-admin"),
    ):
        r2 = runner.invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github", "--force"],
        )
    assert r2.exit_code == 0, r2.output + r2.stderr
    assert "Traceback" not in r2.stderr
    assert blocker.is_file()  # untouched
