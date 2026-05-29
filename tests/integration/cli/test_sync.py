"""Integration tests for `super-harness sync` (Phase 9 batch late).

`sync` re-renders the AGENTS.md "super-harness section" (version stamp + every
installed adapter's subsection) WITHOUT re-running `init` (it never touches
`.harness/`) and WITHOUT modifying user content outside the begin/end markers.
It is the re-render-without-reinit path; the render logic itself is shared with
`init` (`engineering.agents_md_render.render_super_harness_section`).

Surface under test (cli-command-surface §sync; eng-integration §2.2 / §3.2):

- ``sync`` / ``sync --agents-md``  — FULL re-render (identical in v0.1).
- ``sync --adapter <name>``        — re-inject ONLY that adapter's subsection
                                     (no outer version bump).
- ``--yes`` / global ``--quiet``   — skip the overwrite-confirm.

Drives the root ``main`` group via Click's ``CliRunner`` (mirroring
``test_init.py`` / ``test_adapter.py``). ``shutil.which`` is monkeypatched for
the agent-install legs so the real ``super-harness-hook`` binary need not be on
PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.version import __version__

_FAKE_HOOK = "/usr/local/bin/super-harness-hook"
_VERSION_STAMP = (
    f"<!-- super-harness section begin · v{__version__} · DO NOT EDIT MANUALLY -->"
)
_CLAUDE_BEGIN = "<!-- super-harness agent: claude-code -->"
_NO_AGENT = "<!-- super-harness no-agent-adapter-installed -->"


def _agents_md(ws: Path) -> Path:
    return ws / "AGENTS.md"


def _init(runner: CliRunner, ws: Path):
    return runner.invoke(main, ["--workspace", str(ws), "init"])


def _install_claude(runner: CliRunner, ws: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    return runner.invoke(
        main, ["--workspace", str(ws), "adapter", "install", "claude-code"]
    )


# --------------------------------------------------------------------------- #
# workspace resolution
# --------------------------------------------------------------------------- #


def test_sync_no_harness_exits_no_config(tmp_path: Path) -> None:
    """`sync` with no `.harness/` → EXIT_NO_CONFIG (3), clear message, no crash."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync"])
    assert r.exit_code == 3, r.output
    assert "Traceback" not in r.stderr
    assert "super-harness sync:" in r.stderr


# --------------------------------------------------------------------------- #
# full re-render (no flags / --agents-md)
# --------------------------------------------------------------------------- #


def test_sync_rerenders_version_stamp_and_preserves_user_content(tmp_path: Path) -> None:
    """Full sync after `init` re-renders the version stamp and byte-preserves the
    user content OUTSIDE the markers; a second sync is idempotent."""
    runner = CliRunner()
    user_tail = "# My project\n\nUser prose that must survive.\n"
    _agents_md(tmp_path).write_text(user_tail)
    assert _init(runner, tmp_path).exit_code == 0

    # The user content is preserved above the appended section after init.
    before = _agents_md(tmp_path).read_text()
    assert before.startswith(user_tail)

    r = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 0, r.output
    after = _agents_md(tmp_path).read_text()
    assert _VERSION_STAMP in after
    # User content outside the markers byte-preserved.
    assert after.startswith(user_tail)
    assert after.count("<!-- super-harness section begin ") == 1

    # Idempotent: a second sync produces byte-identical output.
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r2.exit_code == 0, r2.output
    assert _agents_md(tmp_path).read_text() == after


