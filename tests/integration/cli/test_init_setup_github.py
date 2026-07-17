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

import click
import pytest
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
    with (
        patch(
            "super_harness.cli.init.check_gh",
            side_effect=GhNotInstalled("gh CLI not found. Install: brew install gh"),
        ) as mock_check,
        patch("super_harness.cli.init.enable_repo_merge_settings") as mock_settings,
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])

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
    with (
        patch("super_harness.cli.init.check_gh") as mock_check,
        patch("super_harness.cli.init.enable_repo_merge_settings") as mock_settings,
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])

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
    assert "<!-- auto-filled by super-harness PR-decorator sensor; do not edit manually -->" in text
    assert "<!-- /super-harness:metadata -->" in text
    # no operation-log on the happy path
    assert _op_logs(tmp_path) == []


def test_setup_github_template_matches_bundled_verbatim(tmp_path: Path):
    """The written template is byte-for-byte the bundled §2.6 template."""
    from importlib.resources import files

    bundled = files("super_harness.templates").joinpath("pull_request_template.md").read_text()

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    assert _template_path(tmp_path).read_text() == bundled


# --------------------------------------------------------------------------- #
# (c) repo-settings failure -> non-fatal: exit 0 + operation-log + advisory
# --------------------------------------------------------------------------- #


def test_setup_github_settings_failure_is_nonfatal_logs_and_advises(tmp_path: Path):
    with (
        patch("super_harness.cli.init.check_gh"),
        patch(
            "super_harness.cli.init.enable_repo_merge_settings",
            side_effect=GhError("gh api -X PATCH /repos/{owner}/{repo} failed (exit 1)"),
        ),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])

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
    with (
        patch("super_harness.cli.init.check_gh"),
        patch(
            "super_harness.cli.init.enable_repo_merge_settings",
            side_effect=GhError("non-admin"),
        ),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    logs = _op_logs(tmp_path)
    assert len(logs) == 1
    assert ":" not in logs[0].name, logs[0].name


# --------------------------------------------------------------------------- #
# (d) re-run idempotency + fail-loud on ambiguity
# --------------------------------------------------------------------------- #


def test_setup_github_rerun_does_not_duplicate_metadata_block(tmp_path: Path):
    runner = CliRunner()
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r1 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
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

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
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

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
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

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
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

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
            input="n\n",  # decline the confirm
        )
    assert r.exit_code == 0, r.output
    assert user_tpl.read_text() == before


def test_setup_github_resolves_existing_file_decision_before_any_write(
    tmp_path: Path,
) -> None:
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    user_tpl = gh_dir / "pull_request_template.md"
    user_tpl.write_text("## My custom template\n")

    def decline_before_apply(*_args: object, **_kwargs: object) -> bool:
        assert not (tmp_path / ".harness").exists()
        assert not _workflow_path(tmp_path).exists()
        return False

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
        patch("super_harness.cli.init.click.confirm", side_effect=decline_before_apply),
    ):
        result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])

    assert result.exit_code == 0, result.output + result.stderr
    assert user_tpl.read_text() == "## My custom template\n"
    assert (tmp_path / ".harness").exists()


# --------------------------------------------------------------------------- #
# (e) WITHOUT --setup-github -> check_gh / gh never called (regression guard)
# --------------------------------------------------------------------------- #


def test_init_without_setup_github_never_calls_gh(tmp_path: Path):
    with (
        patch("super_harness.cli.init.check_gh") as mock_check,
        patch("super_harness.cli.init.enable_repo_merge_settings") as mock_settings,
    ):
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
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "-q", "init", "--setup-github"])
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
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        # No `input=` → empty/non-interactive stdin → click.confirm raises Abort;
        # the fix catches it and degrades to "leave untouched, non-fatal".
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output + r.stderr
    assert "Aborted" not in (r.output + r.stderr)
    assert "Traceback" not in r.stderr
    assert user_tpl.read_text() == before
    assert "non-interactive" in r.stderr.lower() or "skipped" in r.stderr.lower()


def _workflow_path(root: Path) -> Path:
    return root / ".github" / "workflows" / "super-harness.yml"


def test_setup_github_log_write_failure_is_nonfatal(tmp_path: Path):
    """If the operation-log write itself fails (path blocked), the non-fatal
    repo-settings degradation STILL exits 0 — never a hard crash (AC-7)."""
    runner = CliRunner()
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r1 = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r1.exit_code == 0
    # Block the log dir: a FILE where the setup-github log dir would go → mkdir fails.
    blocker = tmp_path / ".harness" / "operation-logs" / "setup-github"
    blocker.write_text("i am a file, not a dir")
    with (
        patch("super_harness.cli.init.check_gh"),
        patch(
            "super_harness.cli.init.enable_repo_merge_settings",
            side_effect=GhError("non-admin"),
        ),
    ):
        r2 = runner.invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github", "--force"],
        )
    assert r2.exit_code == 0, r2.output + r2.stderr
    assert "Traceback" not in r2.stderr
    assert blocker.is_file()  # untouched


