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


def _workflow_path(root: Path) -> Path:
    return root / ".github" / "workflows" / "super-harness.yml"


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


# --------------------------------------------------------------------------- #
# (g) workflow file deployment (Task 14.2)
# --------------------------------------------------------------------------- #


def test_setup_github_writes_workflow_file(tmp_path: Path):
    """--setup-github writes BOTH .github/ files (PR template + workflow)."""
    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )

    assert r.exit_code == 0, r.output
    assert _template_path(tmp_path).exists()
    assert _workflow_path(tmp_path).exists()
    wf_text = _workflow_path(tmp_path).read_text()
    assert len(wf_text) > 0


def test_setup_github_workflow_matches_bundled_verbatim(tmp_path: Path):
    """Written workflow file is byte-for-byte the bundled super_harness_workflow.yml."""
    from importlib.resources import files

    bundled = (
        files("super_harness.templates")
        .joinpath("super_harness_workflow.yml")
        .read_text()
    )

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )
    assert r.exit_code == 0, r.output
    assert _workflow_path(tmp_path).read_text() == bundled


def test_setup_github_existing_workflow_with_quiet_overwrites(tmp_path: Path):
    """Existing workflow file + --quiet → overwrites without prompt."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text("old workflow content\n")

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
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

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ) as mock_settings:                                # ← name it
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
            input="n\n",  # decline the confirm
        )
    assert r.exit_code == 0, r.output
    assert wf.read_text() == before
    assert mock_settings.called, (                     # ← prove init reached Step 3
        "init must traverse Step 2.5 (_write_workflow_file) to reach Step 3 "
        "(enable_repo_merge_settings); a vacuous test would pass even if "
        "_write_workflow_file is removed"
    )
    # Non-vacuity guard: the overwrite prompt from _write_workflow_file must appear
    # in output. If _write_workflow_file were removed, this prompt would be absent
    # and this assertion would fail — proving the test is not vacuous.
    assert "Overwrite existing" in r.output and "super-harness.yml" in r.output, (
        "_write_workflow_file must have fired the overwrite prompt; "
        "if absent, the test is vacuous"
    )


def test_setup_github_existing_workflow_noninteractive_no_quiet_is_nonfatal(tmp_path: Path):
    """Existing workflow + non-interactive (no input) + no --quiet → leave untouched, exit 0."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text("old workflow content\n")
    before = wf.read_text()

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
    assert wf.read_text() == before
    assert "non-interactive" in r.stderr.lower() or "skipped" in r.stderr.lower()


def test_setup_github_non_utf8_existing_workflow_friendly_error(tmp_path: Path):
    """Non-UTF-8 existing workflow file → exit 1 with friendly error, no traceback."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    _workflow_path(tmp_path).write_bytes(b"\xff\xfe not utf-8 \x80\x81")

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


def test_setup_github_existing_workflow_identical_is_noop(tmp_path: Path):
    """Existing workflow that's byte-identical to bundled → no prompt, no-op exit 0."""
    from importlib.resources import files

    import super_harness.cli.init as _init_mod

    bundled = (
        files("super_harness.templates")
        .joinpath("super_harness_workflow.yml")
        .read_text()
    )
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = _workflow_path(tmp_path)
    wf.write_text(bundled)

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ) as mock_settings, patch(                        # ← spy: wraps= keeps real behavior
        "super_harness.cli.init._write_workflow_file",
        wraps=_init_mod._write_workflow_file,
    ) as mock_write_wf:
        # No input= — if a prompt fires this will fail (non-interactive Abort → nonfatal)
        # but we want to assert NO prompt fires at all (byte-identical is silent no-op).
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "init", "--setup-github"]
        )
    assert r.exit_code == 0, r.output + r.stderr
    assert wf.read_text() == bundled
    assert mock_settings.called, (                     # ← prove init reached Step 3
        "init must traverse Step 2.5 (_write_workflow_file) to reach Step 3 "
        "(enable_repo_merge_settings); a vacuous test would pass even if "
        "_write_workflow_file is removed"
    )
    # Non-vacuity guard: the spy on _write_workflow_file must have been called.
    # A vacuous test (with _write_workflow_file removed) would fail here.
    assert mock_write_wf.called, (
        "_write_workflow_file was never invoked — the test is vacuous"
    )


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
        _sys.stdin.isatty = lambda: True  # type: ignore[method-assign]
        raise click.Abort()

    with patch("super_harness.cli.init.check_gh"), patch(
        "super_harness.cli.init.enable_repo_merge_settings"
    ), patch("super_harness.cli.init.click.confirm", side_effect=_abort_as_tty):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "init", "--setup-github"],
        )
    assert r.exit_code == 1, r.output
    # Click formats Abort as "Aborted!" on stderr; either suffices as evidence
    assert "Abort" in (r.output + r.stderr) or r.exit_code == 1