def test_sync_agents_md_flag_identical_to_no_arg(tmp_path: Path) -> None:
    """`sync --agents-md` is identical to no-arg sync in v0.1."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    plain_after = _agents_md(tmp_path).read_text()

    r = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync", "--agents-md"])
    assert r.exit_code == 0, r.output
    assert _agents_md(tmp_path).read_text() == plain_after


def test_sync_help_agents_md_advertises_v01_noop(tmp_path: Path) -> None:
    """`sync --help` notes the v0.1 adapter-checks no-op caveat on --agents-md."""
    r = CliRunner().invoke(main, ["sync", "--help"])
    assert r.exit_code == 0
    assert "v0.1" in r.output
    assert "no-op" in r.output


# --------------------------------------------------------------------------- #
# overwrite-confirm
# --------------------------------------------------------------------------- #


def test_sync_prompts_and_declining_aborts_without_write(tmp_path: Path) -> None:
    """A section exists → sync prompts; declining (`n`) ABORTS with NO write and
    exit 1 (Click's Abort)."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    before = _agents_md(tmp_path).read_text()

    r = runner.invoke(main, ["--workspace", str(tmp_path), "sync"], input="n\n")
    assert r.exit_code == 1, r.output
    # File unchanged — declining the confirm must not write.
    assert _agents_md(tmp_path).read_text() == before


def test_sync_prompt_accepting_writes(tmp_path: Path) -> None:
    """Accepting the confirm (`y`) proceeds with the re-render."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    r = runner.invoke(main, ["--workspace", str(tmp_path), "sync"], input="y\n")
    assert r.exit_code == 0, r.output
    assert _VERSION_STAMP in _agents_md(tmp_path).read_text()


def test_sync_yes_flag_skips_prompt(tmp_path: Path) -> None:
    """`--yes` skips the overwrite-confirm (no input needed) and writes."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    r = runner.invoke(main, ["--workspace", str(tmp_path), "sync", "--yes"])
    assert r.exit_code == 0, r.output
    assert _VERSION_STAMP in _agents_md(tmp_path).read_text()


def test_sync_quiet_skips_prompt(tmp_path: Path) -> None:
    """Global `--quiet` skips the overwrite-confirm and writes."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    r = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 0, r.output
    assert _VERSION_STAMP in _agents_md(tmp_path).read_text()


# --------------------------------------------------------------------------- #
# AGENTS.md absent / present-without-section (no prompt)
# --------------------------------------------------------------------------- #


def test_sync_absent_agents_md_regenerates_without_prompt(tmp_path: Path) -> None:
    """AGENTS.md absent → full sync regenerates it, NO prompt, exit 0.

    We delete AGENTS.md after init but keep `.harness/` so sync has a workspace.
    No input is provided: if sync wrongly prompted it would hang/abort."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    _agents_md(tmp_path).unlink()
    assert not _agents_md(tmp_path).exists()

    r = runner.invoke(main, ["--workspace", str(tmp_path), "sync"])
    assert r.exit_code == 0, r.output
    text = _agents_md(tmp_path).read_text()
    assert _VERSION_STAMP in text
    # No overwrite prompt was shown (nothing to overwrite).
    assert "overwrite" not in r.output.lower()


def test_sync_present_without_section_appends_no_prompt(tmp_path: Path) -> None:
    """AGENTS.md present with ONLY user content (no super-harness section) → the
    section is appended, user content preserved, NO prompt."""
    runner = CliRunner()
    (tmp_path / ".harness").mkdir()
    user_content = "# My project\n\nGuidance with no markers.\n"
    _agents_md(tmp_path).write_text(user_content)

    r = runner.invoke(main, ["--workspace", str(tmp_path), "sync"])
    assert r.exit_code == 0, r.output
    text = _agents_md(tmp_path).read_text()
    assert text.startswith(user_content)
    assert _VERSION_STAMP in text
    assert "overwrite" not in r.output.lower()


# --------------------------------------------------------------------------- #
# --adapter <name>  (single-subsection re-inject)
# --------------------------------------------------------------------------- #


def test_sync_adapter_reinjects_only_that_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sync --adapter claude-code` re-injects ONLY that adapter's block; the
    outer version stamp is NOT bumped and the rest of the section is untouched;
    idempotent."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    assert _install_claude(runner, tmp_path, monkeypatch).exit_code == 0
    before = _agents_md(tmp_path).read_text()
    assert _CLAUDE_BEGIN in before

    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 0, r.output
    after = _agents_md(tmp_path).read_text()
    # The adapter block re-injects to the SAME content → whole file unchanged
    # (no version bump, only the targeted block touched).
    assert after == before
    assert after.count(_CLAUDE_BEGIN) == 1


def test_sync_adapter_does_not_bump_outer_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter-scoped sync leaves a STALE outer version stamp alone (no bump):
    a doctored begin marker keeps its old version after `sync --adapter`."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    assert _install_claude(runner, tmp_path, monkeypatch).exit_code == 0

    # Doctor the begin marker to a fake older version; adapter sync must NOT
    # rewrite the outer section, so the fake stamp survives.
    path = _agents_md(tmp_path)
    text = path.read_text().replace(f"· v{__version__} ·", "· v0.0.1 ·")
    path.write_text(text)

    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 0, r.output
    after = path.read_text()
    assert "· v0.0.1 ·" in after
    assert _VERSION_STAMP not in after


def test_sync_adapter_unknown_or_not_installed_exits_generic(tmp_path: Path) -> None:
    """`sync --adapter <not-installed>` → exit 1 with a clear message (no crash)."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 1, r.output
    assert "is not installed" in r.stderr
    assert "Traceback" not in r.stderr


def test_sync_adapter_absent_agents_md_exits_ok_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sync --adapter X` when AGENTS.md is absent → exit 0, no file created
    (an adapter-scoped sync never regenerates the whole file)."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    assert _install_claude(runner, tmp_path, monkeypatch).exit_code == 0
    _agents_md(tmp_path).unlink()

    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "sync", "--adapter", "claude-code"],
    )
    assert r.exit_code == 0, r.output
    assert not _agents_md(tmp_path).exists()