# --------------------------------------------------------------------------- #
# (g) workflow file deployment (Task 14.2)
# --------------------------------------------------------------------------- #


def test_setup_github_writes_workflow_file(tmp_path: Path):
    """--setup-github writes BOTH .github/ files (PR template + workflow)."""
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])

    assert r.exit_code == 0, r.output
    assert _template_path(tmp_path).exists()
    assert _workflow_path(tmp_path).exists()
    wf_text = _workflow_path(tmp_path).read_text()
    assert len(wf_text) > 0


def test_setup_github_workflow_matches_bundled_verbatim(tmp_path: Path):
    """Written workflow file is byte-for-byte the bundled super_harness_workflow.yml."""
    from importlib.resources import files

    bundled = files("super_harness.templates").joinpath("super_harness_workflow.yml").read_text()

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    assert _workflow_path(tmp_path).read_text() == bundled


def test_setup_github_existing_workflow_with_quiet_overwrites(tmp_path: Path):
    """Existing workflow file + --quiet → overwrites without prompt."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text("old workflow content\n")

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github"],
        )
    assert r.exit_code == 0, r.output
    new_content = wf.read_text()
    assert new_content != "old workflow content\n"
    assert len(new_content) > 0


def test_setup_github_existing_workflow_decline_overwrite_leaves_untouched(tmp_path: Path):
    """Existing workflow file + interactive decline → leaves untouched, exit 0."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text("old workflow content\n")
    before = wf.read_text()

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings") as mock_settings,
    ):  # ← name it
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
            input="n\n",  # decline the confirm
        )
    assert r.exit_code == 0, r.output
    assert wf.read_text() == before
    assert mock_settings.called, (  # ← prove init reached Step 3
        "init must traverse Step 2.5 (_write_workflow_file) to reach Step 3 "
        "(enable_repo_merge_settings); a vacuous test would pass even if "
        "_write_workflow_file is removed"
    )
    # Non-vacuity guard: the overwrite prompt from _write_workflow_file must appear
    # in output. If _write_workflow_file were removed, this prompt would be absent
    # and this assertion would fail — proving the test is not vacuous.
    assert "Overwrite existing" in r.output and "super-harness.yml" in r.output, (
        "_write_workflow_file must have fired the overwrite prompt; if absent, the test is vacuous"
    )


def test_setup_github_existing_workflow_noninteractive_no_quiet_is_nonfatal(tmp_path: Path):
    """Existing workflow + non-interactive (no input) + no --quiet → leave untouched, exit 0."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text("old workflow content\n")
    before = wf.read_text()

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        # No `input=` → empty/non-interactive stdin → click.confirm raises Abort;
        # the fix catches it and degrades to "leave untouched, non-fatal".
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output + r.stderr
    assert "Aborted" not in (r.output + r.stderr)
    assert "Traceback" not in r.stderr
    assert wf.read_text() == before
    assert "non-interactive" in r.stderr.lower() or "skipped" in r.stderr.lower()


def test_setup_github_non_utf8_existing_workflow_friendly_error(tmp_path: Path):
    """Non-UTF-8 existing workflow file → exit 1 with friendly error, no traceback."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    _workflow_path(tmp_path).write_bytes(b"\xff\xfe not utf-8 \x80\x81")

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "-q", "init", "--setup-github"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr
    assert "could not read" in r.stderr


def test_setup_github_existing_workflow_identical_is_noop(tmp_path: Path):
    """Existing workflow that's byte-identical to bundled → no prompt, no-op exit 0."""
    from importlib.resources import files

    import super_harness.cli.init as _init_mod

    bundled = files("super_harness.templates").joinpath("super_harness_workflow.yml").read_text()
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text(bundled)

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings") as mock_settings,
        patch(  # ← spy: wraps= keeps real behavior
            "super_harness.cli.init._write_workflow_file",
            wraps=_init_mod._write_workflow_file,
        ) as mock_write_wf,
    ):
        # No input= — if a prompt fires this will fail (non-interactive Abort → nonfatal)
        # but we want to assert NO prompt fires at all (byte-identical is silent no-op).
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output + r.stderr
    assert wf.read_text() == bundled
    assert mock_settings.called, (  # ← prove init reached Step 3
        "init must traverse Step 2.5 (_write_workflow_file) to reach Step 3 "
        "(enable_repo_merge_settings); a vacuous test would pass even if "
        "_write_workflow_file is removed"
    )
    # Non-vacuity guard: the spy on _write_workflow_file must have been called.
    # A vacuous test (with _write_workflow_file removed) would fail here.
    assert mock_write_wf.called, "_write_workflow_file was never invoked — the test is vacuous"


