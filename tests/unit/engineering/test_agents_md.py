"""Tests for the AGENTS.md inject/remove primitives (engineering integration).

Mirrors the algorithm in engineering-integration spec §3.2 (`inject_section` /
`inject_framework_subsection`) plus the deliberate 4-branch refinement for
`inject_agent_subsection` and the symmetric `remove_subsection`. All tests use
real file I/O via `tmp_path` (no mocking), per the project's adapter-test idiom.

Contract under test: `content` passed to the subsection injectors is the FULL
marker-wrapped block (as returned by an adapter's `agents_md_subsection()`); the
`framework`/`agent` name arg is used only to locate the existing block / pick the
placeholder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.engineering.agents_md import (
    AgentsMdInjectionError,
    inject_agent_subsection,
    inject_framework_subsection,
    inject_section,
    remove_subsection,
)

# A minimal init-style outer section seed, with both placeholders present, as
# `super-harness init` would first write it (before consuming the placeholders).
_SEED_WITH_PLACEHOLDERS = (
    "<!-- super-harness section begin · v0.1.0 · DO NOT EDIT MANUALLY -->\n"
    "## Super-harness conventions\n\n"
    "### PR creation\n\n"
    "[FRAMEWORK_SECTION_AUTO_INSERTED]\n\n"
    "### Agent-specific guidance\n\n"
    "[AGENT_SECTION_AUTO_INSERTED]\n\n"
    "<!-- super-harness section end -->\n"
)


def _fw_block(name: str, body: str = "fw body") -> str:
    return (
        f"<!-- super-harness framework: {name} -->\n"
        f"{body}\n"
        f"<!-- /super-harness framework: {name} -->\n"
    )


def _agent_block(name: str, body: str = "agent body") -> str:
    return (
        f"<!-- super-harness agent: {name} -->\n"
        f"{body}\n"
        f"<!-- /super-harness agent: {name} -->\n"
    )


_NO_AGENT = "<!-- super-harness no-agent-adapter-installed -->"


# --------------------------------------------------------------------------- #
# inject_section
# --------------------------------------------------------------------------- #


def test_inject_section_absent_file_writes_content(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    inject_section(path, "SECTION-A")
    assert path.read_text() == "SECTION-A\n"


def test_inject_section_no_existing_block_appends(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# My project\n\nUser prose.\n")
    inject_section(path, "SECTION-A")
    text = path.read_text()
    # User content preserved verbatim, section appended after a blank line.
    assert text == "# My project\n\nUser prose.\n\nSECTION-A\n"


def test_inject_section_single_block_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    old = (
        "<!-- super-harness section begin · v0.0.1 -->\n"
        "old body\n"
        "<!-- super-harness section end -->"
    )
    path.write_text(f"# Top\n\n{old}\n\nUser tail.\n")
    new = (
        "<!-- super-harness section begin · v0.1.0 -->\n"
        "new body\n"
        "<!-- super-harness section end -->"
    )
    inject_section(path, new)
    text = path.read_text()
    assert "old body" not in text
    assert "new body" in text
    # Surrounding user content untouched.
    assert text.startswith("# Top\n\n")
    assert text.endswith("User tail.\n")


def test_inject_section_multiple_blocks_raises(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    block = (
        "<!-- super-harness section begin · v0.1.0 -->\n"
        "body\n"
        "<!-- super-harness section end -->"
    )
    path.write_text(f"{block}\n\n{block}\n")
    with pytest.raises(AgentsMdInjectionError):
        inject_section(path, "whatever")
    # File left untouched on raise (still 2 blocks).
    assert path.read_text().count("super-harness section begin") == 2


# --------------------------------------------------------------------------- #
# inject_framework_subsection
# --------------------------------------------------------------------------- #


def test_inject_framework_placeholder_branch(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_SEED_WITH_PLACEHOLDERS)
    inject_framework_subsection(path, "plain", _fw_block("plain"))
    text = path.read_text()
    assert "[FRAMEWORK_SECTION_AUTO_INSERTED]" not in text
    assert _fw_block("plain") in text
    # Agent placeholder untouched.
    assert "[AGENT_SECTION_AUTO_INSERTED]" in text


def test_inject_framework_replace_by_name(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_SEED_WITH_PLACEHOLDERS)
    inject_framework_subsection(path, "plain", _fw_block("plain", "v1"))
    inject_framework_subsection(path, "plain", _fw_block("plain", "v2"))
    text = path.read_text()
    assert "v1" not in text
    assert "v2" in text
    # Exactly one plain block (replaced, not duplicated).
    assert text.count("<!-- super-harness framework: plain -->") == 1


def test_inject_framework_append_after_last(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_SEED_WITH_PLACEHOLDERS)
    inject_framework_subsection(path, "plain", _fw_block("plain"))
    # Placeholder gone; a *different* framework must append after the last block.
    inject_framework_subsection(path, "openspec", _fw_block("openspec"))
    text = path.read_text()
    assert text.count("<!-- super-harness framework: plain -->") == 1
    assert text.count("<!-- super-harness framework: openspec -->") == 1
    # openspec appended AFTER plain.
    assert text.index("framework: plain") < text.index("framework: openspec")


# --------------------------------------------------------------------------- #
# inject_agent_subsection (4 branches)
# --------------------------------------------------------------------------- #


def test_inject_agent_placeholder_branch(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_SEED_WITH_PLACEHOLDERS)
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))
    text = path.read_text()
    assert "[AGENT_SECTION_AUTO_INSERTED]" not in text
    assert _agent_block("claude-code") in text


def test_inject_agent_first_install_replaces_no_agent_placeholder(tmp_path: Path) -> None:
    """Branch (2): after init consumes the placeholder into the no-agent marker,
    the FIRST real agent install must anchor on that marker (else silent no-op)."""
    path = tmp_path / "AGENTS.md"
    # Simulate post-init state: agent placeholder already turned into no-agent.
    seed = _SEED_WITH_PLACEHOLDERS.replace("[AGENT_SECTION_AUTO_INSERTED]", _NO_AGENT)
    path.write_text(seed)
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))
    text = path.read_text()
    assert _NO_AGENT not in text
    assert _agent_block("claude-code") in text
    assert "[AGENT_SECTION_AUTO_INSERTED]" not in text


def test_inject_agent_replace_by_name(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    seed = _SEED_WITH_PLACEHOLDERS.replace("[AGENT_SECTION_AUTO_INSERTED]", _NO_AGENT)
    path.write_text(seed)
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code", "v1"))
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code", "v2"))
    text = path.read_text()
    assert "v1" not in text
    assert "v2" in text
    assert text.count("<!-- super-harness agent: claude-code -->") == 1


def test_inject_agent_append_after_last(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    seed = _SEED_WITH_PLACEHOLDERS.replace("[AGENT_SECTION_AUTO_INSERTED]", _NO_AGENT)
    path.write_text(seed)
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))
    inject_agent_subsection(path, "cursor", _agent_block("cursor"))
    text = path.read_text()
    assert text.count("<!-- super-harness agent: claude-code -->") == 1
    assert text.count("<!-- super-harness agent: cursor -->") == 1
    assert text.index("agent: claude-code") < text.index("agent: cursor")


# --------------------------------------------------------------------------- #
# remove_subsection
# --------------------------------------------------------------------------- #


def test_remove_framework_removes_named_block_preserves_others(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    body = (
        "# Top\n\n"
        + _fw_block("plain")
        + "\n"
        + _fw_block("openspec")
        + "\nUser tail.\n"
    )
    path.write_text(body)
    remove_subsection(path, "framework", "plain")
    text = path.read_text()
    assert "framework: plain" not in text
    assert "framework: openspec" in text
    assert text.startswith("# Top\n")
    assert text.endswith("User tail.\n")
    # No double blank line left where plain was.
    assert "\n\n\n" not in text


def test_remove_framework_no_placeholder_restored(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# Top\n\n" + _fw_block("plain"))
    remove_subsection(path, "framework", "plain")
    text = path.read_text()
    assert "framework: plain" not in text
    # Framework NEVER restores any placeholder.
    assert "[FRAMEWORK_SECTION_AUTO_INSERTED]" not in text
    assert _NO_AGENT not in text


def test_remove_last_agent_restores_no_agent_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# Top\n\n" + _agent_block("claude-code") + "\nUser tail.\n")
    remove_subsection(path, "agent", "claude-code")
    text = path.read_text()
    assert "agent: claude-code" not in text
    # Last agent removed -> no-agent placeholder restored as the anchor.
    assert _NO_AGENT in text
    assert text.startswith("# Top\n")
    assert text.endswith("User tail.\n")


def test_remove_agent_with_remaining_does_not_restore_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# Top\n\n" + _agent_block("claude-code") + "\n" + _agent_block("cursor"))
    remove_subsection(path, "agent", "claude-code")
    text = path.read_text()
    assert "agent: claude-code" not in text
    assert "agent: cursor" in text
    # Agents still remain -> no placeholder.
    assert _NO_AGENT not in text


def test_remove_absent_file_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    remove_subsection(path, "framework", "plain")
    assert not path.exists()


def test_remove_absent_block_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    original = "# Top\n\n" + _fw_block("openspec")
    path.write_text(original)
    remove_subsection(path, "framework", "plain")
    assert path.read_text() == original


# --------------------------------------------------------------------------- #
# CRLF preservation
# --------------------------------------------------------------------------- #


def test_crlf_file_injection_preserves_user_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    seed = _SEED_WITH_PLACEHOLDERS.replace("[AGENT_SECTION_AUTO_INSERTED]", _NO_AGENT)
    # Write the whole file with CRLF line endings (Windows-authored AGENTS.md).
    crlf_seed = seed.replace("\n", "\r\n")
    path.write_bytes(crlf_seed.encode("utf-8"))

    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))

    raw = path.read_bytes()
    # (a) injection worked.
    assert b"super-harness agent: claude-code" in raw
    assert b"no-agent-adapter-installed" not in raw
    # (b) untouched user lines keep CRLF; no bare LF leaked in.
    assert b"\r\n" in raw
    assert b"\n" in raw
    # Every LF is part of a CRLF pair (no lone \n introduced anywhere).
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_lf_file_stays_lf(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    seed = _SEED_WITH_PLACEHOLDERS.replace("[AGENT_SECTION_AUTO_INSERTED]", _NO_AGENT)
    path.write_bytes(seed.encode("utf-8"))
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert b"super-harness agent: claude-code" in raw


# --------------------------------------------------------------------------- #
# End-to-end round trip
# --------------------------------------------------------------------------- #


def test_roundtrip_inject_remove_reinject_agent(tmp_path: Path) -> None:
    """init-seed -> inject agent A -> remove A -> inject A again leaves A."""
    path = tmp_path / "AGENTS.md"
    # init-like seed: placeholder already consumed into the no-agent marker.
    seed = _SEED_WITH_PLACEHOLDERS.replace("[AGENT_SECTION_AUTO_INSERTED]", _NO_AGENT)
    path.write_text(seed)

    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))
    assert "agent: claude-code" in path.read_text()

    remove_subsection(path, "agent", "claude-code")
    text = path.read_text()
    assert "agent: claude-code" not in text
    assert _NO_AGENT in text  # anchor restored

    # Re-install must succeed (branch (2) anchors on the restored placeholder).
    inject_agent_subsection(path, "claude-code", _agent_block("claude-code"))
    final = path.read_text()
    assert final.count("<!-- super-harness agent: claude-code -->") == 1
    assert _NO_AGENT not in final