def test_sync_adapter_beats_agents_md_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When BOTH --adapter and --agents-md are given, --adapter wins (adapter-only
    scope → no outer version bump)."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    assert _install_claude(runner, tmp_path, monkeypatch).exit_code == 0

    path = _agents_md(tmp_path)
    text = path.read_text().replace(f"· v{__version__} ·", "· v0.0.1 ·")
    path.write_text(text)

    r = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "--quiet",
            "sync",
            "--agents-md",
            "--adapter",
            "claude-code",
        ],
    )
    assert r.exit_code == 0, r.output
    # --adapter won: outer stamp NOT bumped.
    assert "· v0.0.1 ·" in path.read_text()


# --------------------------------------------------------------------------- #
# robustness: duplicate section, .harness/ isolation, CRLF
# --------------------------------------------------------------------------- #


def test_sync_duplicate_section_surfaces_clean_error(tmp_path: Path) -> None:
    """Two super-harness outer blocks → the underlying AgentsMdInjectionError is
    surfaced through the envelope (exit 1, "manual cleanup" message, NO traceback,
    file left untouched). `--yes` skips the prompt so we reach the render."""
    (tmp_path / ".harness").mkdir()
    block = (
        "<!-- super-harness section begin · v0.0.1 · DO NOT EDIT MANUALLY -->\n"
        "old\n"
        "<!-- super-harness section end -->\n"
    )
    doubled = block + "\n" + block
    _agents_md(tmp_path).write_text(doubled)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--yes"])
    assert r.exit_code == 1, r.output
    assert "manual cleanup" in r.stderr
    assert "Traceback" not in r.stderr
    # inject_section raises before writing → file unchanged.
    assert _agents_md(tmp_path).read_text() == doubled


def test_sync_does_not_touch_harness_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync re-renders AGENTS.md only — every file under `.harness/` is byte-identical
    before and after (the re-render-without-reinit guarantee vs `init --force`)."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    assert _install_claude(runner, tmp_path, monkeypatch).exit_code == 0

    harness = tmp_path / ".harness"
    before = {p: p.read_bytes() for p in harness.rglob("*") if p.is_file()}

    r = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 0, r.output

    after = {p: p.read_bytes() for p in harness.rglob("*") if p.is_file()}
    assert after == before