# --------------------------------------------------------------------------- #
# (h) S3 fix — `init --setup-github` prints stdout advisories per substep
#     (OPEN-ITEMS #6 S3). Path (a) honest outcome literals: helpers report
#     wrote / kept-existing / declined so advisory matches reality.
# --------------------------------------------------------------------------- #


def test_setup_github_advisories_appear_on_fresh_success(tmp_path: Path):
    """Fresh repo + success path: stdout shows one advisory line per substep
    (gh CLI ok / wrote PR template / wrote workflow / enabled merge settings)
    plus the final 'super-harness initialized at ...' line."""
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    out = r.output
    assert "gh CLI: ok" in out, out
    assert "wrote .github/pull_request_template.md" in out, out
    assert "wrote .github/workflows/super-harness.yml" in out, out
    assert "repo merge settings: enabled" in out, out
    # Final line still present.
    assert "super-harness initialized at" in out


def test_setup_github_advisories_suppressed_under_quiet(tmp_path: Path):
    """--quiet suppresses all advisory prints (errors / format_error still go to stderr)."""
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "-q", "init", "--setup-github"],
        )
    assert r.exit_code == 0, r.output
    out = r.output
    assert "gh CLI: ok" not in out
    assert "wrote .github/pull_request_template.md" not in out
    assert "wrote .github/workflows/super-harness.yml" not in out
    assert "repo merge settings:" not in out
    # Final 'initialized at' line ALSO suppressed under --quiet? — the existing
    # contract keeps it (no test changes here). We only need to assert
    # advisory lines are suppressed.


def test_setup_github_advisories_suppressed_under_json(tmp_path: Path):
    """--json suppresses advisories (init emits no JSON envelope, but advisories
    shouldn't pollute stdout when JSON mode was requested)."""
    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "--json", "init", "--setup-github"],
        )
    assert r.exit_code == 0, r.output
    out = r.output
    assert "gh CLI: ok" not in out
    assert "wrote .github/pull_request_template.md" not in out
    assert "wrote .github/workflows/super-harness.yml" not in out
    assert "repo merge settings:" not in out


def test_setup_github_advisory_says_kept_existing_when_pr_template_is_noop(
    tmp_path: Path,
):
    """When _write_pr_template hits the idempotent no-op branch (existing
    template already has exactly one metadata block), the advisory says
    'kept existing' — NOT 'wrote' — because reality is "left untouched"."""
    from importlib.resources import files

    bundled = files("super_harness.templates").joinpath("pull_request_template.md").read_text()
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    (gh_dir / "pull_request_template.md").write_text(bundled)

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    out = r.output
    assert "kept existing .github/pull_request_template.md" in out, out
    # The 'wrote' advisory must NOT fire for the PR template.
    assert "wrote .github/pull_request_template.md" not in out


def test_setup_github_advisory_says_kept_existing_when_workflow_noop(tmp_path: Path):
    """Existing workflow that's byte-identical to bundled → advisory says
    'kept existing' for the workflow."""
    from importlib.resources import files

    bundled = files("super_harness.templates").joinpath("super_harness_workflow.yml").read_text()
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "super-harness.yml").write_text(bundled)

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    out = r.output
    assert "kept existing .github/workflows/super-harness.yml" in out, out
    assert "wrote .github/workflows/super-harness.yml" not in out


