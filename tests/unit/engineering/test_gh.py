"""Tests for the gh CLI subprocess wrapper (engineering-integration spec §3.1).

All subprocess calls are mocked — never invoke real `gh` or touch the network.
Uses stdlib unittest.mock only (no pytest-mock dep).
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from super_harness.engineering.gh import (
    MIN_VERSION,
    GhError,
    GhNotAuthenticated,
    GhNotInstalled,
    GhVersionTooOld,
    _parse_version,
    check_gh,
    create_pr,
    edit_pr_body,
    enable_repo_merge_settings,
    merge_pr_auto_squash,
    view_pr,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Return a fake CompletedProcess-like object."""
    cp = MagicMock()
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


def _cpe(returncode: int = 1) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(returncode, ["gh"])


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_normal(self) -> None:
        assert _parse_version("gh version 2.45.0 (2026-04-01)\n") == (2, 45)

    def test_normal_no_trailing_newline(self) -> None:
        assert _parse_version("gh version 2.40.0 (2025-01-01)") == (2, 40)

    def test_min_version_exact(self) -> None:
        assert _parse_version("gh version 2.40.0 (date)") == MIN_VERSION

    def test_high_minor(self) -> None:
        assert _parse_version("gh version 3.0.0 (date)") == (3, 0)

    def test_malformed_empty_raises_too_old(self) -> None:
        with pytest.raises(GhVersionTooOld):
            _parse_version("")

    def test_malformed_no_version_keyword_raises_too_old(self) -> None:
        with pytest.raises(GhVersionTooOld):
            _parse_version("not a version line at all")

    def test_malformed_partial_raises_too_old(self) -> None:
        with pytest.raises(GhVersionTooOld):
            _parse_version("gh version abc.def.ghi (date)")


# ---------------------------------------------------------------------------
# check_gh
# ---------------------------------------------------------------------------


class TestCheckGh:
    def test_not_installed_raises_gh_not_installed(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("no such file")
            with pytest.raises(GhNotInstalled) as exc_info:
                check_gh()
            assert "brew install gh" in str(exc_info.value) or "install" in str(
                exc_info.value
            ).lower()
        # Only the first call (--version) should have been attempted
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["gh", "--version"]

    def test_version_too_old_raises_gh_version_too_old(self) -> None:
        old_version_output = "gh version 2.39.0 (2024-01-01)\n"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout=old_version_output)
            with pytest.raises(GhVersionTooOld) as exc_info:
                check_gh()
            assert "2.40" in str(exc_info.value) or "2, 40" in str(exc_info.value)

    def test_malformed_version_output_raises_gh_version_too_old(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="garbage output\n")
            with pytest.raises(GhVersionTooOld):
                check_gh()

    def test_not_authenticated_raises_gh_not_authenticated(self) -> None:
        good_version = "gh version 2.45.0 (2026-04-01)\n"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            # First call (--version) succeeds; second call (auth status) fails.
            mock_run.side_effect = [
                _completed(stdout=good_version),
                _cpe(returncode=1),
            ]
            with pytest.raises(GhNotAuthenticated) as exc_info:
                check_gh()
            msg = str(exc_info.value)
            assert "gh auth login" in msg or "auth" in msg.lower()

    def test_happy_path_no_raise(self) -> None:
        good_version = "gh version 2.45.0 (2026-04-01)\n"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _completed(stdout=good_version),
                _completed(),  # auth status success
            ]
            # Should not raise
            check_gh()
        # Assert exact argv for both calls
        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["gh", "--version"]
        assert calls[1][0][0] == ["gh", "auth", "status"]

    def test_gh_not_installed_is_gh_error_subclass(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(GhError):
                check_gh()

    def test_gh_version_too_old_is_gh_error_subclass(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="gh version 1.0.0 (date)\n")
            with pytest.raises(GhError):
                check_gh()

    def test_gh_not_authenticated_is_gh_error_subclass(self) -> None:
        good_version = "gh version 2.45.0 (2026-04-01)\n"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = [_completed(stdout=good_version), _cpe()]
            with pytest.raises(GhError):
                check_gh()


# ---------------------------------------------------------------------------
# view_pr
# ---------------------------------------------------------------------------


class TestViewPr:
    def test_correct_argv_and_json_parse(self) -> None:
        payload = {"number": 42, "title": "My PR", "state": "OPEN"}
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout=json.dumps(payload))
            result = view_pr(42, fields=["number", "title", "state"])
        assert result == payload
        mock_run.assert_called_once()
        argv = mock_run.call_args[0][0]
        assert argv == ["gh", "pr", "view", "42", "--json", "number,title,state"]

    def test_not_found_called_process_error_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = _cpe(returncode=1)
            with pytest.raises(GhError):
                view_pr(999, fields=["number"])

    def test_file_not_found_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(GhError):
                view_pr(1, fields=["number"])

    def test_bad_json_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="not-json-at-all")
            with pytest.raises(GhError):
                view_pr(1, fields=["number"])

    def test_single_field_argv(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout='{"number": 1}')
            view_pr(1, fields=["number"])
        argv = mock_run.call_args[0][0]
        # --json value should be the field name, not comma-joined with nothing
        assert argv[5] == "number"