def test_sync_orphan_begin_marker_no_data_loss(tmp_path: Path) -> None:
    """An AGENTS.md with an orphan `section begin` (no matching end) → sync fails
    LOUD (exit 1, "unbalanced" message, no traceback) and leaves the file BYTE-
    identical, rather than appending a second section that a later sync would
    splice across — silently eating the trapped user content."""
    (tmp_path / ".harness").mkdir()
    original = (
        "# My project\n\n"
        "<!-- super-harness section begin · v0.0.1 · DO NOT EDIT MANUALLY -->\n"
        "IMPORTANT user notes that must survive\n"
    )
    _agents_md(tmp_path).write_text(original)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--yes"])
    assert r.exit_code == 1, r.output
    assert "unbalanced" in r.stderr
    assert "Traceback" not in r.stderr
    # No data loss: file untouched, user notes intact.
    assert _agents_md(tmp_path).read_text() == original


def test_sync_unreadable_agents_md_surfaces_clean_error(tmp_path: Path) -> None:
    """An UNREADABLE AGENTS.md (a directory) on the PROMPT path (no --quiet/--yes,
    so the section_present read runs before any envelope) surfaces through
    format_error — exit 1, NO raw traceback. Regression for the section_present
    read escaping the try/except."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / "AGENTS.md").mkdir()  # a directory where a file is expected

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "super-harness sync:" in r.stderr


def test_sync_non_utf8_agents_md_surfaces_clean_error(tmp_path: Path) -> None:
    """A non-UTF-8 AGENTS.md surfaces through format_error (exit 1, no traceback,
    file untouched) instead of leaking a raw UnicodeDecodeError — even on the
    --yes write path (UnicodeDecodeError is a ValueError, not an OSError)."""
    (tmp_path / ".harness").mkdir()
    raw = (
        b"<!-- super-harness section begin \xff v0.0.1 \xff DO NOT EDIT MANUALLY -->\n"
        b"x\n<!-- super-harness section end -->\n"
    )
    (tmp_path / "AGENTS.md").write_bytes(raw)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--yes"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "UTF-8" in r.stderr
    assert (tmp_path / "AGENTS.md").read_bytes() == raw  # untouched


def test_sync_preserves_crlf_line_endings(tmp_path: Path) -> None:
    """A CRLF-authored AGENTS.md stays CRLF after sync (no churn of line endings;
    the version stamp is still re-rendered)."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    path = _agents_md(tmp_path)
    path.write_bytes(path.read_text().replace("\n", "\r\n").encode("utf-8"))

    r = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 0, r.output
    raw = path.read_bytes()
    assert b"\r\n" in raw
    # No lone LF was introduced (every LF is part of a CRLF).
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0
    assert _VERSION_STAMP.encode("utf-8") in raw.replace(b"\r\n", b"\n")


# --------------------------------------------------------------------------- #
# full lifecycle
# --------------------------------------------------------------------------- #


def test_sync_full_lifecycle_init_install_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init → adapter install claude-code → sync leaves AGENTS.md with BOTH the
    version stamp and the claude-code agent block, user content preserved."""
    runner = CliRunner()
    user_head = "# Team AGENTS\n\nHouse rules above the harness section.\n"
    _agents_md(tmp_path).write_text(user_head)

    assert _init(runner, tmp_path).exit_code == 0
    assert _install_claude(runner, tmp_path, monkeypatch).exit_code == 0

    r = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r.exit_code == 0, r.output

    text = _agents_md(tmp_path).read_text()
    assert text.startswith(user_head)
    assert _VERSION_STAMP in text
    assert _CLAUDE_BEGIN in text
    assert text.count(_CLAUDE_BEGIN) == 1
    assert _NO_AGENT not in text
    assert "<!-- super-harness framework: plain -->" in text