def test_setup_github_advisory_says_declined_when_user_declines_workflow(tmp_path: Path):
    """User declines the workflow overwrite confirm → advisory says
    'kept existing .github/workflows/super-harness.yml (declined overwrite)'."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "super-harness.yml").write_text("old workflow content\n")

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
            input="n\n",  # decline the confirm
        )
    assert r.exit_code == 0, r.output
    out = r.output
    assert "declined" in out.lower(), out


def test_setup_github_advisory_absent_when_settings_fails(tmp_path: Path):
    """When enable_repo_merge_settings raises GhError (non-fatal), the
    SUCCESS advisory 'repo merge settings: enabled ...' must NOT fire — the
    existing advisory on stderr (from the non-fatal degrade) is enough."""
    with (
        patch("super_harness.cli.init.check_gh"),
        patch(
            "super_harness.cli.init.enable_repo_merge_settings",
            side_effect=GhError("non-admin"),
        ),
    ):
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init", "--setup-github"])
    assert r.exit_code == 0, r.output
    out = r.output
    # Stdout success advisory NOT fired on failure path.
    assert "repo merge settings: enabled" not in out
    # But the other substep advisories DID fire (they succeeded).
    assert "wrote .github/pull_request_template.md" in out
    assert "wrote .github/workflows/super-harness.yml" in out


def test_setup_github_existing_workflow_tty_ctrl_c_exits_1(tmp_path: Path):
    """Existing workflow file + interactive Ctrl-C (TTY=True + Abort) → exit 1.

    Simulates the user pressing Ctrl-C at the overwrite prompt. The isatty()
    discriminator distinguishes this from the non-TTY EOF case (which would
    leave the file untouched + exit 0); TTY Ctrl-C re-raises Abort → Click
    converts to exit 1.

    CliRunner replaces sys.stdin during invoke (its isolation context sets
    sys.stdin = _NamedTextIOWrapper). We therefore inject the isatty=True
    patch via a side_effect on click.confirm: the side_effect fires INSIDE
    the CliRunner context (sys.stdin is already the runner's stdin object),
    patches .isatty on that live object, then raises Abort — so the
    isatty() check in the except-Abort handler sees True and re-raises.
    """
    import sys as _sys

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text("old workflow content\n")

    def _abort_as_tty(*args: object, **kwargs: object) -> None:
        # Patch isatty on the live stdin already installed by CliRunner's
        # isolation context, then raise Abort to simulate Ctrl-C.
        # Note: cleanup of this isatty mutation relies on CliRunner restoring
        # sys.stdin on context exit (it replaces the stdin object wholesale).
        # We do NOT restore .isatty here because the stdin object itself becomes
        # unreferenced after CliRunner cleanup. If a future Click version stops
        # fully replacing stdin (only patching some attributes), this test will
        # need an explicit teardown — currently empirically safe.
        _sys.stdin.isatty = lambda: True  # type: ignore[method-assign]
        raise click.Abort()

    with (
        patch("super_harness.cli.init.check_gh"),
        patch("super_harness.cli.init.enable_repo_merge_settings"),
        patch("super_harness.cli.init.click.confirm", side_effect=_abort_as_tty),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
        )
    assert r.exit_code == 1, r.output
    # Click formats the abort with "Aborted!" on stderr; assert strictly so a
    # regression in Click's abort rendering would actually fail this test.
    assert "Abort" in (r.output + r.stderr), (r.output, r.stderr)
    assert not (tmp_path / ".harness").exists()


# --------------------------------------------------------------------------- #
# Pre-apply GitHub inspection and resolved-decision service (Task 6)
# --------------------------------------------------------------------------- #


def _github_bundles() -> tuple[bytes, bytes]:
    from importlib.resources import files

    templates = files("super_harness.templates")
    return (
        templates.joinpath("pull_request_template.md").read_bytes(),
        templates.joinpath("super_harness_workflow.yml").read_bytes(),
    )


def test_inspect_github_files_is_read_only_and_resolves_stable_states(
    tmp_path: Path,
) -> None:
    from super_harness.cli.init_github import (
        GithubExistingState,
        inspect_github_files,
    )
    from super_harness.cli.init_plan import GithubFileDecision

    bundled_pr, bundled_workflow = _github_bundles()

    fresh = inspect_github_files(tmp_path, bundled_pr, bundled_workflow)
    assert not (tmp_path / ".github").exists()
    assert fresh.pr_template.state is GithubExistingState.MISSING
    assert fresh.pr_template.decision is GithubFileDecision.CREATE
    assert fresh.workflow.state is GithubExistingState.MISSING
    assert fresh.workflow.decision is GithubFileDecision.CREATE

    _template_path(tmp_path).parent.mkdir(parents=True)
    _template_path(tmp_path).write_bytes(bundled_pr)
    _workflow_path(tmp_path).parent.mkdir(parents=True)
    _workflow_path(tmp_path).write_bytes(bundled_workflow)
    identical = inspect_github_files(tmp_path, bundled_pr, bundled_workflow)
    assert identical.pr_template.state is GithubExistingState.IDENTICAL
    assert identical.pr_template.decision is GithubFileDecision.KEEP
    assert identical.workflow.state is GithubExistingState.IDENTICAL
    assert identical.workflow.decision is GithubFileDecision.KEEP

    custom_one_block = (
        b"custom\n<!-- super-harness:metadata -->\n<!-- /super-harness:metadata -->\n"
    )
    _template_path(tmp_path).write_bytes(custom_one_block)
    configured = inspect_github_files(tmp_path, bundled_pr, bundled_workflow)
    assert configured.pr_template.state is GithubExistingState.CONFIGURED
    assert configured.pr_template.decision is GithubFileDecision.KEEP


def test_inspect_github_files_rejects_duplicate_blocks_before_apply(
    tmp_path: Path,
) -> None:
    from super_harness.cli.init_github import GithubFileError, inspect_github_files

    bundled_pr, bundled_workflow = _github_bundles()
    _template_path(tmp_path).parent.mkdir(parents=True)
    _template_path(tmp_path).write_text(
        "<!-- super-harness:metadata -->\n<!-- /super-harness:metadata -->\n"
        "<!-- super-harness:metadata -->\n<!-- /super-harness:metadata -->\n"
    )

    with pytest.raises(GithubFileError, match="2 super-harness metadata blocks"):
        inspect_github_files(tmp_path, bundled_pr, bundled_workflow)


@pytest.mark.parametrize("relative", ["template", "workflow"])
def test_inspect_github_files_rejects_non_utf8_existing_files(
    tmp_path: Path, relative: str
) -> None:
    from super_harness.cli.init_github import GithubFileError, inspect_github_files

    bundled_pr, bundled_workflow = _github_bundles()
    path = _template_path(tmp_path) if relative == "template" else _workflow_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(GithubFileError, match="could not read existing"):
        inspect_github_files(tmp_path, bundled_pr, bundled_workflow)


@pytest.mark.parametrize(
    ("template_decision", "expected_template"),
    [
        (
            "append",
            b"custom\n\n<!-- super-harness:metadata -->\n<!-- /super-harness:metadata -->\n",
        ),
        ("overwrite", None),
        ("keep", b"custom\n"),
    ],
)
def test_apply_github_plan_consumes_resolved_template_decisions(
    tmp_path: Path,
    template_decision: str,
    expected_template: bytes | None,
) -> None:
    from super_harness.cli.init_github import (
        apply_github_plan,
        inspect_github_files,
        resolve_github_plan,
    )
    from super_harness.cli.init_plan import GithubFileDecision

    bundled_pr, bundled_workflow = _github_bundles()
    _template_path(tmp_path).parent.mkdir(parents=True)
    _template_path(tmp_path).write_bytes(b"custom\n")
    inspection = inspect_github_files(tmp_path, bundled_pr, bundled_workflow)
    plan = resolve_github_plan(
        inspection,
        {
            ".github/pull_request_template.md": GithubFileDecision(template_decision),
        },
    )

    outcomes = apply_github_plan(plan)

    assert _template_path(tmp_path).read_bytes() == (
        bundled_pr if expected_template is None else expected_template
    )
    assert _workflow_path(tmp_path).read_bytes() == bundled_workflow
    assert outcomes.pr_template == ("kept-existing" if template_decision == "keep" else "wrote")


def test_append_rejects_pr_template_changed_after_inspection(tmp_path: Path) -> None:
    from super_harness.cli.init_github import (
        GithubFileError,
        apply_github_file,
        inspect_github_files,
        resolve_github_plan,
    )
    from super_harness.cli.init_plan import GithubFileDecision

    bundled_pr, bundled_workflow = _github_bundles()
    template = _template_path(tmp_path)
    template.parent.mkdir(parents=True)
    template.write_bytes(b"inspected\n")
    inspection = inspect_github_files(tmp_path, bundled_pr, bundled_workflow)
    plan = resolve_github_plan(
        inspection,
        {".github/pull_request_template.md": GithubFileDecision.APPEND},
    )
    template.write_bytes(b"concurrent edit\n")

    with pytest.raises(GithubFileError, match="changed after inspection"):
        apply_github_file(plan.pr_template)

    assert template.read_bytes() == b"concurrent edit\n"


def test_apply_github_plan_never_calls_any_prompt_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import questionary

    from super_harness.cli.init_github import (
        apply_github_plan,
        inspect_github_files,
        resolve_github_plan,
    )

    bundled_pr, bundled_workflow = _github_bundles()
    plan = resolve_github_plan(inspect_github_files(tmp_path, bundled_pr, bundled_workflow), {})

    def fail(*_args: object, **_kwargs: object) -> None:
        pytest.fail("apply prompted")

    monkeypatch.setattr("builtins.input", fail)
    monkeypatch.setattr(click, "confirm", fail)
    monkeypatch.setattr(questionary, "prompt", fail)

    apply_github_plan(plan)

    assert _template_path(tmp_path).read_bytes() == bundled_pr
    assert _workflow_path(tmp_path).read_bytes() == bundled_workflow