# ---------------------------------------------------------------------------
# edit_pr_body
# ---------------------------------------------------------------------------


class TestEditPrBody:
    def test_correct_argv_with_body_file(self, tmp_path: object) -> None:
        captured_argv: list[list[str]] = []

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            captured_argv.append(args)
            return _completed()

        with patch("super_harness.engineering.gh.subprocess.run", side_effect=fake_run):
            edit_pr_body(7, body="## Test\n\nHello world")

        assert len(captured_argv) == 1
        argv = captured_argv[0]
        assert argv[:4] == ["gh", "pr", "edit", "7"]
        assert "--body-file" in argv
        body_file_idx = argv.index("--body-file")
        path = argv[body_file_idx + 1]
        # The temp file should have been unlinked after the call
        import os

        assert not os.path.exists(path)

    def test_body_written_to_temp_file(self) -> None:
        body_content = "## PR Body\n\nSome content here."
        written_content: list[str] = []

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            # Read the file before it gets deleted
            body_file_idx = args.index("--body-file")
            path = args[body_file_idx + 1]
            with open(path) as f:
                written_content.append(f.read())
            return _completed()

        with patch("super_harness.engineering.gh.subprocess.run", side_effect=fake_run):
            edit_pr_body(7, body=body_content)

        assert written_content == [body_content]

    def test_temp_file_unlinked_even_when_subprocess_raises(self) -> None:
        captured_path: list[str] = []

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            body_file_idx = args.index("--body-file")
            captured_path.append(args[body_file_idx + 1])
            raise subprocess.CalledProcessError(1, args)

        with patch("super_harness.engineering.gh.subprocess.run", side_effect=fake_run):
            with pytest.raises(GhError):
                edit_pr_body(7, body="body")

        import os

        assert captured_path, "subprocess was never called"
        assert not os.path.exists(captured_path[0]), "temp file was not cleaned up"

    def test_called_process_error_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = _cpe()
            with pytest.raises(GhError):
                edit_pr_body(7, body="body")

    def test_file_not_found_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(GhError):
                edit_pr_body(7, body="body")


# ---------------------------------------------------------------------------
# create_pr
# ---------------------------------------------------------------------------


class TestCreatePr:
    def test_minimal_argv_no_labels_no_draft(self) -> None:
        url = "https://github.com/owner/repo/pull/1"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout=url + "\n")
            result = create_pr(base="main", head="feature-branch", title="My PR", body="body")
        assert result == url
        argv = mock_run.call_args[0][0]
        assert argv == [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            "feature-branch",
            "--title",
            "My PR",
            "--body",
            "body",
        ]
        assert "--draft" not in argv
        assert "--label" not in argv

    def test_with_labels_and_draft(self) -> None:
        url = "https://github.com/owner/repo/pull/2"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout=url + "\n")
            result = create_pr(
                base="main",
                head="feature-x",
                title="Draft PR",
                body="wip",
                labels=["harness-auto", "no-human-review"],
                draft=True,
            )
        assert result == url
        argv = mock_run.call_args[0][0]
        assert "--draft" in argv
        # Each label has its own --label flag
        label_indices = [i for i, a in enumerate(argv) if a == "--label"]
        assert len(label_indices) == 2
        labels_found = [argv[i + 1] for i in label_indices]
        assert set(labels_found) == {"harness-auto", "no-human-review"}

    def test_with_single_label_no_draft(self) -> None:
        url = "https://github.com/owner/repo/pull/3"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout=url + "\n")
            result = create_pr(
                base="main",
                head="feat",
                title="T",
                body="b",
                labels=["harness-auto"],
                draft=False,
            )
        assert result == url
        argv = mock_run.call_args[0][0]
        assert "--draft" not in argv
        assert argv.count("--label") == 1
        idx = argv.index("--label")
        assert argv[idx + 1] == "harness-auto"

    def test_returns_stripped_url(self) -> None:
        url = "https://github.com/owner/repo/pull/42"
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="  " + url + "  \n")
            result = create_pr(base="main", head="h", title="t", body="b")
        assert result == url

    def test_called_process_error_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = _cpe()
            with pytest.raises(GhError):
                create_pr(base="main", head="h", title="t", body="b")

    def test_file_not_found_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(GhError):
                create_pr(base="main", head="h", title="t", body="b")


# ---------------------------------------------------------------------------
# merge_pr_auto_squash
# ---------------------------------------------------------------------------


class TestMergePrAutoSquash:
    def test_argv_with_delete_branch(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed()
            merge_pr_auto_squash(10, delete_branch=True)
        argv = mock_run.call_args[0][0]
        assert argv == ["gh", "pr", "merge", "10", "--auto", "--squash", "--delete-branch"]

    def test_argv_without_delete_branch(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed()
            merge_pr_auto_squash(10, delete_branch=False)
        argv = mock_run.call_args[0][0]
        assert argv == ["gh", "pr", "merge", "10", "--auto", "--squash"]
        assert "--delete-branch" not in argv

    def test_default_delete_branch_is_true(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed()
            merge_pr_auto_squash(5)
        argv = mock_run.call_args[0][0]
        assert "--delete-branch" in argv

    def test_called_process_error_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = _cpe()
            with pytest.raises(GhError):
                merge_pr_auto_squash(1)

    def test_file_not_found_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(GhError):
                merge_pr_auto_squash(1)


# ---------------------------------------------------------------------------
# enable_repo_merge_settings (§3.1 repo-settings auto-enable)
# ---------------------------------------------------------------------------


class TestEnableRepoMergeSettings:
    def test_runs_both_patch_calls_with_gh_placeholders(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed()
            enable_repo_merge_settings()
        # two PATCH calls — auto-merge then squash — with gh's own {owner}/{repo}
        # placeholders passed literally (gh resolves them from the git remote).
        argvs = [call.args[0] for call in mock_run.call_args_list]
        assert len(argvs) == 2, argvs
        assert argvs[0] == [
            "gh", "api", "-X", "PATCH", "/repos/{owner}/{repo}",
            "-f", "allow_auto_merge=true",
        ]
        assert argvs[1] == [
            "gh", "api", "-X", "PATCH", "/repos/{owner}/{repo}",
            "-f", "allow_squash_merge=true",
        ]

    def test_called_process_error_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = _cpe()
            with pytest.raises(GhError):
                enable_repo_merge_settings()

    def test_file_not_found_raises_gh_error(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(GhError):
                enable_repo_merge_settings()

    def test_captures_output_so_caller_can_log_stderr(self) -> None:
        with patch("super_harness.engineering.gh.subprocess.run") as mock_run:
            mock_run.return_value = _completed()
            enable_repo_merge_settings()
        # capture_output so a best-effort caller can write stderr to an op-log.
        assert mock_run.call_args.kwargs.get("capture_output") is True
